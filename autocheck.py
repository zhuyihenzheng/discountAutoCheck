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

# 配置 Chrome 浏览器选项
options = webdriver.ChromeOptions()

options.add_argument("--headless")  # 无头模式，后台运行
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("start-maximized")          # 最大化窗口
options.add_argument("disable-infobars")
options.add_argument("--disable-extensions")
options.add_argument("--headless=new")
#options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Safari/537.36")
#driver_path = "/usr/local/bin/chromedriver-linux64/chromedriver"
#service = Service(driver_path)
#driver = webdriver.Chrome(service=service, options=options)
driver = webdriver.Chrome(options=options)


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
    url = "https://www.patagonia.jp/shop/web-specials"
    driver.get(url)

    # 等待商品卡片出现（比固定sleep稳）
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_all_elements_located(
                (By.CSS_SELECTOR, "product-tile .product-tile__inner, div.product[data-pid] product-tile .product-tile__inner")
            )
        )
    except TimeoutException:
        print("初次加载超时，页面无商品卡片")
        return "<html><body><p>No items loaded</p></body></html>"

    # 持续点击 "さらに見る"
    scroll_count = 0
    while True:
        try:
            load_more_button = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, "//div[@class='show-more']//button[contains(text(), 'さらに見る')]"))
            )
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", load_more_button)
            driver.execute_script("arguments[0].click();", load_more_button)
            scroll_count += 1
            # 等到新卡片追加（或简单等一下）
            time.sleep(2)
        except (NoSuchElementException, TimeoutException):
            print("No more 'さらに見る' button, all items loaded.")
            break

    # 1) 取商品卡片（兼容两种容器）
    items = driver.find_elements(
        By.CSS_SELECTOR,
        "div.product[data-pid] product-tile .product-tile__inner, product-tile .product-tile__inner"
    )
    print(f"scroll :{scroll_count}, Total items loaded: {len(items)}")

    products = []
    for item in items:
        try:
            # ---- 折扣百分比：徽章或 data-discount-percent ----
            discount_percent = None
            try:
                badge = item.find_element(By.CSS_SELECTOR, ".badge--percent-off .sale-percent")
                discount_percent = int(float(badge.text.strip()))
            except Exception:
                try:
                    dp = item.find_element(By.CSS_SELECTOR, ".color-price.active")
                    discount_percent = int(float(dp.get_attribute("data-discount-percent")))
                except Exception:
                    pass

            if discount_percent is None or discount_percent <= 30:
                continue  # 只要 >30%

            # ---- 价格读取：优先 content，失败再读文本 ----
            def _get_price(selector, by="css"):
                try:
                    el = item.find_element(By.CSS_SELECTOR, selector) if by == "css" else item.find_element(By.XPATH, selector)
                    val = el.get_attribute("content")
                    if not val:
                        raw = el.text.strip()
                        digits = "".join(ch for ch in raw if ch.isdigit())
                        val = digits or None
                    return val
                except Exception:
                    return None

            original_price = (
                _get_price(".strike-through .value[itemprop='price']") or
                _get_price(".//span[contains(@class,'strike-through')]/span[contains(@class,'value')]", by="xpath")
            )
            sale_price = (
                _get_price(".sales .value[itemprop='price']") or
                _get_price(".//span[contains(@class,'sales')]/span[contains(@class,'value')]", by="xpath")
            )

            # ---- 图片 ----
            image_url = None
            try:
                image_url = item.find_element(By.CSS_SELECTOR, "meta[itemprop='image']").get_attribute("content")
            except Exception:
                try:
                    image_url = item.find_element(By.CSS_SELECTOR, "picture source").get_attribute("srcset").split()[0]
                except Exception:
                    image_url = None

            # ---- 名称 ----
            try:
                product_name = item.find_element(By.CSS_SELECTOR, ".product-tile__name").text.strip()
            except Exception:
                product_name = ""

            # ---- 链接 ----
            product_link = None
            try:
                a = item.find_element(By.CSS_SELECTOR, "a[href*='/product/']")
                href = a.get_attribute("href")
                product_link = urljoin(BASE, href)
            except Exception:
                product_link = None

            products.append({
                "name": product_name,
                "original_price": original_price,
                "sale_price": sale_price,
                "discount_percent": discount_percent,
                "image_url": image_url,
                "product_link": product_link,
                "sizes": []
            })

        except Exception as e:
            print(f"Error processing item: {e}")

    # 2) 进入详情页抓尺寸（放在商品循环之后，作用于 products）
    for product in products:
        if not product["product_link"]:
            continue
        try:
            driver.get(product["product_link"])
            # 等尺寸标签
            size_elements = WebDriverWait(driver, 10).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "label.pdp-size-select"))
            )
            sizes = []
            for size_element in size_elements:
                size = size_element.get_attribute("data-size")
                if not size:
                    continue
                # 跳过不可用
                if "is-disabled" in size_element.get_attribute("class"):
                    continue
                sizes.append(size)
            product["sizes"] = sizes
        except (TimeoutException, NoSuchElementException) as e:
            print(f"Error fetching sizes for {product.get('name','')}: {e}")

    # 3) 生成 HTML
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

    for product in products:
        html_content += f"""
        <div class="product">
            <h2>{product['name']}</h2>
            <a href="{product['product_link'] or '#'}" target="_blank">
                <img src="{product['image_url'] or ''}" alt="{product['name']}">
            </a>
            <p class="original-price">Original Price: {product['original_price'] or ''}</p>
            <p class="price">Sale Price: {product['sale_price'] or ''}</p>
            <p>Discount Percent: {product['discount_percent']}%</p>
            <p class="sizes">Sizes: {" | ".join(product['sizes']) if product['sizes'] else "-"}</p>
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
