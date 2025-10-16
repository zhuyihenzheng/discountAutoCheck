import re
import time
from datetime import datetime
from urllib.parse import urljoin

import os
import pytz
import requests
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
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
    except Exception as exc:
        print(f"[sizes product] failed to open tab for {product_url}: {exc}")
        return []

    sizes = []
    try:
        driver.switch_to.window(driver.window_handles[-1])
        try:
            WebDriverWait(driver, 15).until(
                lambda d: bool(_collect_sizes_from_current_page(d))
            )
        except TimeoutException:
            pass
        sizes = _collect_sizes_from_current_page(driver)
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
    return sizes


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
    min_discount = 50
    max_pages = 5
    wait_timeout = 30
    delay_between_pages = 1.5

    # ---------- 第 1 轮：只收集主列表信息 + qa_url（不跳走页面） ----------
    items = []
    seen_pids = set()
    selector_wait = (
        "div.product[data-pid], "
        "div.product-grid__tile[data-pid], "
        "div[data-pid].product-grid__tile"
    )
    for page in range(1, max_pages + 1):
        url = f"{BASE}/shop/web-specials/men?page={page}"
        print(f"[page {page}] fetching {url}")
        try:
            driver.get(url)
        except Exception as exc:
            print(f"[page {page}] navigation error: {exc}")
            break

        try:
            WebDriverWait(driver, wait_timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, selector_wait))
            )
        except TimeoutException:
            print(f"[page {page}] wait timeout, stop paging")
            break

        tiles = driver.find_elements(By.CSS_SELECTOR, "div.product[data-pid]")
        if not tiles:
            tiles = driver.find_elements(By.CSS_SELECTOR, "div.product-grid__tile[data-pid]")
        if not tiles:
            tiles = driver.find_elements(By.CSS_SELECTOR, "div[data-pid].product-grid__tile")
        if not tiles:
            tiles = driver.find_elements(By.CSS_SELECTOR, "div.product[data-pid] > product-tile")
        print(f"[page {page}] found tiles: {len(tiles)}")
        if not tiles:
            print(f"[page {page}] no tiles, stop paging")
            break

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
                if discount_percent < min_discount:
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

        if page < max_pages:
            time.sleep(delay_between_pages)

    # ---------- 第 2 轮：为每个 item 在新标签页里采集尺码 ----------
    main_window = driver.current_window_handle
    for it in items:
        sizes = []
        qa_url = it.get("qa_url")
        product_link = it.get("product_link")

        # 先尝试直接在商品详情页抓取尺码
        sizes = _fetch_sizes_from_product_page(product_link, main_window)

        # 兜底：如果详情页取不到，再尝试 quick add 片段
        if not sizes and qa_url:
            sizes = _fetch_sizes_from_quick_add(qa_url)

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
    return html_content

def upload_to_gist(content):
    GIST_TOKEN = os.getenv("GIST_TOKEN")
    headers = {"Authorization": f"token {GIST_TOKEN}"}

    # 检查是否已有 Gist
    gist_id = None
    response = requests.get("https://api.github.com/gists", headers=headers)
    if response.status_code == 200:
        gists = response.json()
        for gist in gists:
            if gist["description"] == "Patagonia Discounted Products":
                gist_id = gist["id"]
                break

    # 创建或更新 Gist
    if gist_id:
        url = f"https://api.github.com/gists/{gist_id}"
        payload = {
            "description": "Patagonia Discounted Products",
            "files": {
                "discounted_products.html": {
                    "content": content
                }
            }
        }
        requests.patch(url, headers=headers, json=payload)
        print("Gist updated.")
    else:
        url = "https://api.github.com/gists"
        payload = {
            "description": "Patagonia Discounted Products",
            "public": True,
            "files": {
                "discounted_products.html": {
                    "content": content
                }
            }
        }
        requests.post(url, headers=headers, json=payload)
        print("New Gist created: ")
        # 测试推送
        

# 获取商品数据并上传到 Gist
html_content = fetch_discounted_products()
upload_to_gist(html_content)
#send_wechat_message("提醒", "快去看！！！")
