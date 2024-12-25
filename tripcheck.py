import base64
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
import json

# 配置 Chrome 浏览器选项
options = webdriver.ChromeOptions()
options.add_argument("--headless")  # 无头模式
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("start-maximized")          # 最大化窗口
options.add_argument("disable-infobars")
options.add_argument("--disable-extensions")
options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Safari/537.36")
driver_path = "/usr/local/bin/chromedriver-linux64/chromedriver"
service = Service(driver_path)
driver = webdriver.Chrome(service=service, options=options)

def load_previous_state():
    GIST_TOKEN = os.getenv("GIST_TOKEN")
    headers = {"Authorization": f"token {GIST_TOKEN}"}
    url = "https://api.github.com/gists"

    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        gists = response.json()
        for gist in gists:
            try:
                # 获取文件内容
                ticket_file = gist.get("files", {}).get("ticket_state.json", {})
                state_content = ticket_file.get("content", "")  # 默认内容为空的 JSON 字符串
                
                # 尝试加载 JSON 内容
                return json.loads(state_content)
            except (json.JSONDecodeError, AttributeError) as e:
                # 如果内容不是合法的 JSON 或其他异常，返回空字典
                return {}
    return {}

def save_current_state(state):
    GIST_TOKEN = os.getenv("GIST_TOKEN")
    headers = {"Authorization": f"token {GIST_TOKEN}"}
    url = "https://api.github.com/gists"
    gist_id = None

    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        gists = response.json()
        for gist in gists:
            if gist["description"] == "Ticket State":
                gist_id = gist["id"]
                break

    payload = {
        "description": "Ticket State",
        "files": {
            "ticket_state.json": {
                "content" : json.dumps(state)
            }
        }
    }

    if gist_id:
        requests.patch(f"https://api.github.com/gists/{gist_id}", headers=headers, json=payload)
    else:
        requests.post(url, headers=headers, json=payload)

def send_wechat_message(title, content):
    send_key = os.getenv("WECHAT_SENDKEY")
    url = f"https://sctapi.ftqq.com/{send_key}.send"
    data = {"title": title, "desp": content}
    response = requests.post(url, data=data)
    if response.status_code == 200:
        print("消息发送成功")
    else:
        print("消息发送失败:", response.text)

def fetch_all_ticket_dates():
    url = "https://us.trip.com/travel-guide/attraction/xi-an/shaanxi-history-museum-75684"
    driver.get(url)
    time.sleep(4)  # 等待页面加载

    ticket_data = []
    screenshots = []

    try:
        select_buttons = WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "taro-view-core.hover_pointer"))
        )

        for index, button in enumerate(select_buttons):
            try:
                driver.execute_script("arguments[0].click();", button)
                time.sleep(3)

                # 截取页面截图
                screenshot_path = f"screenshot_{index}.png"
                driver.save_screenshot(screenshot_path)
                with open(screenshot_path, "rb") as image_file:
                    base64_image = base64.b64encode(image_file.read()).decode('utf-8')
                    screenshots.append(base64_image)

                date_elements = WebDriverWait(driver, 10).until(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, "taro-text-core:not([style*='color: rgb(206, 210, 217)'])"))
                )

                for date_element in date_elements:
                    date_text = date_element.text.strip()
                    ticket_data.append({"date": date_text})

                driver.back()
                time.sleep(3)

            except Exception as e:
                print(f"Error processing button: {e}")

    except TimeoutException:
        print("Timeout while waiting for ticket buttons.")
    finally:
        driver.quit()

    return ticket_data, screenshots

def upload_to_gist(content):
    GIST_TOKEN = os.getenv("GIST_TOKEN")
    headers = {"Authorization": f"token {GIST_TOKEN}"}

    # 检查是否已有 Gist
    gist_id = None
    response = requests.get("https://api.github.com/gists", headers=headers)
    if response.status_code == 200:
        gists = response.json()
        for gist in gists:
            if gist["description"] == "Trip Ticket Availability":
                gist_id = gist["id"]
                break

    # 创建或更新 Gist
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
        requests.post(url, headers=headers, json=payload)
        print("New Gist created.")

if __name__ == "__main__":
    previous_state = load_previous_state()
    tickets, screenshots = fetch_all_ticket_dates()
    print("Fetched ticket data:", tickets)

    alert_dates = ["27", "28", "29"]
    available_dates = [ticket["date"] for ticket in tickets if ticket["date"] in alert_dates]

    print(available_dates)
    print("------------------------------")
    print(previous_state.get("available_dates"))
    print("------------------------------")
    print(available_dates != previous_state.get("available_dates"))
    if available_dates != previous_state.get("available_dates"):
        if available_dates:
            send_wechat_message("Trip Ticket Alert", f"以下日期有余票: {', '.join(available_dates)}")
        save_current_state({"available_dates": available_dates})
    else:
        print("No changes in ticket availability. No message sent.")

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

    html_content += "<h2>Screenshots</h2>"
    for screenshot in screenshots:
        html_content += f"""
        <div class="screenshot">
            <img src="data:image/png;base64,{screenshot}" alt="Screenshot" />
        </div>
        """

    html_content += """
    </body>
    </html>
    """

    upload_to_gist(html_content)
