"""Google Sheets integration - đọc/ghi dữ liệu Thu Chi."""
from __future__ import annotations

import time
import logging
from typing import Any

import gspread
from google.oauth2.service_account import Credentials

from xuka.config import GOOGLE_CREDENTIALS_FILE, SPREADSHEET_URL, SHEET_NAME

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
    import re
    for pattern in [
        r"(?:ck|chuyển khoản|chuyen khoan|tt|thanh toan|thanh toán)\s+(?:cho\s+|ho\s+|hộ\s+)?(.+)",
        r"(.+?)\s+(?:ck|chuyển khoản|chuyen khoan|chuyển tiền|chuyen tien)\s*$",
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
        logger.exception("Không thể đọc dữ liệu sheet (type=%s)", type(e).__name__)
        return

    normalize_name = _normalize
    from collections import Counter

    # Track all mappings per key to find most common
    mapping_counts: dict[str, Counter] = {}
    recipient_counts: dict[str, Counter] = {}
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

            # Build mapping by noi_dung (count occurrences)
            if nd and (nn or dm or pt):
                if nd not in mapping_counts:
                    mapping_counts[nd] = Counter()
                mapping_counts[nd][(nn, dm, pt)] += 1

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
                    if key not in recipient_counts:
                        recipient_counts[key] = Counter()
                    recipient_counts[key][(nn, dm, pt)] += 1
        except Exception as row_err:
            logger.warning("Skip row error: %s", row_err)
            continue

    # Pick most common mapping for each key
    mapping = {k: c.most_common(1)[0][0] for k, c in mapping_counts.items()}
    recipient = {k: c.most_common(1)[0][0] for k, c in recipient_counts.items()}

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


EXTRA_PTTT = ["Khách trả"]


def get_unique_pttt() -> list[str]:
    _load_cache()
    merged = list(_unique_pttt)
    for extra in EXTRA_PTTT:
        if extra not in merged:
            merged.append(extra)
    return merged


def find_mapping(noi_dung: str) -> tuple[str, str, str] | None:
    """Tìm mapping từ lịch sử dựa trên nội dung.

    Match theo word overlap (đã chuẩn hoá bỏ dấu). Yêu cầu:
    - overlap >= min(3, số từ input) để chặn key ngắn rác (vd 'tháng 5')
    - overlap >= 50% số từ INPUT để chặn key chỉ trùng vài từ chung
      (vd input dài 7 từ, key chỉ trùng 3 từ chung như 'tiền/tháng/5'
      sẽ bị loại - 3/7 ≈ 0.43 < 0.5)
    """
    _load_cache()
    if not noi_dung or len(noi_dung.strip()) < 3:
        return None

    logger.info("[FM] cache_size=%d input=%r", len(_mapping_cache), noi_dung)

    if noi_dung in _mapping_cache:
        logger.info("[FM] exact hit")
        return _mapping_cache[noi_dung]

    nd_norm = " ".join(_normalize(noi_dung).split())
    if not nd_norm:
        return None

    norm_to_orig: dict[str, str] = {}
    for k in _mapping_cache:
        kn = " ".join(_normalize(k).split())
        norm_to_orig.setdefault(kn, k)

    if nd_norm in norm_to_orig:
        logger.info("[FM] norm hit nd_norm=%r → %r", nd_norm, norm_to_orig[nd_norm])
        return _mapping_cache[norm_to_orig[nd_norm]]

    input_words = set(nd_norm.split())
    if not input_words:
        return None
    min_required = min(3, len(input_words))

    best_match: str | None = None
    best_score = 0

    for kn, orig in norm_to_orig.items():
        key_words = set(kn.split())
        if not key_words:
            continue
        overlap = len(input_words & key_words)
        if overlap < min_required:
            continue
        if overlap / len(input_words) < 0.5:
            continue
        if overlap > best_score:
            best_score = overlap
            best_match = orig

    if best_match:
        logger.info("[FM] fuzzy hit overlap=%d → %r", best_score, best_match)
        return _mapping_cache[best_match]
    logger.info("[FM] no match (cache=%d, input_words=%d)",
                len(_mapping_cache), len(input_words))
    return None


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
    col_a = ws.col_values(1)
    new_row = len(col_a) + 1
    ws.append_row(
        [thang, _ngay_to_formula(ngay_tt), nganh_nghe, danh_muc, noi_dung, thu, chi, pttt, final_gc],
        value_input_option="USER_ENTERED",
    )
    _apply_date_format(ws, new_row)
    global _cache_time
    _cache_time = 0


def _ngay_to_formula(ngay_tt: str) -> str:
    """Convert DD/MM/YYYY to =DATE(Y,M,D) formula for Google Sheets."""
    if not ngay_tt:
        return ""
    try:
        parts = ngay_tt.strip().split("/")
        if len(parts) != 3:
            return ngay_tt
        d, m, y = parts
        return f"=DATE({int(y)},{int(m)},{int(d)})"
    except (ValueError, IndexError):
        return ngay_tt


def _apply_date_format(ws, row_num: int) -> None:
    """Set dd/MM/yyyy format for column B (Ngày TT) of a specific row."""
    try:
        sheet_id = ws.id
        ws.spreadsheet.batch_update({
            "requests": [{
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": row_num - 1,
                        "endRowIndex": row_num,
                        "startColumnIndex": 1,
                        "endColumnIndex": 2,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "numberFormat": {"type": "DATE", "pattern": "dd/MM/yyyy"}
                        }
                    },
                    "fields": "userEnteredFormat.numberFormat",
                }
            }]
        })
    except Exception as e:
        logger.warning("Date format apply failed row %d: %s", row_num, e)


def append_entries(entries: list[dict[str, Any]]) -> None:
    """Batch insert nhiều dòng cùng lúc."""
    ws = _get_worksheet()
    rows = []
    for e in entries:
        # Ghi chú: link ảnh (HYPERLINK formula) + tên người nhận
        nn = e.get("nguoi_nhan", "")
        img = e.get("image_url", "")
        if img:
            label = nn if nn else "Xem ảnh"
            # Escape quotes in label
            label_safe = label.replace('"', '""')
            final_gc = f'=HYPERLINK("{img}","{label_safe}")'
        elif nn:
            final_gc = nn
        else:
            final_gc = e.get("ghi_chu", "")
        rows.append([
            e.get("thang", ""), _ngay_to_formula(e.get("ngay_tt", "")),
            e.get("nganh_nghe", ""), e.get("danh_muc", ""),
            e.get("noi_dung", ""), e.get("thu", ""), e.get("chi", ""),
            e.get("pttt", ""), final_gc,
        ])
    if rows:
        # Determine start row before appending
        col_a = ws.col_values(1)
        start_row = len(col_a) + 1
        ws.append_rows(rows, value_input_option="USER_ENTERED")
        # Apply date format to new rows
        for i in range(len(rows)):
            _apply_date_format(ws, start_row + i)
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
        _ngay_to_formula(ngay), "Zelle", "Mua zelle", tai_khoan_nhan,
        f"{total_ck:,}", usd_str, f"{ti_gia_mua:,}",
    ]], value_input_option="USER_ENTERED")
    if note:
        # If note is a URL, convert to clickable hyperlink
        if note.startswith("http"):
            note_value = f'=HYPERLINK("{note}","Xem ảnh")'
        else:
            note_value = note
        ws.update(f"J{next_row}", [[note_value]], value_input_option="USER_ENTERED")
    logger.info("Zelle saved row %d: %s $%s tỉ giá %s", next_row, tai_khoan_nhan, usd_str, ti_gia_mua)
