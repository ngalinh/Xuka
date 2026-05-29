"""Gemini Vision OCR - đọc screenshot chuyển khoản ngân hàng."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import google.generativeai as genai

from xuka.config import GEMINI_API_KEY

GEMINI_MODEL = "gemini-2.5-flash"
# Hard timeout (seconds) for the Gemini Vision call. Without this the SDK can
# hang indefinitely on a stalled connection and block the bot's event loop.
GEMINI_TIMEOUT_SEC = 60

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

QUAN TRỌNG về NGƯỜI NHẬN (ten_nguoi_nhan) — ĐỌC KỸ:

QUY TẮC VÀNG: Người nhận là tên người **gắn liền với số tài khoản đích** (số tài khoản nhận tiền). KHÔNG BAO GIỜ lấy tên từ dòng memo/nội dung chuyển khoản.

DẤU HIỆU NHẬN BIẾT TÊN TRONG MEMO (KHÔNG được lấy):
- Tên đứng cùng dòng/đoạn có các từ: "chuyen tien", "chuyển tiền", "ck", "ck cho", "thanh toan", "thanh toan ho", "tt", "chuyển khoản"
- VD: "NGUYEN VAN A chuyen tien" → A là NGƯỜI GỬI, KHÔNG phải người nhận
- VD: "ck Tran Thi B" → B có thể là người nhận thật, nhưng đây vẫn là memo, ưu tiên lấy tên gắn với số TK đích

LAYOUT MB BANK CHUẨN (đọc từ trên xuống):
  ✓ Chuyển tiền thành công
  [SỐ TIỀN]
  [Ngày giờ]
       ↓
  [TÊN NGƯỜI NHẬN]            ← LẤY DÒNG NÀY
  [Logo + Tên ngân hàng đích]
  [Số tài khoản đích]
  [Memo: "TÊN NGƯỜI GỬI chuyen tien"]   ← KHÔNG LẤY DÒNG NÀY

VÍ DỤ THỰC TẾ:
Ảnh hiển thị:
  ✓ Chuyển tiền thành công
  6,600,000 VND
  11:15 - 20/04/2026
  ↓
  DINH NGAN NHAT
  MBBank (MB)
  0702499996
  NGUYEN THI PHUONG chuyen tien

→ ĐÚNG: ten_nguoi_nhan = "DINH NGAN NHAT" (đứng ngay trên STK)
→ SAI: ten_nguoi_nhan = "NGUYEN THI PHUONG" (đây là tên trong memo!)

LAYOUT KHÁC (Techcombank, VCB, VPBank...):
- Thường có nhãn rõ ràng: "Người nhận:", "Tên người nhận:", "Đến tài khoản:", "To:", "Recipient:"
- Lấy tên có nhãn rõ ràng. Nếu nhiều tên → lấy tên không nằm trong memo

DOUBLE CHECK trước khi trả về:
1. Tên bạn chọn có nằm trong dòng có "chuyen tien"/"ck"/"thanh toan" không? → Nếu CÓ, chọn lại
2. Tên bạn chọn có gắn với số tài khoản đích không? → Nếu KHÔNG, chọn lại
3. Nếu không có nhãn rõ ràng và không có STK gần đó → ưu tiên tên ở phần trên (gần số tiền) hơn tên ở phần dưới (memo)

Extract chính xác và trả về JSON:
{
  "is_transfer": true,
  "so_tien_vnd": <số tiền VND nếu có, số nguyên không dấu phẩy. Nếu chỉ có USD thì để 0>,
  "so_tien_usd": <số tiền USD nếu có, dạng số thực VD 528.96. Nếu không có thì để 0>,
  "ten_nguoi_nhan": "<TÊN GẮN VỚI SỐ TÀI KHOẢN ĐÍCH, viết hoa đầy đủ - VD: DINH NGAN NHAT. KHÔNG bao giờ lấy tên trong dòng memo có 'chuyen tien'/'ck'/'thanh toan'. Nếu là app Mỹ thì lấy TO field>",
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
    """Gọi Gemini Vision để đọc screenshot chuyển khoản."""
    if not GEMINI_API_KEY:
        raise ValueError("Chưa cấu hình GEMINI_API_KEY")

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(GEMINI_MODEL)
    response = model.generate_content(
        [
            {"mime_type": media_type, "data": image_bytes},
            VISION_PROMPT,
        ],
        generation_config={
            "max_output_tokens": 8192,
            "response_mime_type": "application/json",
            # Disable thinking to prevent it from consuming the token budget
            # before the actual JSON response is generated.
            "thinking_config": {"thinking_budget": 0},
        },
        request_options={"timeout": GEMINI_TIMEOUT_SEC},
    )

    raw = (response.text or "").strip()
    finish_reason = (
        response.candidates[0].finish_reason.name
        if response.candidates
        else "UNKNOWN"
    )
    if finish_reason not in ("STOP", "1"):
        logger.warning("Gemini finish_reason=%s, raw=%r", finish_reason, raw[:300])

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"Không parse được JSON từ OCR: {raw}")

    try:
        parsed = json.loads(match.group())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Không parse được JSON từ OCR: {raw}") from exc

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
