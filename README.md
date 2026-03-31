# Xuka - Bot Telegram Ke Toan

Bot Telegram ghi nhan khoan Thu/Chi cua cong ty vao Google Sheets.

## Cai dat

### 1. Cai dat thu vien

```bash
pip install -r requirements.txt
```

### 2. Tao Telegram Bot

1. Mo Telegram, tim `@BotFather`
2. Gui `/newbot`, dat ten bot la **Xuka**
3. Copy token bot

### 3. Cai dat Google Sheets API

1. Vao [Google Cloud Console](https://console.cloud.google.com/)
2. Tao project moi (hoac chon project co san)
3. Bat **Google Sheets API** va **Google Drive API**
4. Tao **Service Account**, tai file JSON key ve, dat ten la `credentials.json` trong thu muc goc du an
5. Mo Google Spreadsheet, chia se (Share) voi email cua Service Account (quyen **Editor**)

### 4. Cau hinh

Copy file `.env.example` thanh `.env` va dien thong tin:

```bash
cp .env.example .env
```

Sua file `.env`:
```
TELEGRAM_BOT_TOKEN=your-bot-token
GOOGLE_CREDENTIALS_FILE=./credentials.json
SPREADSHEET_URL=https://docs.google.com/spreadsheets/d/1bVQLczc-vYsjc0ngWG2cx8raLDkFjf7CyaNkiXSVRgA/edit
SHEET_NAME=Thu Chi
```

### 5. Tao sheet "Thu Chi"

Trong Google Spreadsheet, tao sheet ten la **Thu Chi** voi header (dong 1):

| A | B | C | D | E | F | G |
|---|---|---|---|---|---|---|
| Ngay | Loai | Danh muc | So tien | Ghi chu | Nguoi nhap | Thoi gian |

### 6. Chay bot

```bash
python -m bot.main
```

## Su dung

- `/start` - Bat dau, xem huong dan
- `/thu` - Nhap khoan thu (income)
- `/chi` - Nhap khoan chi (expense)
- `/huy` - Huy thao tac dang nhap
