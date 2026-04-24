from flask import Flask, request, abort, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import os, json
from datetime import datetime, timezone, timedelta
import anthropic
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)

LINE_TOKEN = os.environ.get("LINE_TOKEN")
LINE_SECRET = os.environ.get("LINE_SECRET")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY")
SHEET_ID = os.environ.get("SHEET_ID")
GOOGLE_CREDS = os.environ.get("GOOGLE_CREDS")
USER_NAMES = {
    "Ue155232bc37f6ad93a4789c6f101f019": "闆娘",
    "Ub4d3e2422af273265c13fb865eae04e7": "闆娘",
    "U4f56a471c0b7e90d7dd3ceb2b1293d59": "Apple",
}
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
    now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    display_name = USER_NAMES.get(user_id, user_id)
    sheet.append_row([now, display_name, text])

def get_today_messages():
    sheet = get_sheet()
    today = datetime.now().strftime("%Y-%m-%d")
    rows = sheet.get_all_values()
    return [r for r in rows if r and r[0].startswith(today)]

def generate_summary(messages):
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    content = "\n".join([f"{r[0]} {USER_NAMES.get(r[1], r[1])}: {r[2]}" for r in messages])
    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=6000,
        messages=[{
            "role": "user",
            "content": f"""以下是今天的Line群組對話記錄，請依照以下格式整理成繁體中文摘要：

【✨ 今日重點】
（用編號列出3-5個今天最重要的事項，每項一行，格式：數字. 主題：簡述）

【🧩 主要討論主題】
（依主題分類，每個主題前加 ⭕️，下面用編號列出討論內容細節）

【✅ 待跟進事項】
（依人名分類，每人前加 emoji，下面用 - 列出待辦事項）

【⚠️ 風險與提醒】
（列出需要注意的風險或提醒事項，用 - 列出）

【💛 一句小結】
（用溫暖鼓勵的語氣寫一句今日總結，可加表情符號）

注意：
- 人名請保留原本群組中的稱呼
- 語氣親切自然
- 若某區塊沒有內容可省略

對話記錄如下：
{content}"""
        }]
    )
    return response.content[0].text

@app.route("/webhook", methods=["POST"])
def webhook():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return jsonify({"status": "ok"}), 200

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text
    user_id = event.source.user_id
    save_message(user_id, text)
    if text == "群組ID":
        source = event.source
        if hasattr(source, 'group_id'):
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"群組ID：{source.group_id}")
            )
        return
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
    return "Bot is running!", 200
@app.route("/send_summary", methods=["POST", "GET"])
def send_summary():
    msgs = get_today_messages()
    if not msgs:
        summary = "今天還沒有對話記錄！"
    else:
        summary = generate_summary(msgs)
    
    # 取得群組ID（需要填入你的群組ID）
    group_id = os.environ.get("GROUP_ID")
    line_bot_api.push_message(
        group_id,
        TextSendMessage(text=summary)
    )
    return jsonify({"status": "ok"}), 200
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
