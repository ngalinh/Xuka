"""Xuka Kế Toán - Telegram Bot."""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes,
)

from xuka.config import TELEGRAM_BOT_TOKEN
from xuka.services.ocr import extract_transfer
from xuka.services.mapper import map_entry
from xuka.services.memory import memory_upsert
from xuka.services.sheets import (
    append_entries, append_zelle_entry,
    get_unique_nganh_nghe, get_unique_danh_muc, get_unique_pttt,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

VN_TZ = timezone(timedelta(hours=7))

# In-memory state: chat_id → {entry_id: entry_data}
entries_store: dict[int, dict[str, dict]] = {}
# Saved entries info for delete: eid → {sheet, ngay_tt, so_tien, noi_dung, nguoi_nhan, is_zelle, message_id, chat_id, saved_at}
saved_entries: dict[str, dict] = {}
# Pending photos waiting for caption: chat_id → {ocr, image_url, progress_msg, task}
pending_photos: dict[int, dict] = {}
# Media group captions: media_group_id → caption (shared across album)
media_group_captions: dict[str, str] = {}
# Media groups marked to skip (any photo's caption tagged another user)
media_group_skip: set[str] = set()


# ===== Helpers =====

def _fmt(n: int) -> str:
    return f"{n:,}"

def _parse_amount(text: str) -> int | None:
    cleaned = text.strip().lower()
    m = re.match(r'^([\d.,]+)\s*(tr|triệu|trieu)$', cleaned)
    if m:
        num = m.group(1).replace(",", "").replace(".", "")
        if num.isdigit():
            return int(num) * 1_000_000
    if cleaned.endswith("k"):
        cleaned = cleaned[:-1].strip().replace(",", "").replace(".", "")
        if cleaned.isdigit() and int(cleaned) > 0:
            return int(cleaned) * 1000
    cleaned = cleaned.replace(",", "").replace(".", "")
    if cleaned.isdigit() and int(cleaned) > 0:
        return int(cleaned)
    return None

def _detect_zelle(note: str) -> dict | None:
    if not note:
        return None
    if "zelle" not in note.lower():
        return None
    # Patterns support: $116, $5k, $1,200, 116$, 116 usd, 5k usd
    patterns = [
        r'\$\s*([\d,]+(?:\.\d+)?)\s*(k|K)',       # $5k, $5K
        r'\$\s*([\d,]+(?:\.\d+)?)',                # $116, $1,200
        r'([\d,]+(?:\.\d+)?)\s*(k|K)\s*\$',        # 5k$
        r'([\d,]+(?:\.\d+)?)\s*\$',                # 116$
        r'([\d,]+(?:\.\d+)?)\s*(k|K)\s*(?:usd|USD)',  # 5k usd
        r'([\d,]+(?:\.\d+)?)\s*(?:usd|USD)',       # 116 usd
    ]
    # "bán zelle" / "ban zelle" → sell (Thu Chi). Default = mua (Lãi tỉ giá).
    from unidecode import unidecode
    is_sell = bool(re.search(r'\bban\b', unidecode(note).lower()))
    for pat in patterns:
        m = re.search(pat, note)
        if m:
            try:
                val = float(m.group(1).replace(",", ""))
                # If pattern had 'k' group, multiply by 1000
                has_k = len(m.groups()) >= 2 and m.group(2) and m.group(2).lower() == "k"
                if has_k:
                    val *= 1000
                return {"usd": val, "is_sell": is_sell}
            except (ValueError, IndexError):
                continue
    return None

def _extract_amount_from_text(text: str) -> tuple[int, str]:
    """Extract amount and remaining text. Returns (amount, clean_text)."""
    patterns = [
        r'([\d.,]+)\s*(tr|triệu|trieu)',
        r'([\d.,]+)\s*k\b',
        r'([\d]{1,3}(?:[.,]\d{3})+)',
        r'\b(\d{4,})\b',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            parsed = _parse_amount(m.group(0))
            if parsed and parsed > 0:
                clean = text[:m.start()].strip(" -–—") + " " + text[m.end():].strip(" -–—")
                return parsed, clean.strip()
    return 0, text


async def _upload_image(image_bytes: bytes, telegram_file_path: str) -> str:
    """Return permanent inline-viewable image URL (via weserv.nl proxy)."""
    # Build Telegram CDN URL
    if telegram_file_path.startswith("http"):
        tg_url = telegram_file_path
    else:
        tg_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{telegram_file_path}"
    # Use weserv.nl proxy - serves images inline (no download), permanent
    return f"https://images.weserv.nl/?url={tg_url}"


def _store_entry(chat_id: int, entry: dict) -> str:
    eid = str(uuid.uuid4())[:8]
    if chat_id not in entries_store:
        entries_store[chat_id] = {}
    entries_store[chat_id][eid] = entry
    return eid

def _get_entry(chat_id: int, eid: str) -> dict | None:
    return entries_store.get(chat_id, {}).get(eid)


def _build_card_text(e: dict) -> str:
    ocr = e["ocr"]
    mp = e["mapping"]
    loai = e.get("loai", "Chi")
    is_zelle = e.get("is_zelle") and e.get("zelle")

    if is_zelle:
        # Zelle card: chỉ hiển thị thông tin cần cho sheet Lãi tỉ giá
        z = e["zelle"]
        lines = [
            f"💵 *ZELLE → Lãi tỉ giá*",
            f"📅 Ngày: {ocr['ngay']}",
            f"👤 Tài khoản nhận: {ocr['nguoi_nhan'] or '—'}",
            f"💰 Total CK: *{_fmt(ocr['so_tien'])} ₫*",
            f"💵 USD: *${z['usd']:g}*",
            f"📊 Tỉ giá mua: *{_fmt(z['ti_gia_mua'])}*",
        ]
        return "\n".join(lines)

    # Normal card
    conf = mp["confidence"]
    badge = f"{conf}% ✓" if conf >= 80 else f"{conf}% ?"
    lines = [
        f"📋 *Giao dịch {ocr['ngay']}*  `{badge}`",
        f"👤 Người nhận: {ocr['nguoi_nhan'] or '—'}",
        f"📝 Nội dung: {ocr['noi_dung'] or '—'}",
    ]
    # USD payment: show both USD and VND
    if e.get("is_usd_payment"):
        usd_amt = e.get("usd_amount", 0)
        lines.append(f"💵 USD: *${usd_amt:g}* × 26,000")
        lines.append(f"💰 Chi (VND): *{_fmt(ocr['so_tien'])} ₫*")
    else:
        lines.append(f"💰 {'Thu' if loai == 'Thu' else 'Chi'}: *{_fmt(ocr['so_tien'])} ₫*")
    lines.extend([
        "",
        f"🏢 Ngành Nghề: *{mp['nganh_nghe'] or 'Chưa chọn'}*",
        f"📂 Danh mục: *{mp['danh_muc'] or 'Chưa chọn'}*",
    ])
    # PTTT chỉ áp dụng cho khoản Chi
    if loai != "Thu":
        lines.append(f"💳 PTTT: *{mp['pttt'] or 'Chưa chọn'}*")
    return "\n".join(lines)


def _build_card_buttons(eid: str, is_zelle: bool = False, loai: str = "Chi") -> InlineKeyboardMarkup:
    if is_zelle:
        # Zelle: chỉ Lưu / Hủy, không cần Sửa
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Lưu vào Lãi tỉ giá", callback_data=f"save_{eid}"),
            InlineKeyboardButton("❌ Hủy", callback_data=f"skip_{eid}"),
        ]])
    rows = [
        [InlineKeyboardButton("✅ Lưu vào Sheets", callback_data=f"save_{eid}"),
         InlineKeyboardButton("❌ Hủy", callback_data=f"skip_{eid}")],
        [InlineKeyboardButton("📝 Sửa Ngành nghề", callback_data=f"edit_nn_{eid}"),
         InlineKeyboardButton("📝 Sửa Danh mục", callback_data=f"edit_dm_{eid}")],
    ]
    # Khoản Thu không có PTTT → bỏ nút Sửa PTTT
    if loai == "Thu":
        rows.append([InlineKeyboardButton("✏️ Sửa nội dung", callback_data=f"edit_nd_{eid}")])
    else:
        rows.append([InlineKeyboardButton("📝 Sửa PTTT", callback_data=f"edit_pt_{eid}"),
                     InlineKeyboardButton("✏️ Sửa nội dung", callback_data=f"edit_nd_{eid}")])
    return InlineKeyboardMarkup(rows)


# ===== Command Handlers =====

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Xin chào! Tôi là *Xuka kế toán* 📊\n\n"
        "Gửi tôi:\n"
        "📸 *Ảnh chuyển khoản* → tự đọc + mapping\n"
        "✍️ *Text* VD: `Chi tool order 5tr` → tạo entry\n"
        "📸 + caption `Mua $116 zelle` → nhập Lãi tỉ giá\n\n"
        "Tôi sẽ tự mapping với lịch sử. Sai thì bấm Sửa!",
        parse_mode="Markdown",
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "*HƯỚNG DẪN SỬ DỤNG XUKA KẾ TOÁN*\n\n"
        "*📸 GỬI ẢNH CHUYỂN KHOẢN*\n"
        "• Gửi 1 hoặc nhiều ảnh cùng lúc\n"
        "• Bot OCR + tự mapping Ngành nghề/Danh mục/PTTT từ lịch sử\n"
        "• Kiểm tra card → bấm ✅ Lưu hoặc 📝 Sửa\n\n"
        "*✍️ KÈM GHI CHÚ CHO ẢNH*\n"
        "• Cách 1: Gửi ảnh kèm caption\n"
        "• Cách 2: Gửi ảnh không caption → gửi text trong 30s tiếp theo\n"
        "• Album nhiều ảnh + 1 caption → caption áp dụng cho tất cả ảnh\n\n"
        "*💵 VIẾT TẮT SỐ TIỀN*\n"
        "`5tr` = `5 tr` = `5 triệu` = 5,000,000\n"
        "`500k` = 500,000\n"
        "`5,000,000` = 5,000,000\n\n"
        "*💰 ZELLE*\n"
        "• `Mua $XXX zelle` → sheet *Lãi tỉ giá*\n"
        "• `Bán $XXX zelle` → sheet *Thu Chi* (cột Thu, mapping theo lịch sử)\n"
        "VD: `Mua $5k zelle` / `Bán $1200 zelle`\n\n"
        "*🌐 USD PAYMENT (Zelle, PayPal, Wise...)*\n"
        "Gửi ảnh screenshot có số $ → bot tự convert × 26,000\n"
        "→ Map: ORDER CHECKOUT / CHI - Vận chuyển quốc tế\n\n"
        "*🗑 XÓA GIAO DỊCH*\n"
        "Sau khi lưu → bấm nút 🗑 Xóa trong 2 phút\n\n"
        "*━━━━━━━━━━━━━━━━━━━━*\n"
        "*🤖 TRONG GROUP - CÁCH GỌI BOT*\n"
        "*━━━━━━━━━━━━━━━━━━━━*\n\n"
        "*✅ BOT SẼ XỬ LÝ:*\n"
        "• Mọi *ảnh chuyển khoản* gửi trong group\n"
        "• Caption kèm ảnh làm ghi chú\n"
        "• Text *tag bot*: `@Asale_xukabot help`\n"
        "• Text *reply* tin nhắn của bot\n\n"
        "*❌ BOT SẼ BỎ QUA:*\n"
        "• Chat thường không tag/reply bot\n"
        "• Text có *cả số tiền + keyword kế toán*\n"
        "  (VD: \"Chi tool order 5tr\" → bot không phản hồi\n"
        "  vì đây là chat giữa user, không phải lệnh cho bot)\n"
        "• Stickers, GIFs, voice, video\n\n"
        "*💡 Muốn bot tạo entry từ text trong group?*\n"
        "→ Tag bot: `@Asale_xukabot chi van phong`\n"
        "→ Hoặc gửi trực tiếp cho bot (DM)\n\n"
        "*⚙️ LỆNH*\n"
        "/start - Khởi động bot\n"
        "/help - Hướng dẫn này"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


# ===== Photo Handler =====

def _caption_tags_other_user(msg, bot_username: str) -> bool:
    """Check if caption tags any user/bot OTHER than this bot.
    Returns True if there's a mention/text_mention not pointing to the bot.
    """
    entities = msg.caption_entities or []
    caption = msg.caption or ""
    if not entities:
        return False
    for ent in entities:
        if ent.type == "mention":
            # @username — extract from text
            mentioned = caption[ent.offset:ent.offset + ent.length]  # like "@SomeUser"
            if mentioned.lstrip("@").lower() != (bot_username or "").lower():
                return True
        elif ent.type == "text_mention":
            # User object embedded in entity (no @ prefix)
            if ent.user and not ent.user.is_bot:
                return True
            if ent.user and ent.user.username and ent.user.username.lower() != (bot_username or "").lower():
                return True
    return False


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    caption = msg.caption or ""
    chat_id = msg.chat_id
    media_group_id = msg.media_group_id
    u = msg.from_user
    user_tag = f"{u.username or u.first_name or '?'}(id={u.id})"
    logger.info("[PHOTO-IN] user=%s chat=%s msg_id=%s media_group=%s caption=%r",
                user_tag, chat_id, msg.message_id, media_group_id, caption)

    # In group: skip the photo if caption tags another user (not the bot).
    # Process only when no tag, OR caption tags the bot itself.
    is_group = msg.chat.type in ("group", "supergroup")
    if is_group:
        bot_username = (await context.bot.get_me()).username or ""
        # If THIS message's caption tags other user → mark album to skip (or skip directly)
        tags_other = _caption_tags_other_user(msg, bot_username)
        if tags_other:
            if media_group_id:
                media_group_skip.add(media_group_id)
            logger.info("[PHOTO-SKIP] user=%s chat=%s caption tags other user — bot không xử lý",
                        user_tag, chat_id)
            return
        # If part of an album that another photo in the album already marked → skip
        if media_group_id and media_group_id in media_group_skip:
            logger.info("[PHOTO-SKIP] user=%s chat=%s media_group=%s already marked skip",
                        user_tag, chat_id, media_group_id)
            return

    # Handle media group (album): share caption across all photos in group
    if media_group_id:
        # Any caption in the group gets stored
        if caption:
            media_group_captions[media_group_id] = caption

    # Cancel any pending single-photo wait (only if NOT in a media group)
    if not media_group_id and chat_id in pending_photos:
        old = pending_photos.pop(chat_id)
        try:
            old["task"].cancel()
        except Exception:
            pass

    progress = await msg.reply_text("⏳ Đang xử lý ảnh...")

    try:
        # Download photo
        photo = msg.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        image_bytes = await file.download_as_bytearray()
        image_url = await _upload_image(bytes(image_bytes), file.file_path)

        # OCR
        ocr = extract_transfer(bytes(image_bytes), "image/jpeg")
        logger.info("OCR: nguoi=%r, noi_dung=%r, ngan_hang=%r, loai=%r, so_tien=%r, is_transfer=%r",
                    ocr.nguoi_nhan, ocr.noi_dung, ocr.ngan_hang, ocr.loai, ocr.so_tien,
                    getattr(ocr, "is_transfer", True))

        # Skip if image is not a bank transfer screenshot
        if not getattr(ocr, "is_transfer", True):
            logger.info("Image not a bank transfer, skipping (chat_id=%s)", chat_id)
            try:
                await progress.delete()
            except Exception:
                pass
            # Only notify in private chats to avoid group noise
            if msg.chat.type == "private":
                await msg.reply_text("ℹ️ Đây không phải ảnh chuyển khoản, bot bỏ qua.")
            return

        if media_group_id:
            # Wait 3s for all photos in album to arrive + caption to be set
            await asyncio.sleep(3)
            # Re-check skip flag: another photo in the same album might have tagged
            # another user and arrived after this one
            if media_group_id in media_group_skip:
                logger.info("[PHOTO-SKIP] chat=%s media_group=%s skipped post-wait (album tagged other user)",
                            chat_id, media_group_id)
                try:
                    await progress.delete()
                except Exception:
                    pass
                return
            group_caption = media_group_captions.get(media_group_id, "")
            await _process_photo_entry(msg, chat_id, ocr, image_url, group_caption, progress)
            return

        if caption:
            # Single photo with caption → process immediately
            await _process_photo_entry(msg, chat_id, ocr, image_url, caption, progress)
        else:
            # Single photo, no caption → wait up to 30s for next text message
            await progress.edit_text("⏳ Đã đọc ảnh. Đợi ghi chú trong 30s... (gửi text để thêm ghi chú)")
            task = asyncio.create_task(
                _wait_for_caption(msg, chat_id, ocr, image_url, progress, context)
            )
            pending_photos[chat_id] = {
                "ocr": ocr,
                "image_url": image_url,
                "progress": progress,
                "original_msg": msg,
                "task": task,
            }

    except Exception as e:
        logger.error("Photo handler error: %s", e)
        try:
            await progress.edit_text(f"❌ Lỗi xử lý ảnh: {e}")
        except Exception:
            await msg.reply_text(f"❌ Lỗi xử lý ảnh: {e}")


async def _wait_for_caption(msg, chat_id: int, ocr, image_url: str, progress, context):
    """Wait 30s for a text message to use as caption. If timeout, process without caption."""
    try:
        await asyncio.sleep(30)
        # Timeout: process without caption
        if chat_id in pending_photos and pending_photos[chat_id].get("ocr") is ocr:
            pending_photos.pop(chat_id, None)
            await _process_photo_entry(msg, chat_id, ocr, image_url, "", progress)
    except asyncio.CancelledError:
        pass


async def _process_photo_entry(msg, chat_id: int, ocr, image_url: str, caption: str, progress):
    """Build and send card for a photo entry with given caption."""
    try:
        # Detect zelle direction up front — sell (bán) routes to Thu Chi as
        # income; buy (mua) routes to Lãi tỉ giá downstream.
        zelle_info = _detect_zelle(caption) if caption else None
        is_sell_zelle = bool(zelle_info and zelle_info.get("is_sell"))

        # Sell zelle = customer pays VND into our account → Thu (income).
        # Other bank-transfer screenshots default to Chi (outgoing).
        loai = "Thu" if is_sell_zelle else "Chi"
        GENERIC_OCR = {"chuyen tien", "chuyển tiền", "ck", "tt", "thanh toan", "thanh toán", "noi dung", ""}
        ocr_nd_clean = (ocr.noi_dung or "").strip().lower()

        # USD payment: build noi_dung as "<caption or default> : $<usd>"
        is_usd_payment = getattr(ocr, "is_usd_payment", False)
        if is_usd_payment and ocr.so_tien_usd > 0:
            base_note = caption if caption else "Thanh toán cước"
            noi_dung = f"{base_note} : ${ocr.so_tien_usd:g}"
        elif is_sell_zelle:
            # Keep caption as-is so history lookup matches "bán zelle" rows
            noi_dung = caption
        elif caption and "zelle" not in caption.lower():
            noi_dung = caption
        elif ocr_nd_clean and ocr_nd_clean not in GENERIC_OCR:
            noi_dung = ocr.noi_dung
        else:
            noi_dung = ocr.nguoi_nhan or ""

        mapping = map_entry(
            nguoi_nhan=ocr.nguoi_nhan,
            noi_dung=noi_dung,
            ngan_hang=ocr.ngan_hang,
            user_note=caption,
            loai=loai.lower(),
        )
        logger.info("Mapping: nn=%r, dm=%r, src=%r (noi_dung=%r)",
                    mapping.nganh_nghe, mapping.danh_muc, mapping.source, noi_dung)

        entry = {
            "ocr": {
                "so_tien": ocr.so_tien,
                "nguoi_nhan": ocr.nguoi_nhan,
                "ngan_hang": ocr.ngan_hang,
                "ngay": ocr.ngay or datetime.now(VN_TZ).strftime("%d/%m/%Y"),
                "noi_dung": noi_dung,
            },
            "mapping": {
                "nganh_nghe": mapping.nganh_nghe,
                "danh_muc": mapping.danh_muc,
                "pttt": mapping.pttt,
                "confidence": mapping.confidence,
                "source": mapping.source,
            },
            "loai": loai,
            "image_url": image_url,
        }

        # Extract "tháng X" from caption for month override
        if caption:
            import re as _re
            m_thang = _re.search(r'(?:thang|tháng)\s*(\d{1,2})', caption, _re.IGNORECASE)
            if m_thang:
                entry["thang_override"] = m_thang.group(1)

        # USD payment: auto-map to ORDER CHECKOUT / CHI - Vận chuyển quốc tế
        if is_usd_payment:
            entry["is_usd_payment"] = True
            entry["usd_amount"] = ocr.so_tien_usd
            entry["mapping"]["nganh_nghe"] = "ORDER CHECKOUT"
            entry["mapping"]["danh_muc"] = "CHI - Vận chuyển quốc tế"
            if entry["mapping"]["confidence"] < 80:
                entry["mapping"]["confidence"] = 90
                entry["mapping"]["source"] = "usd_payment"

        # Zelle detection (from caption) - only if NOT already USD payment
        if not is_usd_payment and zelle_info and ocr.so_tien > 0:
            if is_sell_zelle:
                # Sell zelle → Thu Chi entry. Apply default mapping when
                # history didn't yield one.
                if not entry["mapping"]["nganh_nghe"]:
                    entry["mapping"]["nganh_nghe"] = "CÔNG TY"
                if not entry["mapping"]["danh_muc"]:
                    entry["mapping"]["danh_muc"] = "THU - Khoản thu khác"
                if entry["mapping"]["confidence"] < 60:
                    entry["mapping"]["confidence"] = 60
                    entry["mapping"]["source"] = "ban_zelle_default"
            else:
                # Buy zelle → Lãi tỉ giá entry
                usd = zelle_info["usd"]
                ti_gia = round(ocr.so_tien / usd) if usd > 0 else 0
                entry["is_zelle"] = True
                entry["zelle"] = {
                    "usd": usd, "ti_gia_mua": ti_gia,
                    "total_ck": ocr.so_tien, "tai_khoan_nhan": ocr.nguoi_nhan,
                }
                entry["mapping"]["nganh_nghe"] = "ORDER CHECKOUT"
                entry["mapping"]["danh_muc"] = "CHI - Checkout zelle"
                entry["mapping"]["confidence"] = 95
                entry["mapping"]["source"] = "zelle_auto"

        eid = _store_entry(chat_id, entry)
        logger.info("[CARD-CREATED] chat=%s eid=%s | %s",
                    chat_id, eid, _entry_summary(entry))
        text = _build_card_text(entry)
        kb = _build_card_buttons(eid, entry.get("is_zelle", False), entry.get("loai", "Chi"))
        # Delete progress message and send card
        try:
            await progress.delete()
        except Exception:
            pass
        await msg.reply_text(text, parse_mode="Markdown", reply_markup=kb)
    except Exception as e:
        logger.error("Process photo error: %s", e)
        try:
            await progress.edit_text(f"❌ Lỗi xử lý: {e}")
        except Exception:
            await msg.reply_text(f"❌ Lỗi xử lý: {e}")


# ===== Text Handler =====

# Keywords that indicate accounting-related text
ACCOUNTING_KEYWORDS = [
    "chi", "thu", "zelle", "tool", "order", "ship", "van phong", "văn phòng",
    "luong", "lương", "checkout", "website", "dropship", "stock",
    "ck", "chuyển khoản", "chuyen khoan", "mua", "bán", "ban",
    "tr", "triệu", "trieu",
]

def _has_amount(text: str) -> bool:
    """Check if text contains an amount pattern."""
    lower = text.lower()
    if re.search(r'\d+\s*(tr|triệu|trieu|k)\b', lower):
        return True
    if re.search(r'\$\s*\d+', lower):
        return True
    if re.search(r'\b\d{4,}\b', lower):  # plain 4+ digit number
        return True
    return False


def _has_keyword(text: str) -> bool:
    """Check if text has accounting keywords (word-boundary match for short kw
    to avoid false positives like 'click' matching 'ck', 'thấy' matching 'thu')."""
    lower = text.lower()
    from unidecode import unidecode
    ascii_lower = unidecode(lower)
    for kw in ACCOUNTING_KEYWORDS:
        if " " in kw:
            # Multi-word phrases (e.g., "van phong"): plain substring match
            if kw in ascii_lower or kw in lower:
                return True
        else:
            # Single short word: enforce word boundary
            pat = rf'\b{re.escape(kw)}\b'
            if re.search(pat, ascii_lower) or re.search(pat, lower):
                return True
    return False


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    text = msg.text.strip()
    chat_id = msg.chat_id

    if text.startswith("/"):
        return  # Skip commands

    # In group: only process if tagged or replying to bot
    is_group = msg.chat.type in ("group", "supergroup")
    if is_group:
        bot_username = (await context.bot.get_me()).username
        is_tagged = f"@{bot_username}" in text
        is_reply_to_bot = (msg.reply_to_message and
                           msg.reply_to_message.from_user and
                           msg.reply_to_message.from_user.is_bot)
        if not is_tagged and not is_reply_to_bot:
            # Skip all group chat unless explicitly addressed
            return
        # Remove bot tag from text BEFORE keyword/amount check
        if is_tagged:
            text = text.replace(f"@{bot_username}", "").strip()

    # Required for text entries (in group or DM): must have BOTH amount AND
    # an accounting keyword. Otherwise silently ignore — don't ask the user
    # for amount.
    if not (_has_amount(text) and _has_keyword(text)):
        logger.info("[TEXT-IGNORE] chat=%s missing amount or keyword: %r",
                    chat_id, text[:80])
        return

    zelle = _detect_zelle(text)
    is_sell_zelle = bool(zelle and zelle.get("is_sell"))

    amount, noi_dung = _extract_amount_from_text(text)
    if is_sell_zelle:
        loai = "Thu"
        # Use full text so history lookup matches "bán zelle" rows
        noi_dung = text
    else:
        loai = "Thu" if any(w in noi_dung.lower() for w in ["thu", "nhận", "hoàn"]) else "Chi"
    mapping = map_entry(nguoi_nhan="", noi_dung=noi_dung, ngan_hang="",
                        user_note=text, loai=loai.lower())
    ngay = datetime.now(VN_TZ).strftime("%d/%m/%Y")

    entry = {
        "ocr": {
            "so_tien": amount,
            "nguoi_nhan": "",
            "ngan_hang": "",
            "ngay": ngay,
            "noi_dung": noi_dung,
        },
        "mapping": {
            "nganh_nghe": mapping.nganh_nghe,
            "danh_muc": mapping.danh_muc,
            "pttt": mapping.pttt,
            "confidence": mapping.confidence,
            "source": mapping.source,
        },
        "loai": loai,
    }

    if zelle and amount > 0:
        if is_sell_zelle:
            if not entry["mapping"]["nganh_nghe"]:
                entry["mapping"]["nganh_nghe"] = "CÔNG TY"
            if not entry["mapping"]["danh_muc"]:
                entry["mapping"]["danh_muc"] = "THU - Khoản thu khác"
            if entry["mapping"]["confidence"] < 60:
                entry["mapping"]["confidence"] = 60
                entry["mapping"]["source"] = "ban_zelle_default"
        else:
            usd = zelle["usd"]
            entry["is_zelle"] = True
            entry["zelle"] = {
                "usd": usd, "ti_gia_mua": round(amount / usd) if usd > 0 else 0,
                "total_ck": amount, "tai_khoan_nhan": "",
            }
            entry["mapping"]["nganh_nghe"] = "ORDER CHECKOUT"
            entry["mapping"]["danh_muc"] = "CHI - Checkout zelle"
            entry["mapping"]["confidence"] = 95

    # amount is guaranteed > 0 by the _has_amount check above; if extraction
    # failed for some reason, silently skip rather than prompt.
    if amount == 0:
        logger.warning("[TEXT-IGNORE] passed _has_amount but extracted 0 from %r", text)
        return

    eid = _store_entry(chat_id, entry)
    text_card = _build_card_text(entry)
    kb = _build_card_buttons(eid, entry.get("is_zelle", False), entry.get("loai", "Chi"))
    await msg.reply_text(text_card, parse_mode="Markdown", reply_markup=kb)


# ===== Callback Handlers =====

def _user_tag(query) -> str:
    """Format user info for logs: 'username(id)' or 'FirstName(id)'."""
    u = query.from_user
    name = u.username or u.first_name or "?"
    return f"{name}(id={u.id})"


def _entry_summary(entry: dict) -> str:
    """Compact summary of an entry for logs."""
    ocr = entry.get("ocr", {})
    mp = entry.get("mapping", {})
    return (
        f"so_tien={ocr.get('so_tien')}, nguoi={ocr.get('nguoi_nhan','')!r}, "
        f"nd={ocr.get('noi_dung','')!r}, nn={mp.get('nganh_nghe','')!r}, "
        f"dm={mp.get('danh_muc','')!r}, pttt={mp.get('pttt','')!r}, "
        f"loai={entry.get('loai','')}, zelle={entry.get('is_zelle', False)}"
    )


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id
    user_tag = _user_tag(query)

    # Save
    if data.startswith("save_"):
        eid = data[5:]
        entry = _get_entry(chat_id, eid)
        if not entry:
            logger.warning("[SAVE-FAIL] user=%s chat=%s eid=%s reason=entry_expired",
                           user_tag, chat_id, eid)
            await query.edit_message_text("⚠️ Giao dịch đã hết hạn.")
            return
        logger.info("[SAVE-CLICK] user=%s chat=%s eid=%s | %s",
                    user_tag, chat_id, eid, _entry_summary(entry))
        # Clear any stale editing state for this entry
        if context.user_data.get("editing_noi_dung") == eid:
            context.user_data.pop("editing_noi_dung", None)
        await _save_entry(query, entry, eid, chat_id, user_tag=user_tag)
        return

    # Skip
    if data.startswith("skip_"):
        eid = data[5:]
        entry = _get_entry(chat_id, eid)
        if entry:
            logger.info("[SKIP-CLICK] user=%s chat=%s eid=%s | %s",
                        user_tag, chat_id, eid, _entry_summary(entry))
        else:
            logger.info("[SKIP-CLICK] user=%s chat=%s eid=%s (entry not in store - may be old card)",
                        user_tag, chat_id, eid)
        # Clear any stale editing state
        context.user_data.pop("editing_noi_dung", None)
        await query.edit_message_text("❌ Đã hủy giao dịch.")
        return

    # Delete (after save)
    if data.startswith("del_"):
        import time
        eid = data[4:]
        info = saved_entries.get(eid)
        if not info:
            logger.warning("[DELETE-FAIL] user=%s chat=%s eid=%s reason=not_in_saved_entries (>2min or already deleted)",
                           user_tag, chat_id, eid)
            await query.edit_message_text("⚠️ Quá thời gian xóa (2 phút).")
            return
        # Check 2 min limit
        if time.time() - info["saved_at"] > 120:
            logger.warning("[DELETE-FAIL] user=%s chat=%s eid=%s reason=>2min_limit", user_tag, chat_id, eid)
            del saved_entries[eid]
            await query.edit_message_text("⚠️ Quá thời gian xóa (2 phút).")
            return
        logger.info("[DELETE-CLICK] user=%s chat=%s eid=%s | ngay=%s so_tien=%s nguoi=%r nd=%r zelle=%s",
                    user_tag, chat_id, eid, info.get("ngay_tt"), info.get("so_tien"),
                    info.get("nguoi_nhan",""), info.get("noi_dung",""), info.get("is_zelle"))
        # Delete from sheet
        try:
            await _delete_saved(info)
            del saved_entries[eid]
            logger.info("[DELETE-OK] user=%s eid=%s", user_tag, eid)
            await query.edit_message_text("🗑 Đã xóa giao dịch khỏi sheet.")
        except Exception as e:
            logger.error("[DELETE-ERR] user=%s eid=%s err=%s", user_tag, eid, e)
            await query.edit_message_reply_markup(None)
            await query.message.reply_text(f"❌ Lỗi xóa: {e}")
        return

    # Edit Ngành Nghề
    if data.startswith("edit_nn_"):
        eid = data[8:]
        options = get_unique_nganh_nghe()
        buttons = [[InlineKeyboardButton(o, callback_data=f"snn_{eid}_{o}")] for o in options]
        buttons.append([InlineKeyboardButton("⬅️ Quay lại", callback_data=f"back_{eid}")])
        await query.edit_message_reply_markup(InlineKeyboardMarkup(buttons))
        return

    # Edit Danh Mục
    if data.startswith("edit_dm_"):
        eid = data[8:]
        entry = _get_entry(chat_id, eid)
        loai = entry.get("loai", "Chi") if entry else "Chi"
        options = get_unique_danh_muc(loai)
        buttons = [[InlineKeyboardButton(o, callback_data=f"sdm_{eid}_{o}")] for o in options]
        buttons.append([InlineKeyboardButton("⬅️ Quay lại", callback_data=f"back_{eid}")])
        await query.edit_message_reply_markup(InlineKeyboardMarkup(buttons))
        return

    # Edit PTTT (chỉ khoản Chi mới có PTTT)
    if data.startswith("edit_pt_"):
        eid = data[8:]
        entry = _get_entry(chat_id, eid)
        if entry and entry.get("loai") == "Thu":
            return
        options = get_unique_pttt()
        buttons = [[InlineKeyboardButton(o, callback_data=f"spt_{eid}_{o}")] for o in options]
        buttons.append([InlineKeyboardButton("⬅️ Quay lại", callback_data=f"back_{eid}")])
        await query.edit_message_reply_markup(InlineKeyboardMarkup(buttons))
        return

    # Edit Nội dung - prompt user to type new content
    if data.startswith("edit_nd_"):
        eid = data[8:]
        entry = _get_entry(chat_id, eid)
        if entry:
            context.user_data["editing_noi_dung"] = eid
            await query.message.reply_text(
                f"✏️ Nhập nội dung mới (hiện tại: _{entry['ocr'].get('noi_dung','')}_):",
                parse_mode="Markdown",
            )
        return

    # Set Ngành Nghề
    if data.startswith("snn_"):
        parts = data[4:].split("_", 1)
        eid, val = parts[0], parts[1]
        entry = _get_entry(chat_id, eid)
        if entry:
            old = entry["mapping"].get("nganh_nghe", "")
            entry["mapping"]["nganh_nghe"] = val
            logger.info("[EDIT-NN] user=%s chat=%s eid=%s %r → %r",
                        user_tag, chat_id, eid, old, val)
            text = _build_card_text(entry)
            kb = _build_card_buttons(eid, entry.get("is_zelle", False), entry.get("loai", "Chi"))
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        return

    # Set Danh Mục
    if data.startswith("sdm_"):
        parts = data[4:].split("_", 1)
        eid, val = parts[0], parts[1]
        entry = _get_entry(chat_id, eid)
        if entry:
            old = entry["mapping"].get("danh_muc", "")
            entry["mapping"]["danh_muc"] = val
            logger.info("[EDIT-DM] user=%s chat=%s eid=%s %r → %r",
                        user_tag, chat_id, eid, old, val)
            text = _build_card_text(entry)
            kb = _build_card_buttons(eid, entry.get("is_zelle", False), entry.get("loai", "Chi"))
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        return

    # Set PTTT
    if data.startswith("spt_"):
        parts = data[4:].split("_", 1)
        eid, val = parts[0], parts[1]
        entry = _get_entry(chat_id, eid)
        if entry:
            old = entry["mapping"].get("pttt", "")
            entry["mapping"]["pttt"] = val
            logger.info("[EDIT-PTTT] user=%s chat=%s eid=%s %r → %r",
                        user_tag, chat_id, eid, old, val)
            text = _build_card_text(entry)
            kb = _build_card_buttons(eid, entry.get("is_zelle", False), entry.get("loai", "Chi"))
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        return

    # Back to card
    if data.startswith("back_"):
        eid = data[5:]
        entry = _get_entry(chat_id, eid)
        if entry:
            text = _build_card_text(entry)
            kb = _build_card_buttons(eid, entry.get("is_zelle", False), entry.get("loai", "Chi"))
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        return


async def _save_entry(query, entry: dict, eid: str, chat_id: int, user_tag: str = "?"):
    import time
    ocr = entry["ocr"]
    mp = entry["mapping"]
    loai = entry.get("loai", "Chi")
    now = datetime.now(VN_TZ)
    ngay_tt = ocr.get("ngay", now.strftime("%d/%m/%Y"))
    so_tien = ocr.get("so_tien", 0)
    is_zelle = bool(entry.get("is_zelle") and entry.get("zelle"))

    try:
        if is_zelle:
            z = entry["zelle"]
            zelle_note = entry.get("image_url", "") or ocr.get("noi_dung", "")
            logger.info("[SAVE→Lãi tỉ giá] user=%s eid=%s | ngay=%s tk_nhan=%r usd=%s ti_gia=%s total=%s",
                        user_tag, eid, ngay_tt, z.get("tai_khoan_nhan",""),
                        z.get("usd"), z.get("ti_gia_mua"), z.get("total_ck"))
            append_zelle_entry(
                ngay=ngay_tt,
                tai_khoan_nhan=z.get("tai_khoan_nhan", ""),
                total_ck=z.get("total_ck", 0),
                usd=z.get("usd", 0),
                ti_gia_mua=z.get("ti_gia_mua", 0),
                note=zelle_note,
            )
            saved_text = (
                f"✅ Đã lưu vào *Lãi tỉ giá*!\n"
                f"💵 ${z['usd']:g} | Tỉ giá: {_fmt(z['ti_gia_mua'])}"
            )
            so_tien_for_delete = z.get("total_ck", 0)
        else:
            # Use thang_override from caption (e.g. "tháng 3") if available
            thang_override = entry.get("thang_override", "")
            if thang_override:
                thang = thang_override
            else:
                try:
                    thang = str(int(ngay_tt.split("/")[1]))
                except (IndexError, ValueError):
                    thang = str(now.month)

            logger.info("[SAVE→Thu Chi] user=%s eid=%s | thang=%s ngay=%s nn=%r dm=%r nd=%r %s=%s pttt=%r nguoi=%r img=%s",
                        user_tag, eid, thang, ngay_tt,
                        mp.get("nganh_nghe",""), mp.get("danh_muc",""),
                        ocr.get("noi_dung",""), loai, _fmt(so_tien),
                        mp.get("pttt",""), ocr.get("nguoi_nhan",""),
                        bool(entry.get("image_url")))
            append_entries([{
                "thang": thang, "ngay_tt": ngay_tt,
                "nganh_nghe": mp.get("nganh_nghe", ""),
                "danh_muc": mp.get("danh_muc", ""),
                "noi_dung": ocr.get("noi_dung", ""),
                "thu": _fmt(so_tien) if loai == "Thu" else "",
                "chi": _fmt(so_tien) if loai == "Chi" else "",
                "pttt": mp.get("pttt", "") if loai == "Chi" else "",
                "ghi_chu": "",
                "nguoi_nhan": ocr.get("nguoi_nhan", ""),
                "image_url": entry.get("image_url", ""),
            }])
            saved_text = (
                f"✅ Đã lưu vào *Thu Chi*!\n"
                f"{'Thu' if loai == 'Thu' else 'Chi'}: {_fmt(so_tien)} ₫"
            )
            so_tien_for_delete = so_tien

        # Save info for delete
        saved_entries[eid] = {
            "is_zelle": is_zelle,
            "ngay_tt": ngay_tt,
            "so_tien": so_tien_for_delete,
            "noi_dung": ocr.get("noi_dung", ""),
            "nguoi_nhan": ocr.get("nguoi_nhan", ""),
            "saved_at": time.time(),
        }

        # Show saved message with Delete button
        del_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑 Xóa giao dịch", callback_data=f"del_{eid}")
        ]])
        await query.edit_message_text(saved_text, parse_mode="Markdown", reply_markup=del_kb)

        # Schedule removal of delete button after 2 minutes
        asyncio.create_task(_remove_delete_button(query, eid, chat_id, saved_text))

        # Update memory
        nguoi_nhan = ocr.get("nguoi_nhan", "")
        if nguoi_nhan and mp.get("nganh_nghe"):
            try:
                mem_pttt = mp.get("pttt", "") if loai == "Chi" else ""
                memory_upsert(nguoi_nhan, mp["nganh_nghe"], mp.get("danh_muc", ""), mem_pttt)
            except Exception as ex:
                logger.error("Memory upsert error: %s", ex)

        # Cleanup card entry
        if chat_id in entries_store and eid in entries_store[chat_id]:
            del entries_store[chat_id][eid]

        logger.info("[SAVE-OK] user=%s eid=%s sheet=%s",
                    user_tag, eid, "Lãi tỉ giá" if is_zelle else "Thu Chi")

    except Exception as e:
        logger.error("[SAVE-ERR] user=%s eid=%s err=%s", user_tag, eid, e)
        await query.edit_message_text(f"❌ Lỗi lưu: {e}")


async def _delete_saved(info: dict):
    """Delete a saved row from the appropriate sheet."""
    from xuka.services.sheets import _get_client
    from xuka.config import SPREADSHEET_URL
    client = _get_client()
    ss = client.open_by_url(SPREADSHEET_URL)

    target_amt = info["so_tien"]
    target_ngay = info["ngay_tt"].replace("'", "").strip()

    def norm_amt(s: str) -> int:
        s = s.replace(",", "").replace(".", "").replace(" ", "").replace("$", "")
        return int(s) if s.strip() else 0

    logger.info("[DELETE-SEARCH] target_ngay=%r target_amt=%s zelle=%s",
                target_ngay, target_amt, info["is_zelle"])

    if info["is_zelle"]:
        # Search Lãi tỉ giá sheet, clear A-G + J (preserve H-I formulas)
        ws = ss.worksheet("Lãi tỉ giá")
        all_vals = ws.get_all_values()
        for i in range(len(all_vals) - 1, 0, -1):
            row = all_vals[i]
            if len(row) < 7:
                continue
            row_ngay = row[0].replace("'", "").strip()
            try:
                row_amt = norm_amt(row[4])
            except (ValueError, IndexError):
                continue
            if row_ngay == target_ngay and row_amt == target_amt:
                ws.batch_clear([f"A{i+1}:G{i+1}", f"J{i+1}"])
                logger.info("[DELETE-DONE] sheet=Lãi tỉ giá row=%d cleared", i+1)
                return
    else:
        # Search Thu Chi sheet, delete row
        ws = ss.worksheet("Thu Chi")
        all_vals = ws.get_all_values()
        for i in range(len(all_vals) - 1, 0, -1):
            row = all_vals[i]
            if len(row) < 8:
                continue
            row_ngay = row[1].replace("'", "").strip()
            try:
                row_amt = max(norm_amt(row[5]), norm_amt(row[6]))
            except (ValueError, IndexError):
                continue
            if row_ngay == target_ngay and row_amt == target_amt:
                ws.delete_rows(i + 1)
                logger.info("[DELETE-DONE] sheet=Thu Chi row=%d ngay=%r amt=%s pttt=%r nd=%r",
                            i+1, row_ngay, row_amt,
                            row[7] if len(row) > 7 else "",
                            row[4] if len(row) > 4 else "")
                return
    logger.error("[DELETE-NOT-FOUND] target_ngay=%r target_amt=%s zelle=%s — no matching row",
                 target_ngay, target_amt, info["is_zelle"])
    raise ValueError("Không tìm thấy giao dịch trong sheet")


async def _remove_delete_button(query, eid: str, chat_id: int, text: str):
    """Remove delete button after 2 minutes - silently remove button only."""
    await asyncio.sleep(120)
    if eid in saved_entries:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception as e:
            logger.warning("Remove button failed: %s", e)
        del saved_entries[eid]


# ===== Combined Text Handler =====

async def handle_edit_noi_dung(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle text input when user is editing noi_dung."""
    if "editing_noi_dung" not in context.user_data:
        return False
    eid = context.user_data.pop("editing_noi_dung")
    msg = update.message
    new_nd = msg.text.strip()
    entry = _get_entry(msg.chat_id, eid)
    if not entry:
        # Stale editing state (entry already saved/cancelled).
        # Silently fall through to normal text handling instead of nagging the user.
        return False
    u = msg.from_user
    user_tag = f"{u.username or u.first_name or '?'}(id={u.id})"
    old = entry["ocr"].get("noi_dung", "")
    entry["ocr"]["noi_dung"] = new_nd
    logger.info("[EDIT-ND] user=%s chat=%s eid=%s %r → %r",
                user_tag, msg.chat_id, eid, old, new_nd)
    text = _build_card_text(entry)
    kb = _build_card_buttons(eid, entry.get("is_zelle", False), entry.get("loai", "Chi"))
    await msg.reply_text(text, parse_mode="Markdown", reply_markup=kb)
    return True


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route text messages: pending photo > edit noi_dung > normal text."""
    msg = update.message
    chat_id = msg.chat_id
    text = msg.text.strip() if msg.text else ""

    # If there's a pending photo waiting for caption, use this text as caption
    if chat_id in pending_photos and not text.startswith("/"):
        pending = pending_photos.pop(chat_id)
        try:
            pending["task"].cancel()
        except Exception:
            pass
        ocr = pending["ocr"]
        image_url = pending["image_url"]
        progress = pending["progress"]
        original_msg = pending["original_msg"]
        await _process_photo_entry(original_msg, chat_id, ocr, image_url, text, progress)
        return

    if await handle_edit_noi_dung(update, context):
        return
    await handle_text(update, context)


# ===== Main =====

def main():
    if not TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN not set!")
        return

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_handler(CallbackQueryHandler(callback_handler))

    logger.info("🤖 Xuka kế toán Telegram bot starting...")
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
