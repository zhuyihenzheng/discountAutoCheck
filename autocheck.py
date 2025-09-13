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
    url = f"{BASE}/shop/web-specials/kids-baby?page=10"
    driver.get(url)

    # 等待出现任一 product-tile
    WebDriverWait(driver, 100).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "div.product[data-pid] > product-tile"))
    )

    # 每个商品卡：div.product[data-pid] 下的 <product-tile>
    tiles = driver.find_elements(By.CSS_SELECTOR, "div.product[data-pid] > product-tile")
    print(f"Found tiles: {len(tiles)}")

    products = []
    for tile in tiles:
        try:
            # --- 名称
            name_el = _first_or_none(tile, By.CSS_SELECTOR, ".pdp-link .product-tile__name")
            product_name = name_el.text.strip() if name_el else ""

            # --- 链接
            link_el = _first_or_none(tile, By.CSS_SELECTOR, ".pdp-link a.link[itemprop='url']")
            product_link = urljoin(BASE, link_el.get_attribute("href")) if link_el else None

            # --- 价格（优先从 <product-tile-pricing> 的属性拿）
            ptp = _first_or_none(tile, By.CSS_SELECTOR, "product-tile-pricing")
            sale_price = _num(ptp.get_attribute("sale-price")) if ptp else None
            list_price = _num(ptp.get_attribute("list-price")) if ptp else None

            # 兜底：从当前激活色卡按钮 data-* 里拿
            if sale_price is None or list_price is None:
                active_swatch = _first_or_none(
                    tile, By.CSS_SELECTOR,
                    ".product-tile__pagination .product-tile__bullet .product-tile__colors.default.active"
                )
                if active_swatch:
                    sale_price = sale_price or _num(active_swatch.get_attribute("data-sale-price"))
                    list_price = list_price or _num(active_swatch.get_attribute("data-list-price"))

            if not sale_price or not list_price or list_price <= 0:
                # 价格异常则跳过
                continue

            discount_percent = round((list_price - sale_price) * 100 / list_price, 1)
            if discount_percent <= 30:
                # 不满足你设定的 30% 阈值
                continue

            # --- 图片（优先当前激活颜色的 meta[itemprop=image]）
            img_meta = _first_or_none(
                tile, By.CSS_SELECTOR,
                ".product-tile__image.default.active meta[itemprop='image']"
            ) or _first_or_none(
                tile, By.CSS_SELECTOR,
                ".product-tile__cover meta[itemprop='image']"
            )
            image_url = img_meta.get_attribute("content") if img_meta else None

            # --- Quick Add 取尺码（无需进详情）
            qa_btn = _first_or_none(
                tile, By.CSS_SELECTOR,
                ".product-tile__quickadd-container .tile-quickadd-btn[data-url]"
            )
            sizes = []
            if qa_btn:
                qa_url = urljoin(BASE, qa_btn.get_attribute("data-url"))
                # 用 Selenium 打开这个片段路由，里面会有 label.pdp-size-select
                current_url = driver.current_url
                driver.get(qa_url)
                try:
                    size_labels = WebDriverWait(driver, 10).until(
                        EC.presence_of_all_elements_located((By.CSS_SELECTOR, "label.pdp-size-select"))
                    )
                    for lab in size_labels:
                        # 可用：不含 is-disabled
                        cls = lab.get_attribute("class") or ""
                        sz = lab.get_attribute("data-size") or lab.text.strip()
                        if sz and "is-disabled" not in cls:
                            sizes.append(sz)
                except Exception:
                    pass
                finally:
                    # 回到列表页继续处理下一卡
                    driver.get(current_url)
                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "div.product[data-pid] > product-tile"))
                    )

            products.append({
                "name": product_name,
                "original_price": int(list_price),
                "sale_price": int(sale_price),
                "discount_percent": discount_percent,
                "image_url": image_url,
                "product_link": product_link,
                "sizes": sizes or []
            })

        except Exception as e:
            print(f"[tile error] {e}")

    # --- 生成 HTML（与你原来一致）
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

    for p in products:
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
