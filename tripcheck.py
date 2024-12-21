from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import requests
import time
from datetime import datetime
import pytz
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

def fetch_all_ticket_dates():
    url = "https://jp.trip.com/travel-guide/attraction/xi-an/shaanxi-history-museum-75684?curr=JPY&locale=ja-JP"
    driver.get(url)
    time.sleep(4)  # 等待页面加载

    ticket_data = []

    try:
        # 查找所有 "選択" 按钮
        select_buttons = WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "taro-view-core.hover_pointer"))
        )

        for button in select_buttons:
            try:
                # 点击按钮
                driver.execute_script("arguments[0].click();", button)
                time.sleep(3)  # 等待页面加载

                # 查找日期信息
                date_elements = WebDriverWait(driver, 10).until(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, "taro-text-core[style*='color: rgb(15, 41, 77)']"))
                )

                for date_element in date_elements:
                    date_text = date_element.text.strip()
                    ticket_data.append({
                        "date": date_text
                    })

                # 返回主页面
                driver.back()
                time.sleep(3)

            except Exception as e:
                print(f"Error processing button: {e}")

    except TimeoutException:
        print("Timeout while waiting for ticket buttons.")
    finally:
        driver.quit()

    return ticket_data

if __name__ == "__main__":
    tickets = fetch_all_ticket_dates()
    print("Fetched ticket data:", tickets)

    # 检查 27, 28, 29 是否有余票
    alert_dates = ["27", "28", "29"]
    available_dates = [ticket["date"] for ticket in tickets if ticket["date"] in alert_dates]

    if available_dates:
        send_wechat_message("Trip Ticket Alert", f"以下日期有余票: {', '.join(available_dates)}")
    else:
        print("No tickets available for specified dates.")

    utc_now = datetime.utcnow()
    jst = pytz.timezone("Asia/Tokyo")
    execution_time = utc_now.astimezone(jst).strftime("%Y-%m-%d %H:%M:%S")

    html_content = f"""
    <!DOCTYPE html>
    <html lang="ja">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Trip Ticket Availability</title>
        <style>
            body {{ font-family: Arial, sans-serif; }}
            .ticket {{ border: 1px solid #ddd; padding: 10px; margin: 10px 0; }}
            .timestamp {{ color: #555; font-size: 0.9em; margin-top: 10px; }}
        </style>
    </head>
    <body>
        <h1>Trip Ticket Availability</h1>
        <p class="timestamp">Generated on: {execution_time}</p>
    """

    for ticket in tickets:
        html_content += f"""
        <div class="ticket">
            <p>Date: {ticket['date']}</p>
        </div>
        """

    html_content += """
    </body>
    </html>
    """
