from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from bot.services.sheets import (
    append_entry,
    find_mapping,
    get_unique_nganh_nghe,
    get_unique_danh_muc,
    get_unique_pttt,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

VN_TZ = timezone(timedelta(hours=7))

app = FastAPI(title="Xuka - Trợ lý kế toán")


# --- Models ---

class ChatRequest(BaseModel):
    action: str  # start_thu, start_chi, input_text, select_button, confirm, cancel
    state: str = "IDLE"
    data: dict[str, Any] = {}
    input: str = ""


class ChatResponse(BaseModel):
    messages: list[dict[str, str]]
    buttons: list[str] = []
    button_action: str = "select_button"
    state: str = "IDLE"
    data: dict[str, Any] = {}
    done: bool = False
    show_input: bool = False
    input_placeholder: str = ""


# --- Helpers ---

def _parse_amount(text: str) -> int | None:
    cleaned = text.strip().lower()
    multiplier = 1
    if cleaned.endswith("k"):
        cleaned = cleaned[:-1]
        multiplier = 1000
    cleaned = cleaned.replace(",", "").replace(".", "")
    if cleaned.isdigit() and int(cleaned) > 0:
        return int(cleaned) * multiplier
    return None


def _format_amount(amount: int) -> str:
    return f"{amount:,}".replace(",", ".")


def _confirmation_text(data: dict) -> str:
    loai = data.get("loai", "")
    label = "KHOẢN THU" if loai == "Thu" else "KHOẢN CHI"
    ghi_chu = data.get("ghi_chu") or "(không)"
    now = datetime.now(VN_TZ)
    return (
        f"📋 {label}:\n"
        f"• Nội dung: {data.get('noi_dung', '')}\n"
        f"• Số tiền: {_format_amount(data.get('so_tien', 0))} VNĐ\n"
        f"• Ngành nghề: {data.get('nganh_nghe', '')}\n"
        f"• Danh mục: {data.get('danh_muc', '')}\n"
        f"• PTTT: {data.get('pttt', '')}\n"
        f"• Ghi chú: {ghi_chu}\n"
        f"• Ngày: {now.strftime('%d/%m/%Y')}"
    )


CONFIRM_BUTTONS = [
    "✅ Xác nhận",
    "📝 Sửa Ngành nghề",
    "📝 Sửa Danh mục",
    "📝 Sửa PTTT",
    "❌ Hủy",
]


# --- Chat endpoint ---

@app.post("/api/chat")
async def chat(req: ChatRequest) -> ChatResponse:
    state = req.state
    data = dict(req.data)
    action = req.action
    user_input = req.input.strip()

    # --- START ---
    if action in ("start_thu", "start_chi"):
        loai = "Thu" if action == "start_thu" else "Chi"
        data = {"loai": loai}
        label = "thu" if loai == "Thu" else "chi"
        return ChatResponse(
            messages=[{"text": f"Nhập nội dung khoản {label}:", "type": "bot"}],
            state="NOI_DUNG",
            data=data,
            show_input=True,
            input_placeholder="VD: Tiền điện, Khách hàng trả tiền...",
        )

    # --- NOI_DUNG ---
    if state == "NOI_DUNG" and action == "input_text":
        data["noi_dung"] = user_input
        messages: list[dict[str, str]] = []

        mapping = find_mapping(user_input)
        if mapping:
            nn, dm, pt = mapping
            data["nganh_nghe"] = nn
            data["danh_muc"] = dm
            data["pttt"] = pt
            messages.append({
                "text": (
                    f"Đã tự động nhận diện từ lịch sử:\n"
                    f"• Ngành nghề: {nn}\n"
                    f"• Danh mục: {dm}\n"
                    f"• PTTT: {pt}"
                ),
                "type": "bot",
            })

        messages.append({"text": "Nhập số tiền (đơn vị VNĐ):", "type": "bot"})
        return ChatResponse(
            messages=messages,
            state="SO_TIEN",
            data=data,
            show_input=True,
            input_placeholder="VD: 500000, 500k, 1.500.000",
        )

    # --- SO_TIEN ---
    if state == "SO_TIEN" and action == "input_text":
        amount = _parse_amount(user_input)
        if amount is None:
            return ChatResponse(
                messages=[{"text": "Số tiền không hợp lệ. Vui lòng nhập lại:", "type": "bot"}],
                state="SO_TIEN",
                data=data,
                show_input=True,
                input_placeholder="Chỉ nhập số, VD: 500000",
            )
        data["so_tien"] = amount

        # If auto-mapped, skip to ghi chu
        if data.get("nganh_nghe"):
            return ChatResponse(
                messages=[{"text": "Nhập ghi chú:", "type": "bot"}],
                buttons=["Bỏ qua"],
                button_action="skip_note",
                state="GHI_CHU",
                data=data,
                show_input=True,
                input_placeholder="Ghi chú (hoặc bấm Bỏ qua)",
            )

        # Otherwise ask nganh nghe
        options = get_unique_nganh_nghe()
        return ChatResponse(
            messages=[{"text": "Chọn Ngành nghề:", "type": "bot"}],
            buttons=options,
            state="NGANH_NGHE",
            data=data,
        )

    # --- NGANH_NGHE ---
    if state == "NGANH_NGHE" and action == "select_button":
        data["nganh_nghe"] = user_input
        loai = data.get("loai", "Chi")
        options = get_unique_danh_muc(loai)
        return ChatResponse(
            messages=[{"text": "Chọn Danh mục Thu Chi:", "type": "bot"}],
            buttons=options,
            state="DANH_MUC",
            data=data,
        )

    # --- DANH_MUC ---
    if state == "DANH_MUC" and action == "select_button":
        data["danh_muc"] = user_input
        options = get_unique_pttt()
        return ChatResponse(
            messages=[{"text": "Chọn Phương thức thanh toán (PTTT):", "type": "bot"}],
            buttons=options,
            state="PTTT",
            data=data,
        )

    # --- PTTT ---
    if state == "PTTT" and action == "select_button":
        data["pttt"] = user_input
        return ChatResponse(
            messages=[{"text": "Nhập ghi chú:", "type": "bot"}],
            buttons=["Bỏ qua"],
            button_action="skip_note",
            state="GHI_CHU",
            data=data,
            show_input=True,
            input_placeholder="Ghi chú (hoặc bấm Bỏ qua)",
        )

    # --- GHI_CHU ---
    if state == "GHI_CHU" and action in ("input_text", "skip_note"):
        data["ghi_chu"] = "" if action == "skip_note" else user_input
        return ChatResponse(
            messages=[{"text": _confirmation_text(data), "type": "bot"}],
            buttons=CONFIRM_BUTTONS,
            button_action="confirm_action",
            state="XAC_NHAN",
            data=data,
        )

    # --- XAC_NHAN ---
    if state == "XAC_NHAN" and action == "confirm_action":
        if user_input == "✅ Xác nhận":
            return await _save(data)
        if user_input == "❌ Hủy":
            return ChatResponse(
                messages=[{"text": "Đã hủy thao tác.", "type": "bot"}],
                state="IDLE",
                data={},
                done=True,
            )
        if user_input == "📝 Sửa Ngành nghề":
            options = get_unique_nganh_nghe()
            return ChatResponse(
                messages=[{"text": "Chọn Ngành nghề:", "type": "bot"}],
                buttons=options,
                button_action="edit_nganh_nghe",
                state="XAC_NHAN",
                data=data,
            )
        if user_input == "📝 Sửa Danh mục":
            loai = data.get("loai", "Chi")
            options = get_unique_danh_muc(loai)
            return ChatResponse(
                messages=[{"text": "Chọn Danh mục Thu Chi:", "type": "bot"}],
                buttons=options,
                button_action="edit_danh_muc",
                state="XAC_NHAN",
                data=data,
            )
        if user_input == "📝 Sửa PTTT":
            options = get_unique_pttt()
            return ChatResponse(
                messages=[{"text": "Chọn PTTT:", "type": "bot"}],
                buttons=options,
                button_action="edit_pttt",
                state="XAC_NHAN",
                data=data,
            )

    # --- Edit callbacks ---
    if state == "XAC_NHAN" and action == "edit_nganh_nghe":
        data["nganh_nghe"] = user_input
        return ChatResponse(
            messages=[{"text": _confirmation_text(data), "type": "bot"}],
            buttons=CONFIRM_BUTTONS,
            button_action="confirm_action",
            state="XAC_NHAN",
            data=data,
        )

    if state == "XAC_NHAN" and action == "edit_danh_muc":
        data["danh_muc"] = user_input
        return ChatResponse(
            messages=[{"text": _confirmation_text(data), "type": "bot"}],
            buttons=CONFIRM_BUTTONS,
            button_action="confirm_action",
            state="XAC_NHAN",
            data=data,
        )

    if state == "XAC_NHAN" and action == "edit_pttt":
        data["pttt"] = user_input
        return ChatResponse(
            messages=[{"text": _confirmation_text(data), "type": "bot"}],
            buttons=CONFIRM_BUTTONS,
            button_action="confirm_action",
            state="XAC_NHAN",
            data=data,
        )

    # Fallback
    return ChatResponse(
        messages=[{"text": "Chọn khoản Thu hoặc Chi để bắt đầu.", "type": "bot"}],
        state="IDLE",
        data={},
    )


async def _save(data: dict) -> ChatResponse:
    now = datetime.now(VN_TZ)
    loai = data.get("loai", "Chi")
    so_tien_str = _format_amount(data.get("so_tien", 0))
    thu = so_tien_str if loai == "Thu" else ""
    chi = so_tien_str if loai == "Chi" else ""

    try:
        append_entry(
            thang=str(now.month),
            ngay_tt=now.strftime("%d/%m/%Y"),
            nganh_nghe=data.get("nganh_nghe", ""),
            danh_muc=data.get("danh_muc", ""),
            noi_dung=data.get("noi_dung", ""),
            thu=thu,
            chi=chi,
            pttt=data.get("pttt", ""),
            ghi_chu=data.get("ghi_chu", ""),
        )
        return ChatResponse(
            messages=[{"text": "✅ Đã lưu thành công vào bảng tính!", "type": "bot"}],
            state="IDLE",
            data={},
            done=True,
        )
    except Exception as e:
        logger.error("Lỗi khi lưu: %s", e)
        return ChatResponse(
            messages=[{"text": f"❌ Lỗi khi lưu: {e}\nVui lòng thử lại.", "type": "bot"}],
            state="IDLE",
            data={},
            done=True,
        )


# Mount static files (must be last)
app.mount("/", StaticFiles(directory="static", html=True), name="static")
