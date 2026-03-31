from telegram import Update
from telegram.ext import ContextTypes


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Xin chao! Toi la Xuka - tro ly ke toan.\n\n"
        "Cac lenh:\n"
        "/thu - Ghi nhan khoan thu (income)\n"
        "/chi - Ghi nhan khoan chi (expense)\n"
        "/huy - Huy thao tac hien tai"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Huong dan su dung Xuka:\n\n"
        "/thu - Nhap khoan thu moi\n"
        "/chi - Nhap khoan chi moi\n"
        "/huy - Huy thao tac dang nhap\n\n"
        "Sau khi chon /thu hoac /chi, ban se nhap:\n"
        "1. Danh muc\n"
        "2. So tien (VND)\n"
        "3. Ghi chu\n"
        "4. Xac nhan de luu vao bang tinh"
    )
