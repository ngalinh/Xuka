# Xuka - Bot Telegram Kế Toán

Bot Telegram đọc screenshot chuyển khoản (Claude Vision OCR), tự động phân loại Ngành nghề / Danh mục / PTTT và ghi vào Google Sheets.

## Tính năng

- **OCR ảnh chuyển khoản**: Đọc tự động số tiền, người nhận, ngày, nội dung từ ảnh CK của các app ngân hàng VN (MB, VCB, TCB, VPBank, ACB...) và app quốc tế (Zelle, Cash App, PayPal, Wise...).
- **Auto mapping**: Tự gợi ý Ngành nghề / Danh mục / PTTT dựa trên lịch sử trong Google Sheet và bộ nhớ học (Bot Memory).
- **Caption ghi chú**: Gửi ảnh kèm caption để override nội dung. Hỗ trợ override tháng (`tháng 3`).
- **USD payment**: Tự động convert USD → VND (× 26,000) và map vào Vận chuyển quốc tế.
- **Zelle**: Detect caption `mua $116 zelle` → ghi vào sheet `Lãi tỉ giá`.
- **Media group (album)**: Gửi nhiều ảnh cùng caption → chia sẻ caption cho cả album.
- **Group chat**: Tự lọc tin user-to-user, chỉ xử lý khi tag bot hoặc reply.
- **Xóa giao dịch**: Nút xóa trong vòng 2 phút sau khi lưu.
- **Chấm công** (`/chamcong`): Form chọn Chi nhánh / Nhân viên / Danh mục / Thời gian / Ghi chú từ inline buttons → ghi vào sheet `Chấm công` (file Google Sheet riêng — config qua `CHAM_CONG_SPREADSHEET_URL`).

## Cài đặt

### 1. Cài thư viện

```bash
pip install -r requirements.txt
```

### 2. Tạo Telegram Bot

1. Mở Telegram, tìm `@BotFather`
2. Gửi `/newbot`, đặt tên bot
3. Copy token

### 3. Cài Google Sheets API

1. Vào [Google Cloud Console](https://console.cloud.google.com/)
2. Tạo project, bật **Google Sheets API** + **Google Drive API**
3. Tạo **Service Account**, tải file JSON key về, đặt tên `credentials.json` ở thư mục gốc
4. Mở Google Spreadsheet, share với email của Service Account (quyền **Editor**)

### 4. Lấy Anthropic API Key

Vào [console.anthropic.com](https://console.anthropic.com/) tạo API key.

### 5. Cấu hình `.env`

```bash
cp .env.example .env
```

Sửa `.env`:
```
TELEGRAM_BOT_TOKEN=<bot-token>
ANTHROPIC_API_KEY=<anthropic-key>
GOOGLE_CREDENTIALS_FILE=./credentials.json
SPREADSHEET_URL=https://docs.google.com/spreadsheets/d/.../edit
SHEET_NAME=Thu Chi
# Tùy chọn: file Google Sheet riêng cho sheet "Chấm công"
CHAM_CONG_SPREADSHEET_URL=https://docs.google.com/spreadsheets/d/.../edit
```

### 6. Cấu trúc Google Sheet

Sheet `Thu Chi` (header dòng 1):

| A | B | C | D | E | F | G | H | I |
|---|---|---|---|---|---|---|---|---|
| Tháng | Ngày TT | Ngành nghề | Danh mục | Nội dung | Thu | Chi | PTTT | Ghi chú |

(Tùy chọn) Sheet `Lãi tỉ giá` cho zelle, sheet `Bot Memory` cho bộ nhớ học.

## Chạy bot

```bash
python telegram_bot.py
```

Bot dùng polling mode — chỉ cần host nào chạy được Python liên tục là được.

## Deploy

Bot chạy polling, không cần webhook/port. Chỉ cần:
- Python 3.9+
- Cài `requirements.txt`
- Set env vars (hoặc upload `.env` + `credentials.json`)
- Chạy `python telegram_bot.py` dưới dạng background service / systemd / PM2 / Docker / tmux

Có thể dùng env var `GOOGLE_CREDENTIALS_JSON` (raw JSON hoặc base64) thay cho file `credentials.json` trong môi trường hosted.

## Sử dụng

- `/start` — bắt đầu
- `/help` — xem hướng dẫn đầy đủ
- Gửi ảnh CK → bot đọc và hiện card xác nhận → bấm Lưu
- Gửi ảnh + caption để thêm ghi chú / override tháng / khai báo zelle
- Gửi text "Chi văn phòng 2tr" để nhập thủ công
