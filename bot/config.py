from __future__ import annotations

import os
import json
import base64
import tempfile

from dotenv import load_dotenv

load_dotenv()


def _resolve_credentials_file() -> str:
    path = os.getenv("GOOGLE_CREDENTIALS_FILE", "./credentials.json")
    if os.path.exists(path):
        return path
    # Fallback: base64-encoded JSON in env var (for cloud deployment)
    encoded = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
    if encoded:
        data = base64.b64decode(encoded)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        tmp.write(data)
        tmp.close()
        return tmp.name
    raise FileNotFoundError("Google credentials not found")


GOOGLE_CREDENTIALS_FILE = _resolve_credentials_file()
SPREADSHEET_URL = os.getenv(
    "SPREADSHEET_URL",
    "https://docs.google.com/spreadsheets/d/1bVQLczc-vYsjc0ngWG2cx8raLDkFjf7CyaNkiXSVRgA/edit",
)
SHEET_NAME = os.getenv("SHEET_NAME", "Thu Chi")
PORT = int(os.getenv("PORT", "8000"))
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
