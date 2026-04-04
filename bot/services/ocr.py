"""Claude Vision OCR - đọc screenshot chuyển khoản ngân hàng."""
from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass

import anthropic

from bot.config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

VISION_PROMPT = """Bạn là trợ lý kế toán. Phân tích hình ảnh chuyển khoản ngân hàng Việt Nam.

Extract chính xác và trả về JSON:
{
  "so_tien": <số tiền, chỉ số nguyên không dấu phẩy>,
  "ten_nguoi_nhan": "<tên người nhận viết hoa>",
  "ngan_hang": "<tên ngân hàng viết tắt: MB/VPBank/ACB/TCB/VCB/BIDV/Agribank...>",
  "ngay": "<ngày giao dịch dạng DD/MM/YYYY>",
  "noi_dung_ck": "<nội dung chuyển khoản gốc>",
  "loai": "<'chi' nếu tiền đi ra, 'thu' nếu tiền vào>"
}

Chỉ trả về JSON, không giải thích thêm. Nếu không đọc được trường nào thì để chuỗi rỗng."""


@dataclass
class OCRResult:
    so_tien: int
    nguoi_nhan: str
    ngan_hang: str
    ngay: str
    noi_dung: str
    loai: str  # "thu" or "chi"


def extract_transfer(image_bytes: bytes, media_type: str) -> OCRResult:
    """Gọi Claude Vision để đọc screenshot chuyển khoản."""
    if not ANTHROPIC_API_KEY:
        raise ValueError("Chưa cấu hình ANTHROPIC_API_KEY")

    b64 = base64.b64encode(image_bytes).decode("utf-8")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                {"type": "text", "text": VISION_PROMPT},
            ],
        }],
    )

    raw = response.content[0].text.strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"Không parse được JSON từ OCR: {raw}")

    parsed = json.loads(match.group())

    return OCRResult(
        so_tien=int(parsed.get("so_tien", 0)),
        nguoi_nhan=parsed.get("ten_nguoi_nhan", "").strip(),
        ngan_hang=parsed.get("ngan_hang", "").strip(),
        ngay=parsed.get("ngay", "").strip(),
        noi_dung=parsed.get("noi_dung_ck", "").strip(),
        loai=parsed.get("loai", "chi").strip().lower(),
    )
