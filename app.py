from __future__ import annotations

import base64
import json
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any

import anthropic
from fastapi import FastAPI, File, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from bot.config import ANTHROPIC_API_KEY
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

class ButtonGroup(BaseModel):
    label: str
    options: list[str]
    action: str


class ChatRequest(BaseModel):
    action: str
    state: str = "IDLE"
    data: dict[str, Any] = {}
    input: str = ""


class ChatResponse(BaseModel):
    messages: list[dict[str, str]]
    buttons: list[str] = []
    button_action: str = "select_button"
    button_groups: list[ButtonGroup] = []
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
    ngay = data.get("ngay_tt", datetime.now(VN_TZ).strftime("%d/%m/%Y"))
    return (
        f"{label}:\n"
        f"- Nội dung: {data.get('noi_dung', '')}\n"
        f"- Số tiền: {_format_amount(data.get('so_tien', 0))} VNĐ\n"
        f"- Ngành nghề: {data.get('nganh_nghe', '')}\n"
        f"- Danh mục: {data.get('danh_muc', '')}\n"
        f"- PTTT: {data.get('pttt', '')}\n"
        f"- Ghi chú: {ghi_chu}\n"
        f"- Ngày: {ngay}"
    )


CONFIRM_BUTTONS = [
    "Xác nhận",
    "Sửa Ngành nghề",
    "Sửa Danh mục",
    "Sửa PTTT",
    "Hủy",
]


def _pick_response(data: dict, messages: list[dict[str, str]]) -> ChatResponse:
    """Khi chưa có đủ Ngành nghề/Danh mục/PTTT, hiện tất cả cùng lúc."""
    loai = data.get("loai", "Chi")
    groups: list[ButtonGroup] = []

    if not data.get("nganh_nghe"):
        groups.append(ButtonGroup(label="Ngành nghề", options=get_unique_nganh_nghe(), action="set_nganh_nghe"))
    if not data.get("danh_muc"):
        groups.append(ButtonGroup(label="Danh mục Thu Chi", options=get_unique_danh_muc(loai), action="set_danh_muc"))
    if not data.get("pttt"):
        groups.append(ButtonGroup(label="PTTT", options=get_unique_pttt(), action="set_pttt"))

    if groups:
        messages.append({"text": "Vui lòng chọn các mục sau:", "type": "bot"})
        return ChatResponse(
            messages=messages,
            button_groups=groups,
            state="CHON_NHOM",
            data=data,
        )

    # All filled - show confirmation
    messages.append({"text": _confirmation_text(data), "type": "bot"})
    return ChatResponse(
        messages=messages,
        buttons=CONFIRM_BUTTONS,
        button_action="confirm_action",
        state="XAC_NHAN",
        data=data,
    )


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
                    f"Đã tự động nhận diện:\n"
                    f"- Ngành nghề: {nn}\n"
                    f"- Danh mục: {dm}\n"
                    f"- PTTT: {pt}"
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

        if data.get("nganh_nghe") and data.get("danh_muc") and data.get("pttt"):
            return ChatResponse(
                messages=[{"text": "Nhập ghi chú:", "type": "bot"}],
                buttons=["Bỏ qua"],
                button_action="skip_note",
                state="GHI_CHU",
                data=data,
                show_input=True,
                input_placeholder="Ghi chú (hoặc bấm Bỏ qua)",
            )

        return _pick_response(data, [])

    # --- CHON_NHOM (pick from grouped buttons) ---
    if state == "CHON_NHOM" and action in ("set_nganh_nghe", "set_danh_muc", "set_pttt"):
        if action == "set_nganh_nghe":
            data["nganh_nghe"] = user_input
        elif action == "set_danh_muc":
            data["danh_muc"] = user_input
        elif action == "set_pttt":
            data["pttt"] = user_input

        # Check if all filled
        if data.get("nganh_nghe") and data.get("danh_muc") and data.get("pttt"):
            return ChatResponse(
                messages=[{"text": "Nhập ghi chú:", "type": "bot"}],
                buttons=["Bỏ qua"],
                button_action="skip_note",
                state="GHI_CHU",
                data=data,
                show_input=True,
                input_placeholder="Ghi chú (hoặc bấm Bỏ qua)",
            )

        # Still missing some - show remaining groups
        return _pick_response(data, [{"text": f"Đã chọn: {user_input}", "type": "bot"}])

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
        if user_input == "Xác nhận":
            return await _save(data)
        if user_input == "Hủy":
            return ChatResponse(
                messages=[{"text": "Đã hủy thao tác.", "type": "bot"}],
                state="IDLE",
                data={},
                done=True,
            )
        if user_input == "Sửa Ngành nghề":
            return ChatResponse(
                messages=[{"text": "Chọn Ngành nghề:", "type": "bot"}],
                buttons=get_unique_nganh_nghe(),
                button_action="edit_nganh_nghe",
                state="XAC_NHAN",
                data=data,
            )
        if user_input == "Sửa Danh mục":
            loai = data.get("loai", "Chi")
            return ChatResponse(
                messages=[{"text": "Chọn Danh mục:", "type": "bot"}],
                buttons=get_unique_danh_muc(loai),
                button_action="edit_danh_muc",
                state="XAC_NHAN",
                data=data,
            )
        if user_input == "Sửa PTTT":
            return ChatResponse(
                messages=[{"text": "Chọn PTTT:", "type": "bot"}],
                buttons=get_unique_pttt(),
                button_action="edit_pttt",
                state="XAC_NHAN",
                data=data,
            )

    # --- Edit callbacks ---
    if state == "XAC_NHAN" and action in ("edit_nganh_nghe", "edit_danh_muc", "edit_pttt"):
        if action == "edit_nganh_nghe":
            data["nganh_nghe"] = user_input
        elif action == "edit_danh_muc":
            data["danh_muc"] = user_input
        elif action == "edit_pttt":
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
    ngay_tt = data.get("ngay_tt", now.strftime("%d/%m/%Y"))
    try:
        thang = str(int(ngay_tt.split("/")[1]))
    except (IndexError, ValueError):
        thang = str(now.month)

    try:
        append_entry(
            thang=thang, ngay_tt=ngay_tt,
            nganh_nghe=data.get("nganh_nghe", ""),
            danh_muc=data.get("danh_muc", ""),
            noi_dung=data.get("noi_dung", ""),
            thu=thu, chi=chi,
            pttt=data.get("pttt", ""),
            ghi_chu=data.get("ghi_chu", ""),
        )
        return ChatResponse(
            messages=[{"text": "Đã lưu thành công vào bảng tính!", "type": "bot"}],
            state="IDLE", data={}, done=True,
        )
    except Exception as e:
        logger.error("Lỗi khi lưu: %s", e)
        return ChatResponse(
            messages=[{"text": f"Lỗi khi lưu: {e}\nVui lòng thử lại.", "type": "bot"}],
            state="IDLE", data={}, done=True,
        )


# --- Image upload endpoint ---

VISION_PROMPT = """Phân tích hình ảnh chuyển khoản ngân hàng này. Trích xuất thông tin sau và trả về dạng JSON:
{
  "so_tien": <số tiền chuyển, chỉ số nguyên không dấu phẩy>,
  "noi_dung": "<nội dung chuyển khoản / ghi chú giao dịch>",
  "ngay": "<ngày giao dịch dạng DD/MM/YYYY>",
  "ngan_hang": "<tên ngân hàng nguồn (nơi chuyển)>",
  "nguoi_nhan": "<tên người nhận>"
}

Chỉ trả về JSON, không có text khác. Nếu không đọc được trường nào thì để chuỗi rỗng."""


@app.post("/api/upload")
async def upload_image(file: UploadFile = File(...)):
    if not ANTHROPIC_API_KEY:
        return ChatResponse(
            messages=[{"text": "Chưa cấu hình API key để đọc hình ảnh.", "type": "bot"}],
            state="IDLE", data={}, done=True,
        )

    contents = await file.read()
    b64_image = base64.b64encode(contents).decode("utf-8")
    media_type = file.content_type or "image/png"

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64_image}},
                    {"type": "text", "text": VISION_PROMPT},
                ],
            }],
        )
        raw_text = response.content[0].text.strip()
        json_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if not json_match:
            raise ValueError(f"Không parse được JSON: {raw_text}")
        parsed = json.loads(json_match.group())
    except Exception as e:
        logger.error("Lỗi đọc hình: %s", e)
        return ChatResponse(
            messages=[{"text": f"Không thể đọc hình ảnh: {e}", "type": "bot"}],
            state="IDLE", data={}, done=True,
        )

    so_tien = int(parsed.get("so_tien", 0))
    noi_dung = parsed.get("noi_dung", "").strip()
    ngay = parsed.get("ngay", datetime.now(VN_TZ).strftime("%d/%m/%Y"))
    ngan_hang = parsed.get("ngan_hang", "")
    nguoi_nhan = parsed.get("nguoi_nhan", "")

    data: dict[str, Any] = {
        "loai": "Chi",
        "noi_dung": noi_dung or f"CK cho {nguoi_nhan}",
        "so_tien": so_tien,
        "ghi_chu": "",
        "ngay_tt": ngay,
    }

    messages: list[dict[str, str]] = [{
        "text": (
            f"Đã đọc hình chuyển khoản:\n"
            f"- Số tiền: {_format_amount(so_tien)} VNĐ\n"
            f"- Nội dung: {noi_dung}\n"
            f"- Ngày: {ngay}\n"
            f"- Ngân hàng: {ngan_hang}\n"
            f"- Người nhận: {nguoi_nhan}"
        ),
        "type": "bot",
    }]

    # Auto-map from history
    search_text = noi_dung or nguoi_nhan
    mapping = find_mapping(search_text)
    if mapping:
        nn, dm, pt = mapping
        data["nganh_nghe"] = nn
        data["danh_muc"] = dm
        data["pttt"] = pt
        messages.append({
            "text": f"Đã tự động nhận diện:\n- Ngành nghề: {nn}\n- Danh mục: {dm}\n- PTTT: {pt}",
            "type": "bot",
        })
    else:
        bank_pttt_map = {
            "MB": "MB LB", "MBBank": "MB LB", "MB Bank": "MB LB",
            "Techcombank": "Tech Dzuong", "TCB": "Tech Dzuong",
            "VPBank": "VP Dzuong",
        }
        for key, val in bank_pttt_map.items():
            if key.lower() in ngan_hang.lower():
                data["pttt"] = val
                break

    return _pick_response(data, messages)


# Mount static files (must be last)
app.mount("/", StaticFiles(directory="static", html=True), name="static")
