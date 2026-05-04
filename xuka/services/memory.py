"""Bot Memory - lưu mapping tên người nhận → danh mục vào Google Sheets tab 'Bot Memory'."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta

import gspread
from unidecode import unidecode

from xuka.services.sheets import _get_client
from xuka.config import SPREADSHEET_URL

logger = logging.getLogger(__name__)

VN_TZ = timezone(timedelta(hours=7))

MEMORY_SHEET = "Bot Memory"
MEMORY_HEADERS = ["key", "nganh_nghe", "danh_muc", "pttt", "last_used", "count"]

_memory_cache: dict[str, dict] = {}
_memory_cache_time: float = 0
CACHE_TTL = 1800


def normalize_name(name: str) -> str:
    """Chuẩn hóa tên: lowercase, bỏ dấu, trim, bỏ prefix công ty."""
    if not name:
        return ""
    n = unidecode(name).lower().strip()
    # Bỏ prefix công ty
    for prefix in ("cty ", "ct ", "cong ty ", "tnhh ", "co phan "):
        if n.startswith(prefix):
            n = n[len(prefix):]
    # Bỏ khoảng trắng thừa
    return " ".join(n.split())


def _get_memory_worksheet() -> gspread.Worksheet:
    client = _get_client()
    ss = client.open_by_url(SPREADSHEET_URL)
    try:
        ws = ss.worksheet(MEMORY_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        ws = ss.add_worksheet(title=MEMORY_SHEET, rows=500, cols=6)
        ws.update("A1:F1", [MEMORY_HEADERS])
        logger.info("Đã tạo sheet Bot Memory")
    return ws


def _load_memory() -> None:
    global _memory_cache, _memory_cache_time
    if _memory_cache_time and (time.time() - _memory_cache_time) < CACHE_TTL:
        return
    try:
        ws = _get_memory_worksheet()
        rows = ws.get_all_values()
    except Exception as e:
        logger.exception("Không thể đọc Bot Memory (type=%s)", type(e).__name__)
        return

    cache: dict[str, dict] = {}
    for row in rows[1:]:
        if len(row) < 6:
            continue
        key = row[0].strip()
        if not key:
            continue
        cache[key] = {
            "nganh_nghe": row[1],
            "danh_muc": row[2],
            "pttt": row[3],
            "last_used": row[4],
            "count": int(row[5]) if row[5].isdigit() else 0,
        }
    _memory_cache = cache
    _memory_cache_time = time.time()
    logger.info("Đã tải %d memory entries", len(cache))


def memory_lookup(recipient_name: str) -> dict | None:
    """Tra cứu mapping từ tên người nhận."""
    _load_memory()
    key = normalize_name(recipient_name)
    if not key:
        return None
    hit = _memory_cache.get(key)
    if hit is None:
        logger.info("[MEM] miss cache_size=%d key=%r raw=%r",
                    len(_memory_cache), key, recipient_name)
    else:
        logger.info("[MEM] hit key=%r → %s/%s/%s",
                    key, hit.get("nganh_nghe",""), hit.get("danh_muc",""), hit.get("pttt",""))
    return hit


def memory_upsert(recipient_name: str, nganh_nghe: str, danh_muc: str, pttt: str) -> None:
    """Thêm/cập nhật mapping cho tên người nhận."""
    _load_memory()
    key = normalize_name(recipient_name)
    if not key:
        return

    now = datetime.now(VN_TZ).strftime("%d/%m/%Y")

    try:
        ws = _get_memory_worksheet()
        existing = _memory_cache.get(key)

        if existing:
            # Tìm row để update
            all_vals = ws.get_all_values()
            for i, row in enumerate(all_vals):
                if i == 0:
                    continue
                if row[0].strip() == key:
                    new_count = existing["count"] + 1
                    ws.update(f"A{i+1}:F{i+1}", [[key, nganh_nghe, danh_muc, pttt, now, new_count]])
                    _memory_cache[key] = {
                        "nganh_nghe": nganh_nghe, "danh_muc": danh_muc,
                        "pttt": pttt, "last_used": now, "count": new_count,
                    }
                    logger.info("Cập nhật memory: %s (count=%d)", key, new_count)
                    return
        # Insert mới
        ws.append_row([key, nganh_nghe, danh_muc, pttt, now, 1], value_input_option="USER_ENTERED")
        _memory_cache[key] = {
            "nganh_nghe": nganh_nghe, "danh_muc": danh_muc,
            "pttt": pttt, "last_used": now, "count": 1,
        }
        logger.info("Thêm memory mới: %s", key)
    except Exception as e:
        logger.error("Lỗi upsert memory: %s", e)
