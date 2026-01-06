import os
import re
import time
import json
import asyncio
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    PicklePersistence,
    filters,
)

# =========================
# Config
# =========================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise SystemExit("Thiếu BOT_TOKEN trong .env")

POLL_MINUTES = int(os.getenv("POLL_MINUTES", "5"))
TZ_VN = ZoneInfo("Asia/Ho_Chi_Minh")

API_URL = "https://tramavandon.com/api/spx.php"
API_HEADERS = {
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
    "content-type": "application/json",
    "referer": "https://tramavandon.com/spx/",
}

# Cache ngắn để tránh spam endpoint nếu nhiều thao tác liên tiếp
CACHE_TTL_SECONDS = 20
_cache: Dict[str, Tuple[float, dict]] = {}  # tracking_id -> (ts, payload)

SPX_RE = re.compile(r"^(SPXVN[0-9A-Z]+)$", re.IGNORECASE)

# =========================
# Milestone translate
# =========================
MILESTONE_VI_BY_CODE = {
    1: "Đang chuẩn bị giao",
    5: "Đang vận chuyển",
    6: "Đang giao hàng",
    8: "Đã giao thành công",
}

STATUS_ICON_BY_CODE = {
    1: "⏳",  # Preparing to ship
    5: "🚚",  # In transit
    6: "🛵",  # Out for delivery
    8: "✅",  # Delivered
}

def status_icon(milestone_code: Optional[int], milestone_name: str) -> str:
    if milestone_code in STATUS_ICON_BY_CODE:
        return STATUS_ICON_BY_CODE[milestone_code]
    name = (milestone_name or "").lower()
    if "return" in name:
        return "↩️"
    if "cancel" in name:
        return "🛑"
    if "fail" in name or "exception" in name:
        return "⚠️"
    return "📦"


def translate_milestone(milestone_code: Optional[int], milestone_name: str) -> str:
    if milestone_code in MILESTONE_VI_BY_CODE:
        return MILESTONE_VI_BY_CODE[milestone_code]
    name = (milestone_name or "").lower()
    if "return" in name:
        return "Đang hoàn hàng"
    if "cancel" in name:
        return "Đã huỷ"
    if "fail" in name or "exception" in name:
        return "Có sự cố"
    return milestone_name or "Không rõ trạng thái"


# =========================
# Helpers
# =========================
def esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def fmt_time_vn(epoch_seconds: int) -> str:
    dt = datetime.fromtimestamp(int(epoch_seconds), tz=TZ_VN)
    return dt.strftime("%H:%M:%S %d/%m/%Y")

def get_nested(d: dict, *keys, default="") -> str:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    if cur is None:
        return default
    return str(cur)

def has_text(s: str) -> bool:
    return bool((s or "").strip())

def pick_latest_record(records: List[dict]) -> dict:
    # latest theo actual_time
    return max(records, key=lambda r: int(r.get("actual_time") or 0))

def dedupe_records(records: List[dict]) -> List[dict]:
    # key = (tracking_code, actual_time), giữ record "giàu data" hơn
    def score(r: dict) -> int:
        sc = 0
        if has_text(get_nested(r, "description")): sc += 1
        if has_text(get_nested(r, "buyer_description")): sc += 2
        if has_text(get_nested(r, "current_location", "full_address")): sc += 2
        if has_text(get_nested(r, "next_location", "full_address")): sc += 2
        return sc

    best: Dict[Tuple[str, int], dict] = {}
    for r in records:
        code = get_nested(r, "tracking_code")
        ts = int(r.get("actual_time") or 0)
        key = (code, ts)
        if key not in best or score(r) > score(best[key]):
            best[key] = r
    return list(best.values())

def parse_payload(payload: dict) -> Tuple[str, List[dict]]:
    """
    Return: (tracking_id_in_payload, records)
    """
    data = payload.get("data") or {}
    sls = data.get("sls_tracking_info") or {}
    tracking_id = str(sls.get("sls_tn") or "").strip()
    records = sls.get("records") or []
    if not isinstance(records, list) or not records:
        raise ValueError("Không có records trong response.")
    records = dedupe_records(records)
    return tracking_id, records

def build_template(tracking_id: str, alias: str, record: dict) -> str:
    """
    <Vận đơn SPX> (cho sao chép)
    Items (in đậm): <tên gợi nhớ> (cho sao chép)
    ---------------------
    Thông tin (in đậm): <description>
    Chi tiết (in đậm): <buyer_description>
    Địa chỉ (in đậm): current_location - full_address (nếu có)
    Điểm đến (in đậm): next_location - full_address (nếu có)
    Thời gian (in đậm): hh:mm:ss dd/mm/yyyy (actual_time)
    Trạng thái (in đậm): milestone_name (dịch sang tiếng việt)
    """
    desc = (get_nested(record, "description") or get_nested(record, "tracking_name") or "").strip()
    buyer_desc = (get_nested(record, "buyer_description") or "").strip()

    cur_full = (get_nested(record, "current_location", "full_address") or "").strip()
    nxt_full = (get_nested(record, "next_location", "full_address") or "").strip()

    ts = int(record.get("actual_time") or 0)
    t = fmt_time_vn(ts) if ts else "Chưa có thời gian"

    ms_name = get_nested(record, "milestone_name")
    ms_code_raw = record.get("milestone_code")
    try:
        ms_code = int(ms_code_raw) if ms_code_raw is not None else None
    except Exception:
        ms_code = None

    status_vi = translate_milestone(ms_code, ms_name)
    st_icon = status_icon(ms_code, ms_name)

    alias_show = alias.strip() if alias and alias.strip() else "-"

    lines = [
        f"<code>{esc(tracking_id)}</code>",
        f"🏷️ <b>Items</b>: <code>{esc(alias_show)}</code>",
        "---------------------",
        f"ℹ️ <b>Thông tin</b>: {esc(desc) if desc else '...'}",
        f"📝 <b>Chi tiết</b>: {esc(buyer_desc) if buyer_desc else '...'}",
    ]

    if has_text(cur_full):
        lines.append(f"📍 <b>Địa chỉ</b>: {esc(cur_full)}")
    if has_text(nxt_full):
        lines.append(f"🎯 <b>Điểm đến</b>: {esc(nxt_full)}")

    lines.extend([
        f"🕒 <b>Thời gian</b>: {esc(t)}",
        f"{st_icon} <b>Trạng thái</b>: <b>{esc(status_vi)}</b>",
    ])

    return "\n".join(lines)

def build_timeline(tracking_id: str, records: List[dict], n: int = 8) -> str:
    n = max(1, min(int(n), 20))
    # sort desc
    rs = sorted(records, key=lambda r: int(r.get("actual_time") or 0), reverse=True)[:n]
    rows = []
    for r in rs:
        ts = int(r.get("actual_time") or 0)
        t = fmt_time_vn(ts) if ts else "--"
        code = (get_nested(r, "tracking_code") or "").strip()
        text = (get_nested(r, "buyer_description") or get_nested(r, "description") or get_nested(r, "tracking_name") or "").strip()
        rows.append(f"• {esc(t)} — <b>{esc(code)}</b>: {esc(text) if text else '...'}")
    return f"<b>Timeline</b> <code>{esc(tracking_id)}</code>\n" + "\n".join(rows)

def kb_for(tracking_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh|{tracking_id}"),
            InlineKeyboardButton("🧾 Timeline", callback_data=f"timeline|{tracking_id}"),
        ],
    ])

# =========================
# HTTP fetch (requests -> to_thread)
# =========================
class TrackingError(Exception):
    pass

def fetch_tracking_sync(tracking_id: str) -> dict:
    now = time.time()
    cached = _cache.get(tracking_id)
    if cached and (now - cached[0]) <= CACHE_TTL_SECONDS:
        return cached[1]

    json_data = {"tracking_id": tracking_id}
    try:
        r = requests.post(API_URL, headers=API_HEADERS, json=json_data, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        raise TrackingError(f"Lỗi gọi API: {e}")

    _cache[tracking_id] = (now, data)
    return data

async def fetch_tracking(tracking_id: str) -> dict:
    return await asyncio.to_thread(fetch_tracking_sync, tracking_id)

# =========================
# Persistence schema in chat_data
# chat_data["shipments"] = {
#   "SPXVN...": {"alias": "tên gợi nhớ", "last_ts": 0}
# }
# chat_data["interval"] = POLL_MINUTES
# =========================
def get_shipments(chat_data: dict) -> Dict[str, dict]:
    if "shipments" not in chat_data or not isinstance(chat_data["shipments"], dict):
        chat_data["shipments"] = {}
    return chat_data["shipments"]

def get_interval(chat_data: dict) -> int:
    v = chat_data.get("interval")
    if isinstance(v, int) and 1 <= v <= 60:
        return v
    chat_data["interval"] = POLL_MINUTES
    return chat_data["interval"]

def ensure_watch_job(app: Application, chat_id: int, chat_data: dict) -> None:
    """
    1 job / chat: poll tất cả mã trong list của chat.
    JobQueue.run_repeating là cách chuẩn để chạy định kỳ. :contentReference[oaicite:4]{index=4}
    """
    interval_min = get_interval(chat_data)
    name = f"watch:{chat_id}"

    # nếu đã có job cùng tên thì thôi
    for j in app.job_queue.get_jobs_by_name(name):
        return

    app.job_queue.run_repeating(
        callback=watch_job,
        interval=interval_min * 60,
        first=5,           # đợi 5s rồi chạy lần đầu
        name=name,
        chat_id=chat_id,
    )

# =========================
# Bot commands / handlers
# =========================
HELP = (
    "SPX Tracking Bot\n\n"
    "Gửi thẳng <mã SPXVN...> để xem thông tin mới nhất.\n\n"
    "Lệnh:\n"
    "/add <Mã> <tên gợi nhớ>\n"
    "/list\n"
    "/tt <Mã>\n"
    "/timeline <Mã> [n]\n"
    "/remove <Mã>\n"
    "/name <Mã> <tên gợi nhớ mới>\n"
    "/track <phút> (tuỳ chọn, 1–60)\n\n"
    "/timeline <Mã>\n"
)

async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
    BotCommand("add", "Thêm mã vận đơn để theo dõi"),
    BotCommand("remove", "Xóa mã khỏi danh sách theo dõi"),
    BotCommand("name", "Đổi tên gợi nhớ cho mã vận đơn"),
    BotCommand("list", "Danh sách đơn hàng"),
    BotCommand("track", "Xem thông tin mới nhất"),
    BotCommand("timeline", "Xem timeline"),
    BotCommand("interval", "Đổi chu kỳ theo dõi (phút)"),
    BotCommand("help", "Hướng dẫn"),
])


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP)

async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Cú pháp: /add <Mã> <tên gợi nhớ>")
        return

    tracking_id = context.args[0].strip().upper()
    alias = " ".join(context.args[1:]).strip()

    if not SPX_RE.match(tracking_id):
        await update.message.reply_text("❌ Mã không đúng định dạng SPXVN...")
        return

    shipments = get_shipments(context.chat_data)
    if tracking_id in shipments:
        await update.message.reply_text("ℹ️ Mã này đã tồn tại trong danh sách theo dõi.")
        return

    shipments[tracking_id] = {"alias": alias, "last_ts": 0}
    ensure_watch_job(context.application, update.effective_chat.id, context.chat_data)

    # Tra luôn 1 lần để trả template + set last_ts
    try:
        payload = await fetch_tracking(tracking_id)
        tid_in_payload, records = parse_payload(payload)
        latest = pick_latest_record(records)
        latest_ts = int(latest.get("actual_time") or 0)
        shipments[tracking_id]["last_ts"] = latest_ts

        msg = build_template(tracking_id, alias, latest)
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=kb_for(tracking_id))
    except Exception as e:
        await update.message.reply_text(f"✅ Đã thêm vào list, nhưng tra API lỗi: {e}")

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    shipments = get_shipments(context.chat_data)
    if not shipments:
        await update.message.reply_text("Danh sách đơn hàng đang trống. Dùng /add để thêm.")
        return

    lines = ["<b>Danh sách đơn hàng:</b>"]
    # show copyable code
    for tid, meta in shipments.items():
        alias = (meta.get("alias") or "").strip()
        lines.append(f"<code>{esc(tid)}</code> ({esc(alias)})")

    # thêm nút bấm nhanh: mỗi mã 1 nút Track
    buttons = []
    for tid in list(shipments.keys())[:12]:
        buttons.append([InlineKeyboardButton(f"📦 {tid}", callback_data=f"refresh|{tid}")])

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
    )

async def cmd_track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        cur = get_interval(context.chat_data)
        await update.message.reply_text(f"Chu kỳ hiện tại: {cur} phút.\nCú pháp: /track <phút> (1–60)")
        return
    try:
        mins = int(context.args[0])
        if not (1 <= mins <= 60):
            raise ValueError
    except Exception:
        await update.message.reply_text("❌ Interval không hợp lệ. Chọn 1–60 phút.")
        return

    context.chat_data["interval"] = mins

    # restart job
    chat_id = update.effective_chat.id
    name = f"watch:{chat_id}"
    for j in context.application.job_queue.get_jobs_by_name(name):
        j.schedule_removal()

    ensure_watch_job(context.application, chat_id, context.chat_data)
    await update.message.reply_text(f"✅ Đã đặt chu kỳ theo dõi: {mins} phút.")

async def cmd_tt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Cú pháp: /tt <Mã>")
        return
    tracking_id = context.args[0].strip().upper()
    await send_latest(update, context, tracking_id)

async def cmd_timeline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Cú pháp: /timeline <Mã> [n]")
        return
    tracking_id = context.args[0].strip().upper()
    n = 8
    if len(context.args) >= 2:
        try:
            n = int(context.args[1])
        except Exception:
            n = 8

    try:
        payload = await fetch_tracking(tracking_id)
        _, records = parse_payload(payload)
        msg = build_timeline(tracking_id, records, n=n)
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=kb_for(tracking_id))
    except Exception as e:
        await update.message.reply_text(f"❌ Lỗi timeline: {e}")

async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Cú pháp: /remove <Mã>")
        return

    tracking_id = context.args[0].strip().upper()
    shipments = get_shipments(context.chat_data)

    if tracking_id not in shipments:
        await update.message.reply_text("❌ Mã này không có trong danh sách. Dùng /list để xem.")
        return

    alias = (shipments[tracking_id].get("alias") or "").strip()
    del shipments[tracking_id]

    await update.message.reply_text(
        f"✅ Đã xóa <code>{esc(tracking_id)}</code> ({esc(alias)}) khỏi danh sách.",
        parse_mode=ParseMode.HTML,
    )

    # Nếu danh sách rỗng -> stop watch job
    if not shipments:
        name = f"watch:{update.effective_chat.id}"
        jq = context.application.job_queue
        if jq is not None:
            for j in jq.get_jobs_by_name(name):
                j.schedule_removal()

async def cmd_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Cú pháp: /name <Mã> <tên gợi nhớ mới>")
        return

    tracking_id = context.args[0].strip().upper()
    new_alias = " ".join(context.args[1:]).strip()

    shipments = get_shipments(context.chat_data)
    if tracking_id not in shipments:
        await update.message.reply_text("❌ Mã này chưa có trong danh sách. Dùng /add để thêm trước.")
        return

    shipments[tracking_id]["alias"] = new_alias
    await update.message.reply_text(
        f"✅ Đã đổi tên:\n<code>{esc(tracking_id)}</code>\n→ <code>{esc(new_alias)}</code>",
        parse_mode=ParseMode.HTML,
    )


async def send_latest(update: Update, context: ContextTypes.DEFAULT_TYPE, tracking_id: str):
    shipments = get_shipments(context.chat_data)
    alias = (shipments.get(tracking_id, {}) or {}).get("alias", "")

    try:
        payload = await fetch_tracking(tracking_id)
        _, records = parse_payload(payload)
        latest = pick_latest_record(records)
        msg = build_template(tracking_id, alias, latest)
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=kb_for(tracking_id))
    except Exception as e:
        await update.message.reply_text(f"❌ Lỗi tra cứu: {e}")

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    # hỗ trợ: "timeline SPXVN..."
    if text.lower().startswith("timeline "):
        tracking_id = text.split(None, 1)[1].strip().upper()
        context.args = [tracking_id]  # hack nhẹ để reuse
        await cmd_timeline(update, context)
        return

    # gửi thẳng mã SPXVN...
    m = SPX_RE.match(text.upper())
    if m:
        await send_latest(update, context, m.group(1).upper())
        return

    await update.message.reply_text("Gửi mã SPXVN... hoặc dùng /help.")

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    try:
        action, tracking_id = (q.data or "").split("|", 1)
    except ValueError:
        return

    shipments = get_shipments(context.chat_data)
    alias = (shipments.get(tracking_id, {}) or {}).get("alias", "")

    try:
        payload = await fetch_tracking(tracking_id)
        _, records = parse_payload(payload)

        if action == "refresh":
            latest = pick_latest_record(records)
            msg = build_template(tracking_id, alias, latest)
        elif action == "timeline":
            msg = build_timeline(tracking_id, records, n=8)
        else:
            return

        # edit message (giữ nút bấm)
        await q.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=kb_for(tracking_id))
    except Exception as e:
        await q.edit_message_text(f"❌ Lỗi: {e}")

# =========================
# Watch job (polling notify)
# =========================
async def watch_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Chạy theo chu kỳ: poll tất cả mã trong context.chat_data["shipments"].
    Nếu latest.actual_time mới hơn last_ts => notify.
    """
    app = context.application
    chat_id = context.job.chat_id

    # ✅ ĐÚNG: dùng context.chat_data (đã có vì job tạo với chat_id)
    chat_data = context.chat_data
    shipments = chat_data.get("shipments", {})
    if not shipments:
        return

    for tracking_id, meta in list(shipments.items()):
        alias = (meta.get("alias") or "").strip()
        last_ts = int(meta.get("last_ts") or 0)

        try:
            payload = await fetch_tracking(tracking_id)
            _, records = parse_payload(payload)
            latest = pick_latest_record(records)
            latest_ts = int(latest.get("actual_time") or 0)

            if latest_ts > last_ts:
                # ✅ chỉ mutate dict con, KHÔNG gán app.chat_data[chat_id] = ...
                shipments[tracking_id]["last_ts"] = latest_ts

                msg = "📣 <b>Có cập nhật mới</b>\n" + build_template(tracking_id, alias, latest)
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=msg,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb_for(tracking_id),
                )
        except Exception:
            continue


# =========================
# Main
# =========================
def main():
    persistence = PicklePersistence(filepath="spx_bot_state")

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .persistence(persistence)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("tt", cmd_tt))
    app.add_handler(CommandHandler("timeline", cmd_timeline))
    app.add_handler(CommandHandler("track", cmd_track))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("name", cmd_name))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
