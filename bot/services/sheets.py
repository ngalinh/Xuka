"""Google Sheets integration - đọc/ghi dữ liệu Thu Chi."""
from __future__ import annotations

import time
import logging
from typing import Any

import gspread
from google.oauth2.service_account import Credentials

from bot.config import GOOGLE_CREDENTIALS_FILE, SPREADSHEET_URL, SHEET_NAME

logger = logging.getLogger(__name__)


def _normalize(name: str) -> str:
    """Normalize tên: lowercase, bỏ dấu, trim."""
    if not name:
        return ""
    try:
        from unidecode import unidecode
        n = unidecode(name).lower().strip()
    except ImportError:
        import unicodedata
        n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower().strip()
    return " ".join(n.split())

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_client: gspread.Client | None = None

# Cache
_mapping_cache: dict[str, tuple[str, str, str]] = {}
_recipient_cache: dict[str, tuple[str, str, str]] = {}  # normalized_name → (nn, dm, pttt)
_unique_nganh_nghe: list[str] = []
_unique_danh_muc_thu: list[str] = []
_unique_danh_muc_chi: list[str] = []
_unique_pttt: list[str] = []
_cache_time: float = 0
CACHE_TTL = 1800


def _get_client() -> gspread.Client:
    global _client
    if _client is None:
        creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_FILE, scopes=SCOPES)
        _client = gspread.authorize(creds)
    return _client


def _get_worksheet() -> gspread.Worksheet:
    client = _get_client()
    spreadsheet = client.open_by_url(SPREADSHEET_URL)
    return spreadsheet.worksheet(SHEET_NAME)


def _extract_recipient_from_noi_dung(noi_dung: str) -> str | None:
    """Trích tên người nhận từ nội dung, VD: 'Nga Linh ck Nguyen Huong' → 'Nguyen Huong'."""
    nd = noi_dung.strip()
    if not nd:
        return None
    # Patterns: "X ck Y", "CK cho Y", "chuyen tien Y"
    import re
    for pattern in [
        r"(?:ck|chuyển khoản|chuyen khoan)\s+(?:cho\s+)?(.+)",
        r"(.+?)\s+(?:ck|chuyển khoản)\s*$",
    ]:
        m = re.search(pattern, nd, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None


def _load_cache() -> None:
    global _mapping_cache, _recipient_cache, _unique_nganh_nghe
    global _unique_danh_muc_thu, _unique_danh_muc_chi, _unique_pttt, _cache_time

    if _cache_time and (time.time() - _cache_time) < CACHE_TTL:
        return

    try:
        ws = _get_worksheet()
        all_vals = ws.get_all_values()
    except Exception as e:
        logger.error("Không thể đọc dữ liệu sheet: %s", e)
        return

    normalize_name = _normalize

    mapping: dict[str, tuple[str, str, str]] = {}
    recipient: dict[str, tuple[str, str, str]] = {}
    nganh_set: set[str] = set()
    dm_thu_set: set[str] = set()
    dm_chi_set: set[str] = set()
    pttt_set: set[str] = set()

    for row in all_vals[1:]:
        try:
            if len(row) < 8:
                continue
            nn = row[2].strip()
            dm = row[3].strip()
            nd = row[4].strip()
            pt = row[7].strip()
            gc = row[8].strip() if len(row) > 8 else ""

            if nn:
                nganh_set.add(nn)
            if dm:
                if dm.startswith("THU"):
                    dm_thu_set.add(dm)
                else:
                    dm_chi_set.add(dm)
            if pt:
                pttt_set.add(pt)
            if nd and (nn or dm or pt):
                mapping[nd] = (nn, dm, pt)

            # Build recipient cache
            names_to_index = []
            if gc and not gc.startswith("("):
                names_to_index.append(gc)
            extracted = _extract_recipient_from_noi_dung(nd)
            if extracted:
                names_to_index.append(extracted)
            if nd and len(nd.split()) <= 5:
                names_to_index.append(nd)

            for name in names_to_index:
                key = normalize_name(name)
                if key and len(key) > 2 and (nn or dm or pt):
                    recipient[key] = (nn, dm, pt)
        except Exception as row_err:
            logger.warning("Skip row error: %s", row_err)
            continue

    _mapping_cache = mapping
    _recipient_cache = recipient
    _unique_nganh_nghe = sorted(nganh_set)
    _unique_danh_muc_thu = sorted(dm_thu_set)
    _unique_danh_muc_chi = sorted(dm_chi_set)
    _unique_pttt = sorted(pttt_set)
    _cache_time = time.time()
    logger.info(
        "Cache loaded: %d rows, %d text mappings, %d recipients, %d nn, %d dm_chi, %d pttt",
        len(all_vals) - 1, len(mapping), len(recipient),
        len(nganh_set), len(dm_chi_set), len(pttt_set),
    )


def get_unique_nganh_nghe() -> list[str]:
    _load_cache()
    return _unique_nganh_nghe


def get_unique_danh_muc(loai: str) -> list[str]:
    _load_cache()
    return _unique_danh_muc_thu if loai == "Thu" else _unique_danh_muc_chi


def get_unique_pttt() -> list[str]:
    _load_cache()
    return _unique_pttt


def find_mapping(noi_dung: str) -> tuple[str, str, str] | None:
    """Tìm mapping từ lịch sử dựa trên nội dung."""
    _load_cache()
    if noi_dung in _mapping_cache:
        return _mapping_cache[noi_dung]

    noi_dung_lower = noi_dung.lower()
    best_match: str | None = None
    best_score = 0

    for key in _mapping_cache:
        key_lower = key.lower()
        if key_lower in noi_dung_lower or noi_dung_lower in key_lower:
            score = len(key)
            if score > best_score:
                best_score = score
                best_match = key
            continue
        words_input = set(noi_dung_lower.split())
        words_key = set(key_lower.split())
        overlap = len(words_input & words_key)
        if overlap >= 2 and overlap > best_score:
            best_score = overlap
            best_match = key

    return _mapping_cache[best_match] if best_match else None


def find_by_recipient(recipient_name: str) -> tuple[str, str, str] | None:
    """Tìm mapping dựa trên tên người nhận (normalized)."""
    _load_cache()
    key = _normalize(recipient_name)
    if not key:
        return None
    return _recipient_cache.get(key)


def append_entry(
    thang: str, ngay_tt: str, nganh_nghe: str, danh_muc: str,
    noi_dung: str, thu: str, chi: str, pttt: str, ghi_chu: str,
    nguoi_nhan: str = "",
) -> None:
    """Thêm một dòng vào sheet Thu Chi."""
    ws = _get_worksheet()
    final_gc = nguoi_nhan if nguoi_nhan else ghi_chu
    ws.append_row(
        [thang, "'" + ngay_tt, nganh_nghe, danh_muc, noi_dung, thu, chi, pttt, final_gc],
        value_input_option="USER_ENTERED",
    )
    global _cache_time
    _cache_time = 0


def append_entries(entries: list[dict[str, Any]]) -> None:
    """Batch insert nhiều dòng cùng lúc."""
    ws = _get_worksheet()
    rows = []
    for e in entries:
        final_gc = e.get("nguoi_nhan", "") or e.get("ghi_chu", "")
        rows.append([
            e.get("thang", ""), "'" + e.get("ngay_tt", ""),
            e.get("nganh_nghe", ""), e.get("danh_muc", ""),
            e.get("noi_dung", ""), e.get("thu", ""), e.get("chi", ""),
            e.get("pttt", ""), final_gc,
        ])
    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")
    global _cache_time
    _cache_time = 0


def append_zelle_entry(
    ngay: str, tai_khoan_nhan: str, total_ck: int,
    usd: float, ti_gia_mua: int, note: str = "",
) -> None:
    """Ghi vào sheet Lãi tỉ giá."""
    client = _get_client()
    ss = client.open_by_url(SPREADSHEET_URL)
    ws = ss.worksheet("Lãi tỉ giá")

    usd_str = f"${int(usd):,}" if usd % 1 == 0 else f"${usd:,.2f}"

    # Find first empty row in column A
    col_a = ws.col_values(1)
    next_row = 2  # default after header
    for i in range(len(col_a) - 1, 0, -1):
        if col_a[i].strip():
            next_row = i + 2
            break

    # Copy format from the row above (so new row matches existing style)
    src_row = next_row - 1 if next_row > 2 else 2
    try:
        sheet_id = ws.id
        ss.batch_update({"requests": [{
            "copyPaste": {
                "source": {"sheetId": sheet_id, "startRowIndex": src_row - 1, "endRowIndex": src_row,
                           "startColumnIndex": 0, "endColumnIndex": 7},
                "destination": {"sheetId": sheet_id, "startRowIndex": next_row - 1, "endRowIndex": next_row,
                                "startColumnIndex": 0, "endColumnIndex": 7},
                "pasteType": "PASTE_FORMAT"
            }
        }]})
    except Exception as fmt_err:
        logger.warning("Format copy failed: %s", fmt_err)

    # Write A-G data, preserve H-I formulas
    ws.update(f"A{next_row}:G{next_row}", [[
        "'" + ngay, "Zelle", "Mua zelle", tai_khoan_nhan,
        f"{total_ck:,}", usd_str, f"{ti_gia_mua:,}",
    ]], value_input_option="USER_ENTERED")
    if note:
        ws.update(f"J{next_row}", [[note]], value_input_option="USER_ENTERED")
    logger.info("Zelle saved row %d: %s $%s tỉ giá %s", next_row, tai_khoan_nhan, usd_str, ti_gia_mua)
