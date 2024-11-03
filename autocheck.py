import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import os

# 配置 Chrome 浏览器选项
options = webdriver.ChromeOptions()
options.add_argument("--headless")  # 无头模式
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("start-maximized")          # 最大化窗口
options.add_argument("disable-infobars")
options.add_argument("--disable-extensions")
options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Safari/537.36")
options.add_argument("--headless")  # 无头模式，后台运行
driver_path = "/usr/local/bin/chromedriver-linux64/chromedriver"
service = Service(driver_path)
driver = webdriver.Chrome(service=service, options=options)

def fetch_discounted_products():
    url = "https://www.patagonia.jp/shop/web-specials"
    driver.get(url)
    time.sleep(4)  # 等待页面加载
    # print(driver.page_source)

    # # 记录当前商品数量
    # prev_item_count = 0
    # new_items_loaded = True
    # scroll_count = 0
    # # 滚动页面直到没有新的商品加载
    # while new_items_loaded:
    #     # 滚动页面到底部
    #     driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    #     time.sleep(5)  # 等待新商品加载
    #     scroll_count = scroll_count + 1
        
    #     # 重新获取商品元素，检查是否有新商品加载
    #     items = driver.find_elements(By.CLASS_NAME, "product-tile__inner")
    #     current_item_count = len(items)
        
    #     # 如果商品数量不再增加，则停止滚动
    #     if current_item_count == prev_item_count:
    #         new_items_loaded = False
    #     else:
    #         prev_item_count = current_item_count  # 更新商品计数

    # 持续点击 "さらに見る" 按钮直到按钮不再显示
    scroll_count = 0
    while True:
        try:
            # 查找 "さらに見る" 按钮并点击
            # load_more_button = driver.find_element(
            # By.XPATH, "//div[@class='show-more']//button[contains(text(), 'さらに見る')]")
            # driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", load_more_button)
            load_more_button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//div[@class='show-more']//button[contains(text(), 'さらに見る')]"))
            )
            print(f"さらに見る clicked:{scroll_count}")
            driver.execute_script("arguments[0].click();", load_more_button)
            scroll_count = scroll_count + 1
            time.sleep(3)  # 等待加载新商品
        except (NoSuchElementException, TimeoutException):
            # 如果按钮不存在，则退出循环
            print("No more 'さらに見る' button, all items loaded.")
            break

    items = driver.find_elements(By.CLASS_NAME, "product-tile__inner")
    # 输出最终获取到的商品数量
    print(f"scroll :{scroll_count}, Total items loaded: {len(items)}")
    
    products = []
    for item in items:
        try:
            # 折扣百分比
            discount_element = item.find_element(By.CLASS_NAME, "sale-percent")
            discount_percent = int(discount_element.text)
            
            # 只选取折扣超过30%的商品
            if discount_percent > 30:
                # 原价
                original_price = item.find_element(By.CLASS_NAME, "strike-through").text.strip()
                
                # 折后价
                sale_price = item.find_element(By.CLASS_NAME, "sales").text.strip()
                
                # 图片 URL
                image_url = item.find_element(By.CSS_SELECTOR, "meta[itemprop='image']").get_attribute("content")
                
                # 产品名称
                product_name = item.find_element(By.CLASS_NAME, "product-tile__name").text.strip()

                # 将信息添加到列表中
                products.append({
                    "name": product_name,
                    "original_price": original_price,
                    "sale_price": sale_price,
                    "discount_percent": discount_percent,
                    "image_url": image_url
                })
        except Exception as e:
            print(f"Error processing item: {e}")
    
    driver.quit()

    html_content = """
    <!DOCTYPE html>
    <html lang="ja">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Patagonia Discounted Products</title>
        <style>
            body { font-family: Arial, sans-serif; }
            .product { border: 1px solid #ddd; padding: 10px; margin: 10px 0; }
            .product img { max-width: 200px; }
            .product h2 { font-size: 1.2em; color: #333; }
            .price { font-weight: bold; color: #d9534f; }
            .original-price { text-decoration: line-through; color: #888; }
        </style>
    </head>
    <body>
        <h1>Discounted Products</h1>
    """

    for product in products:
        html_content += f"""
        <div class="product">
            <h2>{product['name']}</h2>
            <img src="{product['image_url']}" alt="{product['name']}">
            <p class="original-price">Original Price: {product['original_price']}</p>
            <p class="price">Sale Price: {product['sale_price']}</p>
            <p>Discount Percent: {product['discount_percent']}%</p>
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
        print("New Gist created.")

# 获取商品数据并上传到 Gist
html_content = fetch_discounted_products()
upload_to_gist(html_content)
