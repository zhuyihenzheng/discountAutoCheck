import json
import re
import time
from datetime import datetime
from urllib.parse import urljoin

import os
import pytz
import requests
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

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

# 配置 Chrome 浏览器选项
options = webdriver.ChromeOptions()

options.add_argument("--headless")  # 无头模式，后台运行
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("start-maximized")          # 最大化窗口
options.add_argument("disable-infobars")
options.add_argument("--disable-extensions")
options.add_argument("--headless=new")
options.add_argument(f"user-agent={DEFAULT_USER_AGENT}")
#driver_path = "/usr/local/bin/chromedriver-linux64/chromedriver"
#service = Service(driver_path)
#driver = webdriver.Chrome(service=service, options=options)
driver = webdriver.Chrome(options=options)

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


def _collect_sizes_by_color(driver):
    elements = []
    for selector in COLOR_SELECTORS:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
        except Exception:
            elements = []
        if elements:
            break

    attr_results = []
    seen_keys = set()

    def _get_data_element(el):
        if el.tag_name.lower() == "button":
            return el
        try:
            return el.find_element(By.CSS_SELECTOR, "button")
        except NoSuchElementException:
            return el

    for idx, element in enumerate(elements, start=1):
        data_el = _get_data_element(element)
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
                attr_results.append({"color": color_name, "sizes": instock_sizes})
                seen_keys.add(color_key)
                print(f"[color sizes attr] {color_name} -> {instock_sizes}")
        except Exception as exc:
            print(f"[color sizes attr] error on {color_name}: {exc}")

    if attr_results:
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

            try:
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
            except Exception:
                pass
            driver.execute_script("arguments[0].click();", element)
            time.sleep(0.5)
            sizes = _collect_sizes_from_current_page(driver)
            if sizes:
                click_results.append({"color": color_name, "sizes": sizes})
                print(f"[color sizes click] {color_name} -> {sizes}")
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
    utc_now = datetime.utcnow()
    jst = pytz.timezone("Asia/Tokyo")
    execution_time = utc_now.astimezone(jst).strftime("%Y-%m-%d %H:%M:%S JST")

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
        if len(added_sizes) > MAX_TELEGRAM_ITEMS:
            lines.append(f"... 新增尺码还有 {len(added_sizes) - MAX_TELEGRAM_ITEMS} 个")

    if gist_url:
        lines.append(f"详情: {gist_url}")

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

def fetch_discounted_products():
    min_discount = 30
    max_pages = None  # None 表示自动遍历直至没有更多商品
    wait_timeout = 30
    delay_between_pages = 1.5

    # ---------- 第 1 轮：只收集主列表信息 + qa_url（不跳走页面） ----------
    items = []
    seen_pids = set()
    wait_selector = (
        "div.product[data-pid], "
        "div.product-grid__tile[data-pid], "
        "div[data-pid].product-grid__tile"
    )
    page = 1
    prev_tiles_count = 0
    while True:
        url = f"{BASE}/shop/web-specials?page={page}"
        print(f"[page {page}] fetching {url}")
        try:
            driver.get(url)
        except Exception as exc:
            print(f"[page {page}] navigation error: {exc}")
            break
        try:
            ready_state = driver.execute_script("return document.readyState")
            print(f"[page {page}] readyState = {ready_state}")
        except Exception as exc:
            print(f"[page {page}] readyState fetch error: {exc}")

        try:
            WebDriverWait(driver, wait_timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, wait_selector))
            )
        except TimeoutException:
            print(f"[page {page}] wait timeout, stop paging")
            break

        tiles = driver.find_elements(By.CSS_SELECTOR, "div.product[data-pid]")
        print(f"[page {page}] tiles(div.product[data-pid]) = {len(tiles)}")
        if not tiles:
            tiles = driver.find_elements(By.CSS_SELECTOR, "div.product-grid__tile[data-pid]")
            print(f"[page {page}] tiles(div.product-grid__tile[data-pid]) = {len(tiles)}")
        if not tiles:
            tiles = driver.find_elements(By.CSS_SELECTOR, "div[data-pid].product-grid__tile")
            print(f"[page {page}] tiles(div[data-pid].product-grid__tile) = {len(tiles)}")
        if not tiles:
            tiles = driver.find_elements(By.CSS_SELECTOR, "div.product[data-pid] > product-tile")
            print(f"[page {page}] tiles(div.product[data-pid] > product-tile) = {len(tiles)}")
        if not tiles:
            shadow_tiles = driver.find_elements(By.CSS_SELECTOR, "product-tile")
            print(f"[page {page}] tiles(product-tile) = {len(shadow_tiles)}")
            if shadow_tiles:
                try:
                    first_shadow = shadow_tiles[0]
                    outer_html = first_shadow.get_attribute("outerHTML")
                    snippet = outer_html[:2000]
                    print(f"[page {page}] shadow product tile snippet:\n{snippet}")
                except Exception:
                    pass
        print(f"[page {page}] final tiles count: {len(tiles)}")
        if not tiles:
            print(f"[page {page}] no tiles, stop paging")
            try:
                snippet = driver.page_source[:2000]
                print(f"[page {page}] page source snippet:\n{snippet}")
            except Exception:
                pass
            break

        new_tiles_count = len(tiles)
        if new_tiles_count == prev_tiles_count:
            print(f"[paging] no new products found on page {page}, stop")
            break

        prev_tiles_count = new_tiles_count

        page += 1
        time.sleep(delay_between_pages)

    for idx, tile in enumerate(tiles, start=1):
        try:
            pid = tile.get_attribute("data-pid")
            if pid and pid in seen_pids:
                continue

            name_el = _first_or_none(tile, By.CSS_SELECTOR, ".pdp-link .product-tile__name")
            product_name = name_el.text.strip() if name_el else ""

            link_el = _first_or_none(tile, By.CSS_SELECTOR, ".pdp-link a.link[itemprop='url']")
            product_link = urljoin(BASE, link_el.get_attribute("href")) if link_el else None

            # 优先 <product-tile-pricing> 属性
            ptp = _first_or_none(tile, By.CSS_SELECTOR, "product-tile-pricing")
            sale_price = _num(ptp.get_attribute("sale-price")) if ptp else None
            list_price = _num(ptp.get_attribute("list-price")) if ptp else None

            # 放宽兜底选择器：在整卡片里找任意带data价格的元素
            if sale_price is None or list_price is None:
                any_price_el = _first_or_none(
                    tile, By.CSS_SELECTOR, "[data-sale-price][data-list-price]"
                )
                if any_price_el:
                    sale_price = sale_price or _num(any_price_el.get_attribute("data-sale-price"))
                    list_price = list_price or _num(any_price_el.get_attribute("data-list-price"))

            if not sale_price or not list_price or list_price <= 0:
                print(f"[skip page {page} #{idx}] price missing")
                continue

            discount_percent = round((list_price - sale_price) * 100 / list_price, 1)
            if discount_percent <= min_discount:
                print(f"[skip page {page} #{idx}] discount {discount_percent}% < {min_discount}%")
                continue
            # 图片
            img_meta = (_first_or_none(
                tile, By.CSS_SELECTOR, ".product-tile__image.default.active meta[itemprop='image']"
            ) or _first_or_none(
                tile, By.CSS_SELECTOR, ".product-tile__cover meta[itemprop='image']"
            ))
            image_url = img_meta.get_attribute("content") if img_meta else None

            # 记录 quick add 片段地址（不要现在跳）
            qa_btn = _first_or_none(
                tile, By.CSS_SELECTOR,
                ".product-tile__quickadd-container .tile-quickadd-btn[data-url]"
            )
            qa_url = urljoin(BASE, qa_btn.get_attribute("data-url")) if qa_btn else None

            items.append({
                "pid": pid,
                "name": product_name,
                "original_price": int(list_price),
                "sale_price": int(sale_price),
                "discount_percent": discount_percent,
                "image_url": image_url,
                "product_link": product_link,
                "qa_url": qa_url,    # 第二轮再用
            })
            if pid:
                seen_pids.add(pid)
                
        except Exception as e:
            print(f"[collect error page {page} #{idx}] {e}")

    # ---------- 第 2 轮：为每个 item 在新标签页里采集尺码 ----------
    main_window = driver.current_window_handle
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

    # ---------- 生成 HTML ----------
    utc_now = datetime.utcnow()
    jst = pytz.timezone("Asia/Tokyo")
    execution_time = utc_now.astimezone(jst).strftime("%Y-%m-%d %H:%M:%S")

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
        html_content += f"""
        <div class="product">
            <h2>{p['name']}</h2>
            <a href="{p['product_link'] or '#'}" target="_blank">
                <img src="{p['image_url'] or ''}" alt="{p['name']}">
            </a>
            <p class="original-price">Original Price: ¥ {p['original_price']:,}</p>
            <p class="price">Sale Price: ¥ {p['sale_price']:,}</p>
            <p>Discount Percent: {p['discount_percent']}%</p>
            <p class="sizes">Sizes: {" | ".join(p['sizes']) if p['sizes'] else "-"}</p>
        </div>
        """

    html_content += """
    </body>
    </html>
    """
    return items, html_content

def upload_to_gist(content):
    gist_data = _upsert_gist(
        description=PRODUCT_GIST_DESCRIPTION,
        files={PRODUCT_GIST_FILE: {"content": content}},
        public=True,
    )
    if gist_data:
        print("[gist] product gist upserted")
        return gist_data.get("html_url")
    return None


def main():
    previous_state = load_previous_state()
    previous_snapshot = previous_state.get("snapshot") or []

    items, html_content = fetch_discounted_products()
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


if __name__ == "__main__":
    try:
        main()
    finally:
        try:
            driver.quit()
        except Exception:
            pass
