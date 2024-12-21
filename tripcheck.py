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

                # 查找日期和余票信息
                date_elements = WebDriverWait(driver, 10).until(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, "taro-text-core[style*='color: rgb(6, 174, 189)']"))
                )
                ticket_elements = driver.find_elements(By.CSS_SELECTOR, "taro-text-core[style*='color: rgb(245, 89, 74)']")

                for date, ticket in zip(date_elements, ticket_elements):
                    ticket_data.append({
                        "date": date.text.strip(),
                        "remaining_tickets": ticket.text.strip()
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

def upload_to_gist(content):
    GIST_TOKEN = os.getenv("GIST_TOKEN")
    headers = {"Authorization": f"token {GIST_TOKEN}"}

    gist_id = None
    response = requests.get("https://api.github.com/gists", headers=headers)
    if response.status_code == 200:
        gists = response.json()
        for gist in gists:
            if gist["description"] == "Trip Ticket Availability":
                gist_id = gist["id"]
                break

    if gist_id:
        url = f"https://api.github.com/gists/{gist_id}"
        payload = {
            "description": "Trip Ticket Availability",
            "files": {
                "trip_tickets.html": {
                    "content": content
                }
            }
        }
        requests.patch(url, headers=headers, json=payload)
        print("Gist updated.")
    else:
        url = "https://api.github.com/gists"
        payload = {
            "description": "Trip Ticket Availability",
            "public": True,
            "files": {
                "trip_tickets.html": {
                    "content": content
                }
            }
        }
        response = requests.post(url, headers=headers, json=payload)
        print("New Gist created: ", response.json().get("html_url"))

if __name__ == "__main__":
    tickets = fetch_all_ticket_dates()
    print("Fetched ticket data:", tickets)

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
            <p>Remaining Tickets: {ticket['remaining_tickets']}</p>
        </div>
        """

    html_content += """
    </body>
    </html>
    """

    upload_to_gist(html_content)
    #send_wechat_message("Trip Ticket Update", "余票信息已更新！")
