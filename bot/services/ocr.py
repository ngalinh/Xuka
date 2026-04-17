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

VISION_PROMPT = """Bạn là trợ lý kế toán. Phân tích hình ảnh.

BƯỚC 1 - KIỂM TRA LOẠI ẢNH:
Xác định ảnh có phải là screenshot chuyển khoản ngân hàng/app tài chính hay không.

Ảnh CHUYỂN KHOẢN hợp lệ phải có ÍT NHẤT các đặc điểm:
- Từ app ngân hàng VN (MB, VCB, TCB, VPBank, ACB, Sacombank, BIDV...) hoặc app quốc tế (Zelle, Cash App, Venmo, PayPal, Wise, Remitly, Chase, BoA...)
- Có số tiền giao dịch rõ ràng
- Có thông tin người nhận HOẶC người gửi
- Có dấu hiệu giao dịch thành công / biên lai / xác nhận chuyển tiền

Nếu KHÔNG phải ảnh chuyển khoản (VD: meme, ảnh chat, ảnh sản phẩm, ảnh cá nhân, screenshot khác, ảnh món ăn, ảnh phong cảnh...):
→ Trả về CHÍNH XÁC: {"is_transfer": false}
→ KHÔNG trả về gì khác.

Nếu ĐÚNG là ảnh chuyển khoản, tiếp tục BƯỚC 2.

BƯỚC 2 - EXTRACT DỮ LIỆU:

QUAN TRỌNG về Thu/Chi:
- Hầu hết screenshot CK là "Chi" (tiền đi ra). Nếu không rõ → "chi"
- Chỉ chọn "thu" nếu thấy rõ "Nhận tiền", "Tiền vào", "Đã nhận", "Cộng vào tài khoản"

QUAN TRỌNG về Loại tiền (USD vs VND):
- Nếu ảnh là app Mỹ/quốc tế (Zelle, Cash App, Venmo, PayPal, Wise, Remitly...) hoặc số tiền có ký hiệu $ → là USD
- Nếu ảnh là ngân hàng Việt Nam (MB, VCB, TCB, VPBank, ACB...) → là VND
- Nếu số tiền có format X,XXX.XX (dấu chấm thập phân 2 số) và có $ → USD
- Nếu số tiền có format X,XXX,XXX hoặc X.XXX.XXX không thập phân → VND

QUAN TRỌNG về NGƯỜI NHẬN (ten_nguoi_nhan):
- Người nhận = người/tài khoản NHẬN TIỀN, thường đi kèm số tài khoản đích và tên ngân hàng đích
- TUYỆT ĐỐI KHÔNG lấy tên từ memo / nội dung chuyển khoản. Memo thường có dạng "NGUYEN VAN A chuyen tien" — đây là NGƯỜI GỬI chứ không phải người nhận
- Trong app MB: layout thường là [Tên người nhận] → [Ngân hàng đích + Số TK] → [Nội dung memo]
- Nếu không chắc chắn đâu là người nhận, lấy tên đứng gần số tài khoản đích nhất
- Bỏ qua tên nào xuất hiện trong dòng có chữ "chuyen tien", "ck", "chuyển tiền", "thanh toan"

Extract chính xác và trả về JSON:
{
  "is_transfer": true,
  "so_tien_vnd": <số tiền VND nếu có, số nguyên không dấu phẩy. Nếu chỉ có USD thì để 0>,
  "so_tien_usd": <số tiền USD nếu có, dạng số thực VD 528.96. Nếu không có thì để 0>,
  "ten_nguoi_nhan": "<tên người NHẬN TIỀN đầy đủ, viết hoa - VD: NGUYEN VAN A. Phải là tên gắn với số TK đích, KHÔNG phải tên trong memo. Nếu là app Mỹ thì lấy TO field>",
  "ngan_hang": "<tên ngân hàng/app: MB/VPBank/TCB/Zelle/Cash App/PayPal/Wise...>",
  "ngay": "<ngày giao dịch dạng DD/MM/YYYY. Nếu thấy 'Today' thì để chuỗi rỗng>",
  "noi_dung_ck": "<nội dung chuyển khoản / memo / confirmation code - giữ nguyên văn>",
  "loai": "chi hoặc thu"
}

Lưu ý:
- "ten_nguoi_nhan" phải là tên người, KHÔNG phải tên địa danh, KHÔNG phải tên trong memo
- Chỉ trả về JSON, không giải thích thêm"""


@dataclass
class OCRResult:
    so_tien: int  # VND amount (after conversion if USD)
    nguoi_nhan: str
    ngan_hang: str
    ngay: str
    noi_dung: str
    loai: str  # "thu" or "chi"
    so_tien_usd: float = 0.0  # Original USD amount if applicable
    is_usd_payment: bool = False
    is_transfer: bool = True  # False if image is not a bank transfer screenshot


# Exchange rate for USD → VND
USD_TO_VND_RATE = 26000


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

    # Check if image is actually a bank transfer screenshot
    if parsed.get("is_transfer") is False:
        logger.info("Image is not a bank transfer screenshot, skipping")
        return OCRResult(
            so_tien=0, nguoi_nhan="", ngan_hang="", ngay="",
            noi_dung="", loai="chi", is_transfer=False,
        )

    so_tien_vnd = int(parsed.get("so_tien_vnd", 0) or 0)
    so_tien_usd = float(parsed.get("so_tien_usd", 0) or 0)
    is_usd = so_tien_usd > 0

    # If USD payment, convert to VND at fixed rate
    if is_usd:
        so_tien_final = round(so_tien_usd * USD_TO_VND_RATE)
    else:
        so_tien_final = so_tien_vnd

    return OCRResult(
        so_tien=so_tien_final,
        nguoi_nhan=parsed.get("ten_nguoi_nhan", "").strip(),
        ngan_hang=parsed.get("ngan_hang", "").strip(),
        ngay=parsed.get("ngay", "").strip(),
        noi_dung=parsed.get("noi_dung_ck", "").strip(),
        loai=parsed.get("loai", "chi").strip().lower(),
        so_tien_usd=so_tien_usd,
        is_usd_payment=is_usd,
        is_transfer=True,
    )
