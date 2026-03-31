"""Conversation handler for both Thu (income) and Chi (expense) entries."""

from datetime import datetime, timezone, timedelta

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot.services.sheets import append_entry

# Conversation states
DANH_MUC, SO_TIEN, GHI_CHU, XAC_NHAN = range(4)

VN_TZ = timezone(timedelta(hours=7))


def _parse_amount(text: str) -> int | None:
    """Parse amount string, stripping commas/dots used as thousand separators."""
    cleaned = text.strip().replace(",", "").replace(".", "")
    if cleaned.isdigit() and int(cleaned) > 0:
        return int(cleaned)
    return None


def _format_amount(amount: int) -> str:
    return f"{amount:,}".replace(",", ".")


async def _start_entry(update: Update, context: ContextTypes.DEFAULT_TYPE, loai: str) -> int:
    context.user_data["loai"] = loai
    label = "thu" if loai == "Thu" else "chi"
    await update.message.reply_text(
        f"Nhap DANH MUC khoan {label}:\n"
        f"(VD: {'Khach hang tra tien, Lai ngan hang' if loai == 'Thu' else 'Tien dien, Van phong pham'}...)"
    )
    return DANH_MUC


async def start_thu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _start_entry(update, context, "Thu")


async def start_chi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _start_entry(update, context, "Chi")


async def danh_muc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["danh_muc"] = update.message.text.strip()
    await update.message.reply_text("Nhap SO TIEN (chi nhap so, don vi VND):")
    return SO_TIEN


async def so_tien(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    amount = _parse_amount(update.message.text)
    if amount is None:
        await update.message.reply_text("So tien khong hop le. Vui long nhap lai (chi nhap so):")
        return SO_TIEN
    context.user_data["so_tien"] = amount
    await update.message.reply_text("Nhap GHI CHU (hoac gui /boqua de bo qua):")
    return GHI_CHU


async def ghi_chu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    context.user_data["ghi_chu"] = "" if text == "/boqua" else text
    return await _show_confirmation(update, context)


async def _show_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = context.user_data
    loai = data["loai"]
    label = "KHOAN THU" if loai == "Thu" else "KHOAN CHI"
    now = datetime.now(VN_TZ)

    await update.message.reply_text(
        f"Da ghi nhan {label}:\n"
        f"- Danh muc: {data['danh_muc']}\n"
        f"- So tien: {_format_amount(data['so_tien'])} VND\n"
        f"- Ghi chu: {data.get('ghi_chu') or '(khong)'}\n"
        f"- Ngay: {now.strftime('%d/%m/%Y')}\n\n"
        f"Xac nhan? (Co / Khong)",
        reply_markup=ReplyKeyboardMarkup([["Co", "Khong"]], one_time_keyboard=True, resize_keyboard=True),
    )
    return XAC_NHAN


async def xac_nhan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    answer = update.message.text.strip().lower()
    if answer not in ("co", "có"):
        await update.message.reply_text("Da huy thao tac.", reply_markup=ReplyKeyboardRemove())
        context.user_data.clear()
        return ConversationHandler.END

    data = context.user_data
    now = datetime.now(VN_TZ)
    user = update.effective_user
    nguoi_nhap = f"@{user.username}" if user.username else user.full_name

    try:
        append_entry(
            ngay=now.strftime("%d/%m/%Y"),
            loai=data["loai"],
            danh_muc=data["danh_muc"],
            so_tien=str(data["so_tien"]),
            ghi_chu=data.get("ghi_chu", ""),
            nguoi_nhap=nguoi_nhap,
            thoi_gian=now.strftime("%d/%m/%Y %H:%M:%S"),
        )
        await update.message.reply_text(
            "Da luu thanh cong vao bang tinh!",
            reply_markup=ReplyKeyboardRemove(),
        )
    except Exception as e:
        await update.message.reply_text(
            f"Loi khi luu du lieu: {e}\nVui long thu lai.",
            reply_markup=ReplyKeyboardRemove(),
        )

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Da huy thao tac.", reply_markup=ReplyKeyboardRemove())
    context.user_data.clear()
    return ConversationHandler.END


def build_entry_handler() -> ConversationHandler:
    """Build a single ConversationHandler for both /thu and /chi."""
    return ConversationHandler(
        entry_points=[
            CommandHandler("thu", start_thu),
            CommandHandler("chi", start_chi),
        ],
        states={
            DANH_MUC: [MessageHandler(filters.TEXT & ~filters.COMMAND, danh_muc)],
            SO_TIEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, so_tien)],
            GHI_CHU: [
                CommandHandler("boqua", ghi_chu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ghi_chu),
            ],
            XAC_NHAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, xac_nhan)],
        },
        fallbacks=[CommandHandler("huy", cancel)],
    )
