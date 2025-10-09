import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
from datetime import datetime
import pytz
import os
from selenium.webdriver.chrome.options import Options
from urllib.parse import urljoin

BASE = "https://www.patagonia.jp"

# 配置 Chrome 浏览器选项
options = webdriver.ChromeOptions()

options.add_argument("--headless")  # 无头模式，后台运行
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("start-maximized")          # 最大化窗口
options.add_argument("disable-infobars")
options.add_argument("--disable-extensions")
options.add_argument("--headless=new")
options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Safari/537.36")
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
    url = f"{BASE}/shop/web-specials/kids-baby?page=30"
    driver.get(url)

    WebDriverWait(driver, 100).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "div.product[data-pid] > product-tile"))
    )

    tiles = driver.find_elements(By.CSS_SELECTOR, "div.product[data-pid] > product-tile")
    print(f"Found tiles: {len(tiles)}")

    # ---------- 第 1 轮：只收集主列表信息 + qa_url（不跳走页面） ----------
    items = []
    for idx, tile in enumerate(tiles):
        try:
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
                # 价格取不到就跳过
                print(f"[skip-{idx}] price missing")
                continue

            discount_percent = round((list_price - sale_price) * 100 / list_price, 1)
            if discount_percent < 50:
                # 小于 30% 的不要
                print(f"[skip-{idx}] discount {discount_percent}% < 30%")
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
                "name": product_name,
                "original_price": int(list_price),
                "sale_price": int(sale_price),
                "discount_percent": discount_percent,
                "image_url": image_url,
                "product_link": product_link,
                "qa_url": qa_url,    # 第二轮再用
            })
        except Exception as e:
            print(f"[collect error-{idx}] {e}")

    # ---------- 第 2 轮：为每个 item 在新标签页里采集尺码 ----------
    for it in items:
        sizes = []
        qa_url = it.get("qa_url")
        if not qa_url:
            it["sizes"] = sizes
            continue
        try:
            # 在新标签打开，不刷新主列表页
            driver.execute_script("window.open(arguments[0], '_blank');", qa_url)
            driver.switch_to.window(driver.window_handles[-1])
            try:
                size_labels = WebDriverWait(driver, 10).until(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, "label.pdp-size-select"))
                )
                for lab in size_labels:
                    cls = lab.get_attribute("class") or ""
                    sz = lab.get_attribute("data-size") or lab.text.strip()
                    if sz and "is-disabled" not in cls:
                        sizes.append(sz)
            except TimeoutException:
                pass
        except Exception as e:
            print(f"[sizes error] {e}")
        finally:
            # 关闭新标签，回到主标签
            if len(driver.window_handles) > 1:
                driver.close()
                driver.switch_to.window(driver.window_handles[0])
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
