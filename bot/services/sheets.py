from __future__ import annotations

import time
import logging
from collections import defaultdict

import gspread
from google.oauth2.service_account import Credentials

from bot.config import GOOGLE_CREDENTIALS_FILE, SPREADSHEET_URL, SHEET_NAME

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_client: gspread.Client | None = None

# Cache for history mappings
_mapping_cache: dict[str, tuple[str, str, str]] = {}
_unique_nganh_nghe: list[str] = []
_unique_danh_muc_thu: list[str] = []
_unique_danh_muc_chi: list[str] = []
_unique_pttt: list[str] = []
_cache_time: float = 0
CACHE_TTL = 1800  # 30 minutes


def _get_client() -> gspread.Client:
    global _client
    if _client is None:
        creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_FILE, scopes=SCOPES)
        _client = gspread.authorize(creds)
    return _client


def _get_worksheet() -> gspread.Worksheet:
    client = _get_client()
    spreadsheet = client.open_by_url(SPREADSHEET_URL)
    ws = spreadsheet.worksheet(SHEET_NAME)
    return ws


def _load_cache() -> None:
    global _mapping_cache, _unique_nganh_nghe, _unique_danh_muc_thu
    global _unique_danh_muc_chi, _unique_pttt, _cache_time

    if _cache_time and (time.time() - _cache_time) < CACHE_TTL:
        return

    try:
        ws = _get_worksheet()
        all_vals = ws.get_all_values()
    except Exception as e:
        logger.error("Không thể đọc dữ liệu sheet: %s", e)
        return

    mapping: dict[str, tuple[str, str, str]] = {}
    nganh_set: set[str] = set()
    dm_thu_set: set[str] = set()
    dm_chi_set: set[str] = set()
    pttt_set: set[str] = set()

    for row in all_vals[1:]:
        if len(row) < 9:
            continue
        nn = row[2].strip()
        dm = row[3].strip()
        nd = row[4].strip()
        pt = row[7].strip()

        if nn:
            nganh_set.add(nn)
        if dm:
            if dm.startswith("THU"):
                dm_thu_set.add(dm)
            elif dm.startswith("CHI"):
                dm_chi_set.add(dm)
        if pt:
            pttt_set.add(pt)
        if nd and (nn or dm or pt):
            mapping[nd] = (nn, dm, pt)

    _mapping_cache = mapping
    _unique_nganh_nghe = sorted(nganh_set)
    _unique_danh_muc_thu = sorted(dm_thu_set)
    _unique_danh_muc_chi = sorted(dm_chi_set)
    _unique_pttt = sorted(pttt_set)
    _cache_time = time.time()
    logger.info("Đã tải %d mapping từ sheet", len(mapping))


def get_unique_nganh_nghe() -> list[str]:
    _load_cache()
    return _unique_nganh_nghe


def get_unique_danh_muc(loai: str) -> list[str]:
    _load_cache()
    if loai == "Thu":
        return _unique_danh_muc_thu
    return _unique_danh_muc_chi


def get_unique_pttt() -> list[str]:
    _load_cache()
    return _unique_pttt


def find_mapping(noi_dung: str) -> tuple[str, str, str] | None:
    """Tìm mapping (ngành nghề, danh mục, pttt) từ lịch sử dựa trên nội dung."""
    _load_cache()

    # Exact match first
    if noi_dung in _mapping_cache:
        return _mapping_cache[noi_dung]

    # Substring match: find best match by overlapping keywords
    noi_dung_lower = noi_dung.lower()
    best_match: str | None = None
    best_score = 0

    for key in _mapping_cache:
        key_lower = key.lower()
        # Check if key contains the input or input contains the key
        if key_lower in noi_dung_lower or noi_dung_lower in key_lower:
            score = len(key)
            if score > best_score:
                best_score = score
                best_match = key
            continue

        # Word overlap matching
        words_input = set(noi_dung_lower.split())
        words_key = set(key_lower.split())
        overlap = len(words_input & words_key)
        if overlap >= 2 and overlap > best_score:
            best_score = overlap
            best_match = key

    if best_match:
        return _mapping_cache[best_match]
    return None


def append_entry(
    thang: str,
    ngay_tt: str,
    nganh_nghe: str,
    danh_muc: str,
    noi_dung: str,
    thu: str,
    chi: str,
    pttt: str,
    ghi_chu: str,
) -> None:
    """Thêm một dòng vào sheet Thu Chi."""
    ws = _get_worksheet()
    ws.append_row(
        [thang, ngay_tt, nganh_nghe, danh_muc, noi_dung, thu, chi, pttt, ghi_chu],
        value_input_option="USER_ENTERED",
    )
    # Invalidate cache so new entry is included in future mappings
    global _cache_time
    _cache_time = 0
