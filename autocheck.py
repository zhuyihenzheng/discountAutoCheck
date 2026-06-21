import argparse
import json
import re
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin

import os
import requests

# Selenium / pytz are imported lazily so that offline HTML parsing (used to
# verify the scraper against a saved page without a browser or network) works
# in environments where those packages / a Chrome binary are unavailable.
# These names are populated by _ensure_selenium() before the online flow runs.
webdriver = None
By = None
EC = None
WebDriverWait = None
NoSuchElementException = Exception
TimeoutException = Exception
driver = None

# Asia/Tokyo is a fixed +9 offset with no DST, so we avoid the pytz dependency.
JST = timezone(timedelta(hours=9))

BASE = "https://www.patagonia.jp"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Safari/537.36"
)

SIZE_SELECTORS = (
    "[data-attr='size'] button:not([disabled])",
    "fieldset[data-attr='size'] button:not([disabled])",
    "button.pdp-size-select:not(.is-disabled)",
    "label.pdp-size-select:not(.is-disabled)",
    "[data-size]:not([disabled])",
)

COLOR_SELECTORS = (
    "[data-attr='color'] button",
    "[data-attr='color'] label",
    "[data-attr='color'] a",
    "fieldset[data-attr='color'] button",
    "fieldset[data-attr='color'] label",
    ".color-attribute button",
    ".color-attribute label",
    ".product-attribute__color button",
    ".product-attribute__color label",
)

PRODUCT_GIST_DESCRIPTION = "Patagonia Discounted Products"
PRODUCT_GIST_FILE = "discounted_products.html"
STATE_GIST_DESCRIPTION = "Patagonia Discount State"
STATE_GIST_FILE = "discount_state.json"
MAX_TELEGRAM_ITEMS = 8
TEST_STOP_AFTER_FILTERED_PRODUCTS = 0  # 0 means full run.
TEST_REQUIRE_COLOR_PRICE_DATA = False  # True means test stops only after actual per-color price filtering.

def _ensure_selenium():
    """Import selenium on demand and expose the symbols used by the online flow."""
    global webdriver, By, EC, WebDriverWait, NoSuchElementException, TimeoutException
    from selenium import webdriver as _webdriver
    from selenium.common.exceptions import (
        NoSuchElementException as _NoSuchElementException,
        TimeoutException as _TimeoutException,
    )
    from selenium.webdriver.common.by import By as _By
    from selenium.webdriver.support import expected_conditions as _EC
    from selenium.webdriver.support.ui import WebDriverWait as _WebDriverWait

    webdriver = _webdriver
    By = _By
    EC = _EC
    WebDriverWait = _WebDriverWait
    NoSuchElementException = _NoSuchElementException
    TimeoutException = _TimeoutException


class BotBlockedError(RuntimeError):
    """Raised when the site serves Akamai's bot-failover page instead of products.

    Distinguishes "the site blocked our scraper" (0 items because of a wall) from
    "the sale genuinely has no items" (0 items because nothing is discounted), so
    the caller does not wipe the gist / saved state with a false empty result.
    """


# Markers of Akamai's "Hang Tight! Routing to checkout..." / bot-failover page.
# When these show up, the real product grid never rendered and any 0件 is a block.
_BOT_BLOCK_MARKERS = (
    "hang tight",
    "sit tight",
    "routing to checkout",
    "botfailover",
    "waitroomform",
    "現在ウェブサイトがご利用いただけません",
)


def _looks_blocked(drv):
    """True if the current page is Akamai's bot/waiting-room failover, not content."""
    try:
        title = (drv.title or "").lower()
    except Exception:
        title = ""
    if any(marker in title for marker in ("hang tight", "sit tight", "routing to checkout")):
        return True
    try:
        head = (drv.page_source or "")[:6000].lower()
    except Exception:
        head = ""
    return any(marker in head for marker in _BOT_BLOCK_MARKERS)


def _get_driver():
    """Create (once) and return a headless Chrome driver tuned to look human.

    patagonia.jp sits behind Akamai Bot Manager. A stock Selenium session (old
    user-agent, ``navigator.webdriver === true``, the "enable-automation" switch,
    a ``HeadlessChrome`` UA token) is trivially fingerprinted and served the
    bot-failover page instead of products. We strip those tells so the grid has a
    chance to render. This does not defeat IP-reputation checks, but it removes
    the cheap browser-side signals that flag the request before IP is even weighed.
    """
    global driver
    if driver is not None:
        return driver
    _ensure_selenium()
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")  # 无头模式，后台运行
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,1800")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--lang=ja-JP")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_experimental_option("prefs", {"intl.accept_languages": "ja-JP,ja,en-US,en"})
    driver = webdriver.Chrome(options=options)

    # 用真实 Chrome 版本号覆盖 UA（去掉 HeadlessChrome 标记），避免 UA 版本对不上 /
    # 出现无头特征而被反爬识别。运行时读取真实 navigator.userAgent，再做最小修补。
    try:
        real_ua = driver.execute_script("return navigator.userAgent") or DEFAULT_USER_AGENT
    except Exception:
        real_ua = DEFAULT_USER_AGENT
    clean_ua = real_ua.replace("HeadlessChrome", "Chrome")
    try:
        driver.execute_cdp_cmd(
            "Network.setUserAgentOverride",
            {"userAgent": clean_ua, "acceptLanguage": "ja-JP,ja;q=0.9,en;q=0.8"},
        )
    except Exception as exc:
        print(f"[driver] UA override failed: {exc}")

    # 隐藏 webdriver / 补全 languages、plugins、window.chrome 等指纹。
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": (
                    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                    "Object.defineProperty(navigator,'languages',{get:()=>['ja-JP','ja','en-US','en']});"
                    "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});"
                    "window.chrome=window.chrome||{runtime:{}};"
                )
            },
        )
    except Exception as exc:
        print(f"[driver] stealth script failed: {exc}")

    return driver


def _num(s):
    if s is None:
        return None
    try:
        # 既兼容 "8250.0" 也兼容 "¥ 8,250"
        s = str(s)
        digits = "".join(ch for ch in s if (ch.isdigit() or ch == "."))
        return float(digits) if digits else None
    except Exception:
        return None

def _first_or_none(root, by, sel):
    try:
        return root.find_element(by, sel)
    except Exception:
        return None


def _is_disabled(element):
    cls = (element.get_attribute("class") or "").lower()
    aria_disabled = (element.get_attribute("aria-disabled") or "").lower()
    data_available = (element.get_attribute("data-available") or "").lower()
    disabled_attr = element.get_attribute("disabled")
    if "disabled" in cls or "unavailable" in cls:
        return True
    if aria_disabled in ("true", "1"):
        return True
    if data_available in ("false", "0"):
        return True
    return disabled_attr is not None


def _extract_size_text(element):
    candidates = (
        element.get_attribute("data-size"),
        element.get_attribute("data-value"),
        element.get_attribute("value"),
        element.get_attribute("aria-label"),
        element.text,
    )
    for cand in candidates:
        if not cand:
            continue
        cand = cand.strip()
        if not cand:
            continue
        if cand.lower().startswith("サイズ"):
            parts = cand.split()
            cand = parts[-1] if parts else cand
        return cand
    return None


def _collect_sizes_from_current_page(driver):
    seen = set()
    sizes = []
    for selector in SIZE_SELECTORS:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
        except Exception:
            continue
        for el in elements:
            try:
                if _is_disabled(el):
                    continue
                size = _extract_size_text(el)
                if not size:
                    continue
                if size not in seen:
                    seen.add(size)
                    sizes.append(size)
            except Exception:
                continue
        if sizes:
            break
    return sizes


def _parse_size_list(value):
    if not value:
        return []
    value = value.strip()
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if item]
    except Exception:
        pass
    # 如果不是标准 JSON，尝试手动拆分
    cleaned = value.strip("[]")
    if not cleaned:
        return []
    parts = [p.strip().strip('"').strip("'") for p in cleaned.split(",")]
    return [p for p in parts if p]


def _extract_color_name(element, fallback):
    candidates = (
        element.get_attribute("data-display-value"),
        element.get_attribute("data-color-name"),
        element.get_attribute("data-value"),
        element.get_attribute("data-attr-value"),
        element.get_attribute("aria-label"),
        element.get_attribute("title"),
        element.text,
    )
    for cand in candidates:
        if not cand:
            continue
        cand = cand.strip()
        if cand:
            return cand
    return fallback


def _normalize_color_token(value):
    if not value:
        return ""
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())


def _colors_match(left, right):
    left_token = _normalize_color_token(left)
    right_token = _normalize_color_token(right)
    return bool(left_token and right_token and left_token == right_token)


def _infer_color_from_image_url(image_url, groups):
    image_token = _normalize_color_token(image_url)
    if not image_token:
        return None

    candidates = []
    for group in groups:
        color = group.get("color")
        color_token = _normalize_color_token(color)
        if len(color_token) >= 3:
            candidates.append((len(color_token), color, color_token))

    for _, color, color_token in sorted(candidates, reverse=True):
        if color_token in image_token:
            return color
    return None


COLOR_SALE_PRICE_ATTRS = (
    "sale-price",
    "data-sale-price",
    "data-adjusted-price",
    "data-online-price",
    "data-price",
    "data-promo-price",
    "data-variant-price",
)
COLOR_LIST_PRICE_ATTRS = (
    "list-price",
    "data-list-price",
    "data-pricebook-price",
    "data-standard-price",
    "data-regular-price",
    "data-original-price",
)
PRICE_SELECTORS = (
    "product-detail-pricing",
    "product-pricing",
    "product-tile-pricing",
    "[sale-price][list-price]",
    "[data-sale-price][data-list-price]",
    ".product-detail__price",
    ".product-detail__pricing",
    ".product-pricing",
    ".prices",
    ".price",
    "[class*='price']",
)


def _extract_color_prices(element, data_el):
    sale = None
    listp = None
    for attr in COLOR_SALE_PRICE_ATTRS:
        sale = _num(data_el.get_attribute(attr)) or _num(element.get_attribute(attr))
        if sale:
            break
    for attr in COLOR_LIST_PRICE_ATTRS:
        listp = _num(data_el.get_attribute(attr)) or _num(element.get_attribute(attr))
        if listp:
            break
    return sale, listp


def _find_color_elements(driver):
    for selector in COLOR_SELECTORS:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
        except Exception:
            elements = []
        if elements:
            return elements
    return []


def _get_color_data_element(element):
    if element.tag_name.lower() == "button":
        return element
    try:
        return element.find_element(By.CSS_SELECTOR, "button")
    except NoSuchElementException:
        return element


def _is_selected_color_option(element, data_el):
    for candidate in (data_el, element):
        try:
            cls = (candidate.get_attribute("class") or "").lower()
            aria_pressed = (candidate.get_attribute("aria-pressed") or "").lower()
            aria_checked = (candidate.get_attribute("aria-checked") or "").lower()
            aria_current = (candidate.get_attribute("aria-current") or "").lower()
            data_selected = (candidate.get_attribute("data-selected") or "").lower()
            checked = candidate.get_attribute("checked")
            if any(flag in cls for flag in ("selected", "active", "current", "checked")):
                return True
            if aria_pressed in ("true", "1"):
                return True
            if aria_checked in ("true", "1"):
                return True
            if aria_current in ("true", "page", "step", "location"):
                return True
            if data_selected in ("true", "1"):
                return True
            if checked is not None:
                return True
        except Exception:
            continue
    return False


def _wait_for_color_selection(driver, color_name, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for element in _find_color_elements(driver):
            try:
                data_el = _get_color_data_element(element)
                current_name = _extract_color_name(data_el, fallback="")
                if _colors_match(current_name, color_name) and _is_selected_color_option(element, data_el):
                    return True
            except Exception:
                continue
        time.sleep(0.1)
    return False


def _extract_price_pair_from_text(text):
    if not text:
        return None, None
    prices = []
    for match in re.finditer(r"[¥￥]\s*([0-9][0-9,]*)", text):
        price = _num(match.group(1))
        if price:
            prices.append(price)
    distinct = sorted(set(prices))
    if len(distinct) < 2:
        return None, None
    return distinct[0], distinct[-1]


def _extract_current_page_prices(driver):
    for selector in PRICE_SELECTORS:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
        except Exception:
            continue
        for element in elements:
            try:
                sale, listp = _extract_color_prices(element, element)
                if sale and listp and listp > 0:
                    return sale, listp
                text = (element.text or "").strip()
                if 0 < len(text) <= 500:
                    sale, listp = _extract_price_pair_from_text(text)
                    if sale and listp and listp > 0:
                        return sale, listp
            except Exception:
                continue
    return None, None


def _click_color_element(driver, element):
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    except Exception:
        pass
    driver.execute_script("arguments[0].click();", element)
    time.sleep(0.7)


def _enrich_color_prices_by_click(driver, color_entries):
    for entry in color_entries:
        result = entry["result"]
        if result.get("sale_price") and result.get("list_price"):
            continue
        color_name = result.get("color")
        try:
            _click_color_element(driver, entry["element"])
            if not _wait_for_color_selection(driver, color_name):
                print(f"[color price click] selection did not confirm for {color_name}")
            sale_price, list_price = _extract_current_page_prices(driver)
            result["sale_price"] = sale_price
            result["list_price"] = list_price
            print(
                f"[color price click] {color_name}"
                f" (sale={sale_price}, list={list_price})"
            )
        except Exception as exc:
            print(f"[color price click] error on {color_name}: {exc}")


def _collect_sizes_by_color(driver):
    elements = _find_color_elements(driver)

    attr_results = []
    attr_entries = []
    seen_keys = set()

    for idx, element in enumerate(elements, start=1):
        data_el = _get_color_data_element(element)
        color_name = f"Color #{idx}"
        try:
            if _is_disabled(element) and _is_disabled(data_el):
                continue
            color_name = _extract_color_name(data_el, fallback=color_name)
            color_key = (
                data_el.get_attribute("data-attr-value")
                or data_el.get_attribute("data-caption")
                or color_name
            )
            if color_key in seen_keys:
                continue

            instock_attr = (
                data_el.get_attribute("data-size-stock")
                or data_el.get_attribute("data-online-instock")
            )
            instock_sizes = _parse_size_list(instock_attr)
            if instock_sizes:
                sale_price, list_price = _extract_color_prices(element, data_el)
                result = {
                    "color": color_name,
                    "sizes": instock_sizes,
                    "sale_price": sale_price,
                    "list_price": list_price,
                }
                attr_results.append(result)
                attr_entries.append({"element": element, "result": result})
                seen_keys.add(color_key)
                print(
                    f"[color sizes attr] {color_name} -> {instock_sizes}"
                    f" (sale={sale_price}, list={list_price})"
                )
        except Exception as exc:
            print(f"[color sizes attr] error on {color_name}: {exc}")

    if attr_results:
        if any(not r.get("sale_price") or not r.get("list_price") for r in attr_results):
            _enrich_color_prices_by_click(driver, attr_entries)
        return attr_results

    # 兜底：退回旧的点击方式
    click_results = []
    seen_keys.clear()

    if not elements:
        sizes = _collect_sizes_from_current_page(driver)
        if sizes:
            click_results.append({"color": None, "sizes": sizes})
        return click_results

    for idx, element in enumerate(elements, start=1):
        color_name = f"Color #{idx}"
        try:
            if _is_disabled(element):
                continue
            color_name = _extract_color_name(element, fallback=color_name)
            color_key = element.get_attribute("data-attr-value") or color_name
            if color_key in seen_keys:
                continue

            _click_color_element(driver, element)
            if not _wait_for_color_selection(driver, color_name):
                print(f"[color sizes click] selection did not confirm for {color_name}")
            sizes = _collect_sizes_from_current_page(driver)
            if sizes:
                sale_price, list_price = _extract_current_page_prices(driver)
                click_results.append({
                    "color": color_name,
                    "sizes": sizes,
                    "sale_price": sale_price,
                    "list_price": list_price,
                })
                print(
                    f"[color sizes click] {color_name} -> {sizes}"
                    f" (sale={sale_price}, list={list_price})"
                )
            seen_keys.add(color_key)
        except Exception as exc:
            print(f"[color sizes click] error on {color_name}: {exc}")

    return click_results


def _parse_sizes_from_html(html):
    seen = set()
    sizes = []
    label_pattern = re.compile(
        r"<label[^>]*class=\"[^\"]*pdp-size-select[^\"]*\"[^>]*>(.*?)</label>",
        re.IGNORECASE | re.DOTALL,
    )
    for block in label_pattern.findall(html):
        if "is-disabled" in block.lower():
            continue
        size = None
        match = re.search(r'data-size="([^"]+)"', block)
        if match:
            size = match.group(1).strip()
        if not size:
            match = re.search(r'data-value="([^"]+)"', block)
            if match:
                size = match.group(1).strip()
        if not size:
            match = re.search(r">([^<>]+)</", block)
            if match:
                size = match.group(1).strip()
        if size and size not in seen:
            seen.add(size)
            sizes.append(size)
    if sizes:
        return sizes
    # 兜底：尝试匹配任意 data-size / data-value
    for match in re.finditer(r'data-(?:size|value)="([^"]+)"', html):
        size = match.group(1).strip()
        if size and size not in seen:
            seen.add(size)
            sizes.append(size)
    return sizes


def _fetch_sizes_from_quick_add(qa_url):
    if not qa_url:
        return []
    try:
        response = requests.get(
            qa_url,
            headers={
                "User-Agent": DEFAULT_USER_AGENT,
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=15,
        )
        if response.status_code != 200:
            print(f"[sizes quickadd] status {response.status_code} for {qa_url}")
            return []
        return _parse_sizes_from_html(response.text)
    except Exception as exc:
        print(f"[sizes quickadd] error fetching {qa_url}: {exc}")
        return []


def _fetch_sizes_from_product_page(product_url, main_window):
    if not product_url:
        return []
    try:
        driver.execute_script("window.open(arguments[0], '_blank');", product_url)
        print(f"[sizes product] open {product_url}")
    except Exception as exc:
        print(f"[sizes product] failed to open tab for {product_url}: {exc}")
        return []

    color_sizes = []
    try:
        driver.switch_to.window(driver.window_handles[-1])
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "[data-attr='color'], fieldset[data-attr='size']"))
            )
        except TimeoutException:
            print(f"[sizes product] timeout waiting for options on {product_url}")
        color_sizes = _collect_sizes_by_color(driver)
        print(f"[sizes product] collected {len(color_sizes)} color groups for {product_url}")
    finally:
        try:
            driver.close()
        except Exception:
            pass
        try:
            driver.switch_to.window(main_window)
        except Exception:
            # 如果句柄顺序变化则退回第一个句柄
            driver.switch_to.window(driver.window_handles[0])
    return color_sizes


def _build_github_headers():
    gist_token = os.getenv("GIST_TOKEN")
    if not gist_token:
        raise RuntimeError("GIST_TOKEN is not configured")
    return {
        "Authorization": f"token {gist_token}",
        "Accept": "application/vnd.github+json",
    }


def _find_gist_by_description(description, headers):
    try:
        response = requests.get("https://api.github.com/gists", headers=headers, timeout=30)
        if response.status_code != 200:
            print(f"[gist] failed to list gists: {response.status_code} {response.text}")
            return None
        for gist in response.json():
            if gist.get("description") == description:
                return gist
    except Exception as exc:
        print(f"[gist] list error: {exc}")
    return None


def _upsert_gist(description, files, public=True):
    try:
        headers = _build_github_headers()
    except RuntimeError as exc:
        print(f"[gist] {exc}")
        return None
    gist = _find_gist_by_description(description, headers)
    payload = {"description": description, "files": files}
    try:
        if gist:
            gist_id = gist["id"]
            response = requests.patch(
                f"https://api.github.com/gists/{gist_id}",
                headers=headers,
                json=payload,
                timeout=30,
            )
        else:
            payload["public"] = public
            response = requests.post(
                "https://api.github.com/gists",
                headers=headers,
                json=payload,
                timeout=30,
            )
    except Exception as exc:
        print(f"[gist] upsert error ({description}): {exc}")
        return None

    if response.status_code not in (200, 201):
        print(f"[gist] upsert failed ({description}): {response.status_code} {response.text}")
        return None
    return response.json()


def _build_gist_preview_url(gist_data, file_name):
    if not gist_data:
        return None

    gist_id = (gist_data.get("id") or "").strip()
    owner_login = ((gist_data.get("owner") or {}).get("login") or "").strip()
    if gist_id and owner_login:
        return f"https://htmlpreview.github.io/?https://gist.githubusercontent.com/{owner_login}/{gist_id}/raw"

    file_info = (gist_data.get("files") or {}).get(file_name) or {}
    raw_url = (file_info.get("raw_url") or "").strip()
    if raw_url:
        return f"https://htmlpreview.github.io/?{raw_url}"
    return None


def load_previous_state():
    try:
        headers = _build_github_headers()
    except RuntimeError as exc:
        print(f"[state] {exc}")
        return {}

    gist = _find_gist_by_description(STATE_GIST_DESCRIPTION, headers)
    if not gist:
        return {}

    state_file = gist.get("files", {}).get(STATE_GIST_FILE)
    if not state_file:
        return {}

    content = state_file.get("content")
    if content:
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            print("[state] failed to decode state JSON from gist content")

    raw_url = state_file.get("raw_url")
    if not raw_url:
        return {}

    try:
        response = requests.get(raw_url, timeout=20)
        if response.status_code != 200:
            print(f"[state] failed to fetch raw state: {response.status_code}")
            return {}
        return json.loads(response.text)
    except Exception as exc:
        print(f"[state] failed to load previous state: {exc}")
    return {}


def save_current_state(state):
    content = json.dumps(state, ensure_ascii=False, sort_keys=True)
    gist_data = _upsert_gist(
        description=STATE_GIST_DESCRIPTION,
        files={STATE_GIST_FILE: {"content": content}},
        public=False,
    )
    if gist_data:
        print("[state] state saved")
        return True
    return False


def _build_state_snapshot(items):
    snapshot = []
    for item in items:
        snapshot.append(
            {
                "pid": str(item.get("pid") or ""),
                "name": item.get("name") or "",
                "original_price": item.get("original_price"),
                "sale_price": item.get("sale_price"),
                "discount_percent": item.get("discount_percent"),
                "sizes": sorted(item.get("sizes") or []),
                "product_link": item.get("product_link") or "",
            }
        )
    snapshot.sort(key=lambda x: (x["pid"], x["name"], x["product_link"]))
    return snapshot


def _item_key(item):
    pid = (item.get("pid") or "").strip()
    product_link = (item.get("product_link") or "").strip()
    name = (item.get("name") or "").strip()
    if pid:
        return f"pid:{pid}"
    if product_link:
        return f"url:{product_link}"
    return f"name:{name}"


def _build_snapshot_index(snapshot):
    index = {}
    for item in snapshot:
        index[_item_key(item)] = item
    return index


def _compute_additions(previous_snapshot, current_snapshot):
    previous_index = _build_snapshot_index(previous_snapshot)
    current_index = _build_snapshot_index(current_snapshot)

    new_products = []
    added_sizes = []

    for key, current_item in current_index.items():
        previous_item = previous_index.get(key)
        if not previous_item:
            new_products.append(current_item)
            continue

        previous_size_set = set(previous_item.get("sizes") or [])
        current_size_set = set(current_item.get("sizes") or [])
        newly_added = sorted(current_size_set - previous_size_set)
        if newly_added:
            added_sizes.append(
                {
                    "item": current_item,
                    "sizes": newly_added,
                }
            )

    return {
        "new_products": new_products,
        "added_sizes": added_sizes,
    }


def _has_additions(diff):
    return bool(diff.get("new_products") or diff.get("added_sizes"))


def send_telegram_message(content):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        print("[telegram] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not configured")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": content,
        "disable_web_page_preview": True,
    }
    try:
        response = requests.post(url, json=payload, timeout=20)
    except Exception as exc:
        print(f"[telegram] send failed: {exc}")
        return False

    if response.status_code != 200:
        print(f"[telegram] send failed: {response.status_code} {response.text}")
        return False

    body = response.json()
    if not body.get("ok"):
        print(f"[telegram] send failed: {body}")
        return False

    print("[telegram] message sent")
    return True


def _format_telegram_update(diff, total_count, gist_url):
    utc_now = datetime.now(timezone.utc)
    execution_time = utc_now.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S JST")

    new_products = diff.get("new_products") or []
    added_sizes = diff.get("added_sizes") or []

    lines = [
        "Patagonia 折扣监控有追加",
        f"更新时间: {execution_time}",
        f"当前商品数量: {total_count}",
        f"差分: 新增商品 {len(new_products)} 个, 新增尺码 {len(added_sizes)} 个",
    ]

    if new_products:
        lines.append("")
        lines.append("新增商品:")
        for item in new_products[:MAX_TELEGRAM_ITEMS]:
            name = (item.get("name") or "").strip() or "(Unnamed)"
            discount = item.get("discount_percent")
            sale_price = item.get("sale_price")
            line = f"- {name} | {discount}%"
            if sale_price is not None:
                line += f" | ¥{sale_price:,}"
            lines.append(line)
            product_link = (item.get("product_link") or "").strip()
            if product_link:
                lines.append(product_link)
        if len(new_products) > MAX_TELEGRAM_ITEMS:
            lines.append(f"... 新增商品还有 {len(new_products) - MAX_TELEGRAM_ITEMS} 个")

    if added_sizes:
        lines.append("")
        lines.append("新增尺码:")
        for entry in added_sizes[:MAX_TELEGRAM_ITEMS]:
            item = entry["item"]
            sizes = entry["sizes"]
            name = (item.get("name") or "").strip() or "(Unnamed)"
            lines.append(f"- {name} | +{' / '.join(sizes)}")
            product_link = (item.get("product_link") or "").strip()
            if product_link:
                lines.append(product_link)
        if len(added_sizes) > MAX_TELEGRAM_ITEMS:
            lines.append(f"... 新增尺码还有 {len(added_sizes) - MAX_TELEGRAM_ITEMS} 个")

    if gist_url:
        lines.append("")
        lines.append(f"折扣列表: {gist_url}")

    message = "\n".join(lines)
    if len(message) > 3900:
        message = message[:3890] + "\n...(truncated)"
    return message


def send_wechat_message(title, content):
    # 替换为你的 Server酱 SendKey
    send_key = os.getenv("WECHAT_SENDKEY")
    url = f"https://sctapi.ftqq.com/{send_key}.send"
    
    data = {
        "title": title,       # 消息标题
        "desp": content       # 消息内容
    }
    
    response = requests.post(url, data=data)
    if response.status_code == 200:
        print("消息发送成功")
    else:
        print("消息发送失败:", response.text)

def _filter_groups_by_discount(
    groups,
    min_discount,
    product_name="",
    fallback_color=None,
    fallback_sale=None,
    fallback_list=None,
):
    if not groups:
        return groups

    has_price_data = any(
        g.get("sale_price") and g.get("list_price") and g.get("list_price") > 0
        for g in groups
    )
    if not has_price_data:
        fallback_discount = None
        if fallback_sale and fallback_list and fallback_list > 0:
            fallback_discount = round((fallback_list - fallback_sale) * 100 / fallback_list, 1)
        if fallback_discount is None or fallback_discount < min_discount:
            print(
                f"[filter] {product_name}: no per-color price data and product"
                f" discount {fallback_discount}% < {min_discount}%, dropping colors"
            )
            return []

        if fallback_color:
            filtered = [g for g in groups if _colors_match(g.get("color"), fallback_color)]
            if filtered:
                for group in filtered:
                    group["sale_price"] = fallback_sale
                    group["list_price"] = fallback_list
                print(
                    f"[filter] {product_name}: no per-color price data, inferred"
                    f" discounted color '{fallback_color}' from image, keeping only it"
                )
                return filtered

        if len(groups) == 1:
            groups[0]["sale_price"] = fallback_sale
            groups[0]["list_price"] = fallback_list
            print(
                f"[filter] {product_name}: no per-color price data, single color,"
                f" keeping it with product discount {fallback_discount}%"
            )
            return groups

        print(
            f"[filter] {product_name}: no per-color price data and could not infer"
            " discounted color, dropping colors"
        )
        return []

    color_discounts = []
    for g in groups:
        sale = g.get("sale_price")
        listp = g.get("list_price")
        if not sale or not listp or listp <= 0:
            color_discounts.append(None)
            continue
        color_discounts.append(round((listp - sale) * 100 / listp, 1))

    valid = [d for d in color_discounts if d is not None]
    if not valid:
        return []
    filtered = []
    for g, d in zip(groups, color_discounts):
        if d is None:
            print(
                f"[filter] {product_name}: color '{g.get('color')}' missing price, dropping"
            )
            continue
        if d >= min_discount:
            filtered.append(g)
        else:
            print(
                f"[filter] {product_name}: drop color '{g.get('color')}'"
                f" (discount {d}% < min {min_discount}%)"
            )
    return filtered


# ---------------------------------------------------------------------------
# Layout-resilient listing parser
#
# A site redesign ("改版") usually renames the theme-level CSS classes while the
# application-level signals survive: product tiles still carry data-pid, prices
# still live in a <product-tile-pricing> custom element (or data-*-price
# attributes), and the name/link are plain anchors. So instead of matching exact
# class chains, we parse the rendered HTML into a small DOM and extract by these
# stable signals, with a yen-text fallback for the price. This also lets us
# verify parsing offline against a saved page (see run_offline / --html).
# ---------------------------------------------------------------------------

_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}

NAME_CLASS_HINTS = (
    "product-tile__name",
    "product-name",
    "tile__name",
    "product-tile-name",
    "producttile__name",
    "pdp-link",
)


class _Node:
    __slots__ = ("tag", "attrs", "children", "parent", "text_parts")

    def __init__(self, tag, attrs):
        self.tag = tag
        self.attrs = attrs
        self.children = []
        self.parent = None
        self.text_parts = []

    def attr(self, name):
        return self.attrs.get(name)

    @property
    def classes(self):
        return (self.attrs.get("class") or "").lower()

    def iter(self):
        yield self
        for child in self.children:
            yield from child.iter()

    def text(self):
        parts = []

        def _rec(node):
            for chunk in node.text_parts:
                if chunk and chunk.strip():
                    parts.append(chunk.strip())
            for child in node.children:
                _rec(child)

        _rec(self)
        return " ".join(parts)


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {})
        self.stack = [self.root]

    def _append(self, tag, attrs):
        node = _Node(tag, {k: (v or "") for k, v in attrs})
        node.parent = self.stack[-1]
        self.stack[-1].children.append(node)
        return node

    def handle_starttag(self, tag, attrs):
        node = self._append(tag, attrs)
        if tag not in _VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self._append(tag, attrs)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if data and data.strip():
            self.stack[-1].text_parts.append(data)


def _build_dom(html):
    builder = _TreeBuilder()
    try:
        builder.feed(html or "")
    except Exception as exc:
        print(f"[parse] DOM build error: {exc}")
    return builder.root


def _tile_image(tile):
    for node in tile.iter():
        if node.tag == "meta" and node.attr("itemprop") == "image" and node.attr("content"):
            return node.attr("content")
    for node in tile.iter():
        if node.tag != "img":
            continue
        for attr in ("src", "data-src", "data-original"):
            value = node.attr(attr)
            if value:
                return value
        srcset = node.attr("srcset")
        if srcset:
            return srcset.split(",")[0].strip().split(" ")[0]
    return None


def _tile_link(tile):
    best = None
    for node in tile.iter():
        if node.tag != "a":
            continue
        href = node.attr("href")
        if not href:
            continue
        if node.attr("itemprop") == "url":
            return href
        if best is None and ("/product" in href or ".html" in href or "/shop/" in href):
            best = href
        if best is None:
            best = href
    return best


def _tile_name(tile):
    for node in tile.iter():
        if node.attr("itemprop") == "name":
            text = node.text()
            if text:
                return text
    for node in tile.iter():
        cls = node.classes
        if any(hint in cls for hint in NAME_CLASS_HINTS):
            text = node.text()
            if text:
                return text
    for node in tile.iter():
        if node.tag == "a":
            for attr in ("aria-label", "title"):
                value = node.attr(attr)
                if value and value.strip():
                    return value.strip()
            text = node.text()
            if text:
                return text
    for node in tile.iter():
        if node.tag == "img" and node.attr("alt"):
            return node.attr("alt").strip()
    return ""


def _tile_prices(tile):
    for node in tile.iter():
        sale = None
        listp = None
        for attr in COLOR_SALE_PRICE_ATTRS:
            value = _num(node.attr(attr))
            if value:
                sale = value
                break
        for attr in COLOR_LIST_PRICE_ATTRS:
            value = _num(node.attr(attr))
            if value:
                listp = value
                break
        if sale and listp and listp > 0:
            return sale, listp
    return _extract_price_pair_from_text(tile.text())


def _tile_qa_url(tile):
    for node in tile.iter():
        data_url = node.attr("data-url")
        if not data_url:
            continue
        cls = node.classes
        low = data_url.lower()
        if "quickadd" in cls or "quick-add" in cls or "quickadd" in low or "showquickview" in low:
            return data_url
    return None


def collect_tile_pids(html):
    """Return all product-tile ``data-pid`` values present in a listing page.

    Used to drive pagination: we advance/stop based on the raw set of product
    tiles, independent of whether any of them happen to be discounted, so a page
    full of full-price items does not look like "the end of the results".
    """
    root = _build_dom(html)
    pids = []
    seen = set()
    for tile in root.iter():
        pid = tile.attr("data-pid")
        if pid and pid not in seen:
            seen.add(pid)
            pids.append(pid)
    return pids


def parse_listing_html(html, min_discount=0):
    """Extract discounted product tiles from a rendered listing page's HTML.

    Returns a list of item dicts. Tiles without a usable sale/list price pair, or
    whose discount does not exceed ``min_discount``, are skipped.
    """
    root = _build_dom(html)
    items = []
    seen_pids = set()
    for tile in root.iter():
        pid = tile.attr("data-pid")
        if not pid or pid in seen_pids:
            continue
        sale_price, list_price = _tile_prices(tile)
        if not sale_price or not list_price or list_price <= 0:
            continue
        discount_percent = round((list_price - sale_price) * 100 / list_price, 1)
        if discount_percent <= min_discount:
            continue

        link = _tile_link(tile)
        image = _tile_image(tile)
        qa = _tile_qa_url(tile)
        seen_pids.add(pid)
        items.append({
            "pid": pid,
            "name": _tile_name(tile),
            "original_price": int(list_price),
            "sale_price": int(sale_price),
            "discount_percent": discount_percent,
            "image_url": urljoin(BASE, image) if image else None,
            "product_link": urljoin(BASE, link) if link else None,
            "qa_url": urljoin(BASE, qa) if qa else None,
        })
    return items


def _find_show_more_button(drv):
    """Locate a visible "Show more / もっと見る" pagination button, if present.

    SFCC listing grids load a fixed first batch and append the rest into the
    same page via an AJAX "show more" button (or infinite scroll). The button is
    what advances pagination — query params like ?page / ?start are reset by the
    front-end JS — so we must find and click it to load every product.
    """
    selectors = (
        "div.show-more button",
        "div.show-more a",
        "button.show-more",
        "a.show-more",
        "button.btn-show-more",
        ".show-more-button",
        "button.more",
        "[data-url*='UpdateGrid']",
        "[data-url*='Search-Show']",
        "button[class*='more']",
        "a[class*='more']",
    )
    for selector in selectors:
        try:
            elements = drv.find_elements(By.CSS_SELECTOR, selector)
        except Exception:
            continue
        for el in elements:
            try:
                if el.is_displayed():
                    return el
            except Exception:
                continue

    # 文本兜底：按钮文案可能是「もっと見る」「Show more」等。
    text_markers = ("もっと", "さらに", "show more", "view more", "load more", "more results")
    try:
        clickables = drv.find_elements(By.XPATH, "//button | //a")
    except Exception:
        clickables = []
    for el in clickables:
        try:
            if not el.is_displayed():
                continue
            raw = (el.text or "").strip()
            low = raw.lower()
            if low == "more" or any(marker in low or marker in raw for marker in text_markers):
                return el
        except Exception:
            continue
    return None


def _expand_listing(drv, wait_timeout, max_iterations=300):
    """Scroll + click "show more" until no further product tiles load.

    Returns the fully-expanded page HTML so the listing parser sees every
    product, not just the first batch.
    """
    last_count = len(collect_tile_pids(drv.page_source))
    print(f"[expand] initial tiles: {last_count}")
    for i in range(max_iterations):
        try:
            drv.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        except Exception:
            pass

        button = _find_show_more_button(drv)
        if button is not None:
            try:
                drv.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
                time.sleep(0.3)
                drv.execute_script("arguments[0].click();", button)
            except Exception as exc:
                print(f"[expand] click error: {exc}")

        # 等待瓦片数量增长（无按钮时只等待少量时间给懒加载/滚动反应）。
        try:
            WebDriverWait(drv, wait_timeout if button is not None else 3).until(
                lambda d: len(collect_tile_pids(d.page_source)) > last_count
            )
        except TimeoutException:
            pass

        count = len(collect_tile_pids(drv.page_source))
        print(
            f"[expand] step {i + 1}: tiles {last_count} -> {count}"
            f" (show_more={'yes' if button is not None else 'no'})"
        )
        if count <= last_count and button is None:
            break
        last_count = count

    return drv.page_source


def fetch_discounted_products():
    min_discount = 0
    min_color_discount = 50
    page_size = 48  # 初始请求一个较大的 sz，站点接受时可减少 show-more 次数
    wait_timeout = 30

    # ---------- 第 1 轮：加载列表页并展开全部商品，再用 parse_listing_html 解析 ----------
    # patagonia.jp 基于 Salesforce Commerce Cloud（Demandware）。列表页只首屏渲染
    # 一批商品，其余通过「もっと見る / Show more」按钮（或下拉懒加载）AJAX 追加到
    # 同一个页面。?page / ?start 等查询参数会被前端 JS 重置，所以必须反复点击
    # show-more 把所有页都展开后再解析，否则只会拿到第一页。
    drv = _get_driver()
    items = []
    seen_pids = set()
    wait_selector = "[data-pid]"

    url = f"{BASE}/shop/web-specials?sz={page_size}"
    full_html = None
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        print(f"[round1] attempt {attempt}/{max_attempts}: fetching {url}")
        if attempt > 1:
            # 重新发起请求前清掉可能携带的「已被标记」cookie，并退避一会儿。
            try:
                drv.delete_all_cookies()
            except Exception:
                pass
            time.sleep(5 * attempt)
        try:
            drv.get(url)
        except Exception as exc:
            print(f"[round1] attempt {attempt}: navigation error: {exc}")
            continue

        try:
            WebDriverWait(drv, wait_timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, wait_selector))
            )
        except TimeoutException:
            if _looks_blocked(drv):
                print(f"[round1] attempt {attempt}: blocked by Akamai bot-failover page")
            else:
                print(f"[round1] attempt {attempt}: wait timeout (no [data-pid])")
            continue

        if _looks_blocked(drv):
            print(f"[round1] attempt {attempt}: bot-failover page detected, retrying")
            continue

        full_html = _expand_listing(drv, wait_timeout)
        break

    if full_html is None:
        if _looks_blocked(drv):
            raise BotBlockedError(
                f"Akamai bot-failover page returned for all {max_attempts} attempts;"
                " 0件 here means we were blocked, not that the sale is empty"
            )
        print(f"[round1] no [data-pid] after {max_attempts} attempts, giving up")
        return [], render_products_html([])
    raw_total = len(collect_tile_pids(full_html))

    page_items = parse_listing_html(full_html, min_discount=min_discount)
    for it in page_items:
        if it["pid"] in seen_pids:
            continue
        seen_pids.add(it["pid"])
        items.append(it)

    print(
        f"[round1] expanded to {raw_total} product tiles,"
        f" {len(items)} discounted products"
    )

    # ---------- 第 2 轮：为每个 item 在新标签页里采集尺码 ----------
    main_window = driver.current_window_handle
    processed_items = []
    for it in items:
        qa_url = it.get("qa_url")
        product_link = it.get("product_link")
        sizes = []

        color_size_groups = _fetch_sizes_from_product_page(product_link, main_window)

        if not color_size_groups and qa_url:
            quick_sizes = _fetch_sizes_from_quick_add(qa_url)
            if quick_sizes:
                color_size_groups = [{"color": None, "sizes": quick_sizes}]
                print(f"[sizes quickadd] collected {len(quick_sizes)} sizes for {qa_url}")

        fallback_color = _infer_color_from_image_url(
            it.get("image_url"),
            color_size_groups,
        )
        color_size_groups = _filter_groups_by_discount(
            color_size_groups,
            min_discount=min_color_discount,
            product_name=it.get("name"),
            fallback_color=fallback_color,
            fallback_sale=it.get("sale_price"),
            fallback_list=it.get("original_price"),
        )
        has_color_price_data = any(
            group.get("sale_price") and group.get("list_price") and group.get("list_price") > 0
            for group in color_size_groups
        )

        for group in color_size_groups:
            g_sale = group.get("sale_price")
            g_list = group.get("list_price")
            if g_sale and g_list and g_list > 0:
                it["sale_price"] = int(g_sale)
                it["original_price"] = int(g_list)
                it["discount_percent"] = round((g_list - g_sale) * 100 / g_list, 1)
                break

        for group in color_size_groups:
            color_label = group.get("color") or ""
            size_text = " ".join(group.get("sizes", []))
            if not size_text:
                continue
            if color_label:
                sizes.append(f"{color_label}: {size_text}")
            else:
                sizes.append(size_text)

        it["sizes"] = sizes
        if not sizes:
            print(
                f"[filter] {it.get('name')}: no colors at >= {min_color_discount}%"
                " discount with stock, dropping product"
            )
            continue

        if TEST_STOP_AFTER_FILTERED_PRODUCTS:
            if sizes and (has_color_price_data or not TEST_REQUIRE_COLOR_PRICE_DATA):
                processed_items.append(it)
                if len(processed_items) >= TEST_STOP_AFTER_FILTERED_PRODUCTS:
                    test_mode = (
                        "color-filtered"
                        if has_color_price_data
                        else "sizes-only/no per-color price data"
                    )
                    print(
                        f"[test] stop after {len(processed_items)} test product(s)"
                        f" ({test_mode})"
                    )
                    break
            continue

        processed_items.append(it)

    items = processed_items

    return items, render_products_html(items)


def render_products_html(items):
    execution_time = datetime.now(timezone.utc).astimezone(JST).strftime("%Y-%m-%d %H:%M:%S")

    html_content = f"""
    <!DOCTYPE html>
    <html lang="ja">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Patagonia Discounted Products</title>
        <style>
            body {{ font-family: Arial, sans-serif; }}
            .product {{ border: 1px solid #ddd; padding: 10px; margin: 10px 0; }}
            .product img {{ max-width: 200px; }}
            .product h2 {{ font-size: 1.2em; color: #333; }}
            .price {{ font-weight: bold; color: #d9534f; }}
            .original-price {{ text-decoration: line-through; color: #888; }}
            .timestamp {{ color: #555; font-size: 0.9em; margin-top: 10px; }}
            .sizes {{ margin-top: 5px; color: #555; font-size: 0.9em; }}
        </style>
    </head>
    <body>
        <h1>Discounted Products</h1>
        <p class="timestamp">Generated on: {execution_time}</p>
    """

    for p in items:
        sizes = p.get("sizes") or []
        html_content += f"""
        <div class="product">
            <h2>{p['name']}</h2>
            <a href="{p['product_link'] or '#'}" target="_blank">
                <img src="{p['image_url'] or ''}" alt="{p['name']}">
            </a>
            <p class="original-price">Original Price: ¥ {p['original_price']:,}</p>
            <p class="price">Sale Price: ¥ {p['sale_price']:,}</p>
            <p>Discount Percent: {p['discount_percent']}%</p>
            <p class="sizes">Sizes: {" | ".join(sizes) if sizes else "-"}</p>
        </div>
        """

    html_content += """
    </body>
    </html>
    """
    return html_content

def upload_to_gist(content):
    gist_data = _upsert_gist(
        description=PRODUCT_GIST_DESCRIPTION,
        files={PRODUCT_GIST_FILE: {"content": content}},
        public=True,
    )
    if gist_data:
        print("[gist] product gist upserted")
        return _build_gist_preview_url(gist_data, PRODUCT_GIST_FILE)
    return None


def main():
    previous_state = load_previous_state()
    previous_snapshot = previous_state.get("snapshot") or []

    try:
        items, html_content = fetch_discounted_products()
    except BotBlockedError as exc:
        # 被 Akamai 拦截时拿到的是 0 件假结果。绝不能用空列表覆盖 Gist / state，
        # 否则会清空用户实际看到的清单，并在下次恢复时误报一堆「新增」。
        print(f"[blocked] {exc}")
        print("[blocked] keeping previous gist/state; not uploading empty result or sending telegram")
        return

    gist_url = upload_to_gist(html_content)

    current_snapshot = _build_state_snapshot(items)
    diff = _compute_additions(previous_snapshot, current_snapshot)
    has_additions = _has_additions(diff)
    print(f"[state] has_additions={has_additions}")

    if has_additions:
        message = _format_telegram_update(diff, len(items), gist_url)
        message_sent = send_telegram_message(message)
        if message_sent:
            save_current_state(
                {
                    "snapshot": current_snapshot,
                    "updated_at": datetime.utcnow().isoformat() + "Z",
                    "count": len(items),
                }
            )
    else:
        print("[state] no additions, telegram not sent")
        save_current_state(
            {
                "snapshot": current_snapshot,
                "updated_at": datetime.utcnow().isoformat() + "Z",
                "count": len(items),
            }
        )


def run_offline(html_path, output_html=None):
    """Parse a saved web-specials page (no browser / network) and report results.

    Lets us verify the listing parser against a real rendered page on one page,
    which is exactly what is needed to confirm the redesign selectors work.
    """
    with open(html_path, encoding="utf-8") as fp:
        html = fp.read()

    items = parse_listing_html(html, min_discount=0)
    print(f"[offline] {html_path}: parsed {len(items)} discounted product tiles")
    for it in items:
        name = it["name"] or "(no name)"
        print(
            f"  - [{it['pid']}] {name} | ¥{it['sale_price']:,}"
            f" (was ¥{it['original_price']:,}) -{it['discount_percent']}%"
        )
        print(f"      link: {it['product_link']}")

    if output_html:
        with open(output_html, "w", encoding="utf-8") as fp:
            fp.write(render_products_html(items))
        print(f"[offline] wrote preview HTML to {output_html}")
    return items


if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser(description="Patagonia web-specials discount checker")
    arg_parser.add_argument(
        "--html",
        metavar="FILE",
        help="Parse a saved web-specials HTML file offline (no browser/network) and exit.",
    )
    arg_parser.add_argument(
        "--out",
        metavar="FILE",
        help="With --html, also write a preview HTML of the parsed products.",
    )
    args, _ = arg_parser.parse_known_args()

    if args.html:
        run_offline(args.html, output_html=args.out)
    else:
        try:
            main()
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
