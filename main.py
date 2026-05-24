from flask import Flask, request, abort, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import os, json, re
from datetime import datetime, timezone, timedelta
import anthropic
import gspread
import requests
from google.oauth2.service_account import Credentials

app = Flask(__name__)

LINE_TOKEN = os.environ.get("LINE_TOKEN")
LINE_SECRET = os.environ.get("LINE_SECRET")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY")
SHEET_ID = os.environ.get("SHEET_ID")
GOOGLE_CREDS = os.environ.get("GOOGLE_CREDS")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
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
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    rows = sheet.get_all_values()
    return [r for r in rows if r and r[0].startswith(today)]

def generate_summary(messages):
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    content = "\n".join([f"{r[0]} {USER_NAMES.get(r[1], r[1])}: {r[2]}" for r in messages[-500:]])

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=3000,
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

# ===== ga02. studio 指令處理 =====

STUDIO_TRIGGERS = ["上架", "下架", "列表", "查商品", "目前在賣", "新增 reel", "新 reel", "新增故事", "新故事", "新團購", "結團了"]

# 文字觸發 mention（沒 LINE 原生 @ 但打這些字也算 tag）
BOT_MENTION_KEYWORDS = ["@嘎秘書", "@嘎", "@ga", "嘎秘書", "ga秘書", "ga 秘書"]

# Bot user ID 快取（LINE API 取一次）
_BOT_USER_ID = None

def get_bot_user_id():
    global _BOT_USER_ID
    if _BOT_USER_ID is None:
        try:
            info = line_bot_api.get_bot_info()
            _BOT_USER_ID = info.user_id
        except Exception:
            _BOT_USER_ID = ""
    return _BOT_USER_ID or None

def is_bot_addressed(event, text):
    """訊息是否在「叫」嘎秘書 ─ 兩種方式都認：
    1. LINE 原生 @ mention（跳選單選 bot）
    2. 文字含「嘎秘書」「ga 秘書」之類關鍵字
    """
    mention = getattr(event.message, "mention", None)
    if mention is not None:
        bot_id = get_bot_user_id()
        mentionees = getattr(mention, "mentionees", []) or []
        if bot_id:
            for m in mentionees:
                if getattr(m, "user_id", None) == bot_id:
                    return True
    lower = text.lower()
    return any(k.lower() in lower for k in BOT_MENTION_KEYWORDS)

def looks_like_studio_command(text):
    """快速判斷文字是否可能是 studio 指令"""
    return any(t in text.lower() for t in [t.lower() for t in STUDIO_TRIGGERS])

def supabase_request(method, path, data=None):
    """打 Supabase REST API"""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    try:
        return requests.request(method, url, headers=headers, json=data, timeout=10)
    except Exception as e:
        return None

def parse_studio_command(text):
    """用 Claude 解析自然語言 studio 指令"""
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    prompt = f"""你是 LINE 群組秘書，使用者要管理一個團購集合頁。判斷以下訊息是不是「上架商品」「下架商品」「查列表」「新增 IG Reel」「新增故事」之一。

今天日期：{today}

回傳 JSON 之一（純 JSON 不要 markdown）：

- 上架商品：
{{"action": "add_product", "name": "...", "start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD", "discount_code": "或 null", "discount_label": "例如 9折 或 null", "purchase_url": "...", "is_always_open": false}}

- 下架商品：
{{"action": "archive_product", "name": "..."}}

- 列出目前商品：
{{"action": "list_products"}}

- 新 Reel：
{{"action": "add_reel", "title": "...", "ig_url": "https://...", "description": "或 null"}}

- 新故事：
{{"action": "add_story", "era": "或 null", "title": "...", "description": "..."}}

- 都不像：
{{"action": "none"}}

- 缺資訊（例如要上架但沒結團日）：
{{"action": "incomplete", "type": "add_product", "missing": ["end_date"], "asking": "請補上結團日，例如：結團 6/15"}}

注意：
- 日期把「6/1」「6月1日」轉成 YYYY-MM-DD（年份用今年或推算合理年）
- 如果寫「常駐」「永久」就設 is_always_open: true、start/end 為 null
- 訊息：
{text}"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except Exception:
        return {"action": "none"}

def execute_studio_action(cmd):
    """執行 parsed 指令，回傳給 LINE 的訊息"""
    action = cmd.get("action")

    if action == "incomplete":
        return f"📝 還差一點點\n{cmd.get('asking', '請補上缺少的資訊')}"

    if action == "add_product":
        name = cmd.get("name", "").strip()
        if not name:
            return "✗ 缺商品名稱"
        payload = {
            "name": name,
            "start_date": cmd.get("start_date"),
            "end_date": cmd.get("end_date"),
            "discount_code": cmd.get("discount_code"),
            "discount_label": cmd.get("discount_label"),
            "purchase_url": cmd.get("purchase_url", ""),
            "is_always_open": bool(cmd.get("is_always_open", False)),
            "status": "active",
            "sort_order": 999,
        }
        res = supabase_request("POST", "products", payload)
        if res is None:
            return "✗ 連不上資料庫"
        if res.ok:
            label = "常駐" if payload["is_always_open"] else f"{payload['start_date']} → {payload['end_date']}"
            return f"✓ 已上架「{name}」\n{label}\n👉 https://ga02-studio.vercel.app"
        return f"✗ 上架失敗：{res.text[:120]}"

    if action == "archive_product":
        name = cmd.get("name", "").strip()
        if not name:
            return "✗ 沒指定要下架哪個商品"
        find = supabase_request("GET", f"products?name=ilike.*{name}*&status=eq.active&select=id,name")
        if find is None or not find.ok:
            return "✗ 連不上資料庫"
        items = find.json()
        if not items:
            return f"✗ 找不到上架中的「{name}」"
        target = items[0]
        res = supabase_request("PATCH", f"products?id=eq.{target['id']}", {"status": "archived"})
        if res and res.ok:
            return f"✓ 已下架「{target['name']}」"
        return f"✗ 下架失敗"

    if action == "list_products":
        res = supabase_request("GET", "products?status=eq.active&select=name,end_date,is_always_open&order=sort_order")
        if res is None or not res.ok:
            return "✗ 連不上資料庫"
        items = res.json()
        if not items:
            return "📦 目前沒有上架中商品"
        lines = ["📦 目前上架中："]
        for p in items:
            if p.get("is_always_open"):
                lines.append(f"• {p['name']}（常駐）")
            else:
                lines.append(f"• {p['name']}（結團 {p.get('end_date', '?')})")
        return "\n".join(lines)

    if action == "add_reel":
        title = cmd.get("title", "").strip()
        ig_url = cmd.get("ig_url", "").replace("/reels/", "/reel/")
        if not title or not ig_url:
            return "✗ 缺 Reel 標題或連結"
        payload = {
            "title": title,
            "description": cmd.get("description"),
            "ig_url": ig_url,
            "status": "active",
            "sort_order": 999,
        }
        res = supabase_request("POST", "reels", payload)
        if res and res.ok:
            return f"✓ 已新增 Reel「{title}」\n（縮圖會自動抓，幾秒後刷新前台查看）"
        return f"✗ 新增 Reel 失敗"

    if action == "add_story":
        title = cmd.get("title", "").strip()
        if not title:
            return "✗ 缺故事標題"
        payload = {
            "title": title,
            "era": cmd.get("era"),
            "description": cmd.get("description"),
            "status": "active",
            "sort_order": 999,
        }
        res = supabase_request("POST", "stories", payload)
        if res and res.ok:
            return f"✓ 已新增故事「{title}」"
        return f"✗ 新增故事失敗"

    return None


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
    source = event.source
    group_id = getattr(source, 'group_id', None)

    # 「群組ID」指令在任何群組都可用，方便取得新群組 ID
    if text == "群組ID":
        if group_id:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"群組ID：{group_id}")
            )
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="這不是群組訊息")
            )
        return

    # 其他功能（存 Google Sheet、摘要）只在白名單群組執行
    apple_group_id = os.environ.get("GROUP_ID")
    if group_id != apple_group_id:
        return

    save_message(user_id, text)

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
        return

    # ga02. studio 指令處理：tag 嘎秘書就送 Claude 解析（不做關鍵字過濾）
    if is_bot_addressed(event, text):
        try:
            cmd = parse_studio_command(text)
            action = cmd.get("action")
            if action and action != "none":
                reply = execute_studio_action(cmd)
                if reply:
                    line_bot_api.reply_message(
                        event.reply_token,
                        TextSendMessage(text=reply)
                    )
            else:
                # 看不懂指令，給友善提示
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=(
                        "嗨～我看不太懂這個指令 🙂\n\n"
                        "可以試試：\n"
                        "• 列表（看目前在賣什麼）\n"
                        "• 上架商品（含名稱/開團/結團/連結）\n"
                        "• 下架 商品名\n"
                        "• 新 reel（含標題/連結）\n"
                        "• 新故事（含標題/描述）"
                    ))
                )
        except Exception as e:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"✗ 指令處理失敗：{str(e)[:120]}")
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
