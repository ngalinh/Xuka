from __future__ import annotations

import os
import json
import base64
import tempfile
import logging

from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root (works regardless of cwd)
_project_root = Path(__file__).resolve().parent.parent
load_dotenv(_project_root / ".env", override=True)

logger = logging.getLogger(__name__)


def _resolve_credentials_file() -> str:
    default_cred = str(_project_root / "credentials.json")
    path = os.getenv("GOOGLE_CREDENTIALS_FILE", default_cred)
    # Resolve relative paths against project root
    if not os.path.isabs(path):
        path = str(_project_root / path)
    if os.path.exists(path):
        logger.info("Using credentials from file: %s", path)
        return path

    # Try env var (base64 or raw JSON) - useful for hosted environments
    raw_json = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
    if raw_json:
        try:
            if raw_json.strip().startswith("{"):
                parsed = json.loads(raw_json)
            else:
                padding = 4 - len(raw_json) % 4
                if padding != 4:
                    raw_json += "=" * padding
                parsed = json.loads(base64.b64decode(raw_json))

            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="wb")
            tmp.write(json.dumps(parsed).encode("utf-8"))
            tmp.close()
            logger.info("Credentials loaded from env var, email: %s", parsed.get("client_email", "?"))
            return tmp.name
        except Exception as e:
            logger.error("Failed to parse GOOGLE_CREDENTIALS_JSON: %s", e)
            raise

    raise FileNotFoundError("No Google credentials found")


GOOGLE_CREDENTIALS_FILE = _resolve_credentials_file()
SPREADSHEET_URL = os.getenv(
    "SPREADSHEET_URL",
    "https://docs.google.com/spreadsheets/d/1bVQLczc-vYsjc0ngWG2cx8raLDkFjf7CyaNkiXSVRgA/edit",
)
SHEET_NAME = os.getenv("SHEET_NAME", "Thu Chi")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
