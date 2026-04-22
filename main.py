from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import os, json
from datetime import datetime
import anthropic
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)

LINE_TOKEN = os.environ.get("LINE_TOKEN")
LINE_SECRET = os.environ.get("LINE_SECRET")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY")
SHEET_ID = os.environ.get("SHEET_ID")
GOOGLE_CREDS = os.environ.get("GOOGLE_CREDS")

line_bot_api = LineBotApi(LINE_TOKEN)
handler = WebhookHandler(LINE_SECRET)

def get_sheet():
    creds_dict = json.loads(GOOGLE_CREDS)
    creds = Credentials.from_service_account_info(creds_dict, scopes=[
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ])
    gc = gspread.authorize(creds)
    return gc.open_by_key(SHEET_ID).sheet1

def save_message(user_id, text):
    sheet = get_sheet()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    sheet.append_row([now, user_id, text])

def get_today_messages():
    sheet = get_sheet()
    today = datetime.now().strftime("%Y-%m-%d")
    rows = sheet.get_all_values()
    return [r for r in rows if r and r[0].startswith(today)]

def generate_summary(messages):
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    content = "\n".join([f"{r[0]} {r[1]}: {r[2]}" for r in messages])
    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1000,
        messages=[{
            "role": "user",
            "content": f"以下是今天的群組對話記錄，請整理成繁體中文摘要，包含：待辦事項、重要討論、結論。格式要清楚易讀。\n\n{content}"
        }]
    )
    return response.content[0].text

@app.route("/webhook", methods=["POST"])
def webhook():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text
    user_id = event.source.user_id
    
    # 儲存訊息
    save_message(user_id, text)
    
    # 指令：發送摘要（輸入「摘要」觸發）
    if text == "摘要":
        msgs = get_today_messages()
        if not msgs:
            reply = "今天還沒有對話記錄！"
        else:
            reply = generate_summary(msgs)
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply)
        )

@app.route("/")
def index():
    return "Bot is running!"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
