from __future__ import annotations

import os
import json
import base64
import tempfile
import logging

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _resolve_credentials_file() -> str:
    path = os.getenv("GOOGLE_CREDENTIALS_FILE", "./credentials.json")
    if os.path.exists(path):
        logger.info("Using credentials from file: %s", path)
        return path
    # Fallback: base64-encoded JSON in env var (for cloud deployment)
    encoded = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
    if encoded:
        try:
            # Handle potential padding issues
            padding = 4 - len(encoded) % 4
            if padding != 4:
                encoded += "=" * padding
            data = base64.b64decode(encoded)
            # Validate it's valid JSON
            parsed = json.loads(data)
            if "private_key" not in parsed:
                raise ValueError("Missing private_key in credentials")
            # Fix escaped newlines in private_key
            if "\\n" in parsed["private_key"] and "\n" not in parsed["private_key"]:
                parsed["private_key"] = parsed["private_key"].replace("\\n", "\n")
                data = json.dumps(parsed).encode()
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="wb")
            tmp.write(data)
            tmp.close()
            logger.info("Using credentials from GOOGLE_CREDENTIALS_JSON env var (decoded %d bytes)", len(data))
            return tmp.name
        except Exception as e:
            logger.error("Failed to decode GOOGLE_CREDENTIALS_JSON: %s", e)
            raise
    raise FileNotFoundError("Google credentials not found. Set GOOGLE_CREDENTIALS_FILE or GOOGLE_CREDENTIALS_JSON")


GOOGLE_CREDENTIALS_FILE = _resolve_credentials_file()
SPREADSHEET_URL = os.getenv(
    "SPREADSHEET_URL",
    "https://docs.google.com/spreadsheets/d/1bVQLczc-vYsjc0ngWG2cx8raLDkFjf7CyaNkiXSVRgA/edit",
)
SHEET_NAME = os.getenv("SHEET_NAME", "Thu Chi")
PORT = int(os.getenv("PORT", "8000"))
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
