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
    url = "https://www.patagonia.jp/shop/web-specials?F25WO=&page=2"
    driver.get(url)
    time.sleep(10)  # 等待页面加载

    # 持续点击 "さらに見る" 按钮直到按钮不再显示
    scroll_count = 0
    while True:
        try:
            # 查找 "さらに見る" 按钮并点击
            # load_more_button = driver.find_element(
            # By.XPATH, "//div[@class='show-more']//button[contains(text(), 'さらに見る')]")
            # driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", load_more_button)
            load_more_button = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.XPATH, "//div[@class='show-more']//button[contains(text(), 'さらに見る')]"))
            )
            print(f"さらに見る clicked:{scroll_count}")
            driver.execute_script("arguments[0].click();", load_more_button)
            scroll_count = scroll_count + 1
            time.sleep(10)  # 等待加载新商品
        except (NoSuchElementException, TimeoutException):
            # 如果按钮不存在，则退出循环
            print("No more 'さらに見る' button, all items loaded.")
            break
    
# 1) 先取到所有商品卡片（兼容两种容器写法）
items = driver.find_elements(
    By.CSS_SELECTOR,
    "div.product[data-pid] product-tile .product-tile__inner, "
    "product-tile .product-tile__inner"
)

print(f"scroll :{scroll_count}, Total items loaded: {len(items)}")

products = []
for item in items:
    try:
        # ---- 折扣百分比（两种来源，先读徽章，再读 data- 属性）----
        discount_percent = None
        try:
            # 方式 A：徽章上的 “30% Off”
            badge = item.find_element(By.CSS_SELECTOR, ".badge--percent-off .sale-percent")
            # 一般是 '30'；保险起见做一下清洗
            discount_percent = int(float(badge.text.strip()))
        except Exception:
            # 方式 B：价格块上的 data-discount-percent="30.0"
            try:
                dp = item.find_element(By.CSS_SELECTOR, ".color-price.active")
                discount_percent = int(float(dp.get_attribute("data-discount-percent")))
            except Exception:
                pass

        if discount_percent is None:
            # 没拿到折扣就跳过
            continue

        # 只抓 > 30% 的商品
        if discount_percent <= 20:
            continue

        # ---- 原价 / 现价（优先读 content 属性，失败再读文本）----
        def _get_price(css_xpath, by="css"):
            try:
                if by == "css":
                    el = item.find_element(By.CSS_SELECTOR, css_xpath)
                else:
                    el = item.find_element(By.XPATH, css_xpath)
                val = el.get_attribute("content")
                if not val:
                    # 回退：从可见文本抽取数字
                    raw = el.text.strip()
                    # 去掉非数字字符
                    digits = "".join(ch for ch in raw if ch.isdigit())
                    val = digits or None
                return val
            except Exception:
                return None

        # 原价（删除线）
        original_price = (
            _get_price(".strike-through .value[itemprop='price']") or
            _get_price(".//span[contains(@class,'strike-through')]/span[contains(@class,'value')]", by="xpath")
        )

        # 折后价
        sale_price = (
            _get_price(".sales .value[itemprop='price']") or
            _get_price(".//span[contains(@class,'sales')]/span[contains(@class,'value')]", by="xpath")
        )

        # ---- 图片 URL（meta[itemprop='image'] 最稳）----
        image_url = None
        try:
            image_url = item.find_element(By.CSS_SELECTOR, "meta[itemprop='image']").get_attribute("content")
        except Exception:
            # 回退到 <picture> 里的第一个 source（若有需要）
            try:
                image_url = item.find_element(By.CSS_SELECTOR, "picture source").get_attribute("srcset").split()[0]
            except Exception:
                image_url = None

        # ---- 商品名称 ----
        product_name = None
        try:
            product_name = item.find_element(By.CSS_SELECTOR, ".product-tile__name").text.strip()
        except Exception:
            product_name = ""

        # ---- 商品链接（优先任一指向 /product/ 的 <a>；然后补全绝对地址）----
        product_link = None
        try:
            a = item.find_element(By.CSS_SELECTOR, "a[href*='/product/']")
            href = a.get_attribute("href")
            product_link = urljoin(BASE, href)
        except Exception:
            product_link = None

        # 组装
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


    # 在详细页面抓取尺寸信息
    for product in products:
        try:
            driver.get(product["product_link"])
            time.sleep(2)  # 等待页面加载

            # 等待尺寸信息加载完成
            size_elements = WebDriverWait(driver, 10).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "label.pdp-size-select"))
            )
            
            sizes = []
            for size_element in size_elements:
                size = size_element.get_attribute("data-size")
                if "is-disabled" in size_element.get_attribute("class"):
                    continue
                sizes.append(f"{size}")
            
            # 将尺寸信息添加到产品字典中
            product["sizes"] = sizes
        except (TimeoutException, NoSuchElementException) as e:
            print(f"Error fetching sizes for {product['name']}: {e}")

    driver.quit()

    utc_now = datetime.utcnow()
    jst = pytz.timezone("Asia/Tokyo")
    execution_time = utc_now.astimezone(jst).strftime("%Y-%m-%d %H:%M:%S")

    # 生成 HTML 内容
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
            <a href="{product['product_link']}" target="_blank">
                <img src="{product['image_url']}" alt="{product['name']}">
            </a>
            <p class="original-price">Original Price: {product['original_price']}</p>
            <p class="price">Sale Price: {product['sale_price']}</p>
            <p>Discount Percent: {product['discount_percent']}%</p>
            <p class="sizes">Sizes: {" | ".join(product['sizes'])}</p>
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
