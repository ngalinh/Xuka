import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "./credentials.json")
SPREADSHEET_URL = os.getenv(
    "SPREADSHEET_URL",
    "https://docs.google.com/spreadsheets/d/1bVQLczc-vYsjc0ngWG2cx8raLDkFjf7CyaNkiXSVRgA/edit",
)
SHEET_NAME = os.getenv("SHEET_NAME", "Thu Chi")

_allowed = os.getenv("ALLOWED_USERS", "")
ALLOWED_USERS: list[int] = [int(uid.strip()) for uid in _allowed.split(",") if uid.strip()]
