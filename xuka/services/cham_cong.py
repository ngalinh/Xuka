"""Chấm công - ghi vào sheet 'Chấm công' trong Google Sheets riêng."""
from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import datetime, timezone, timedelta

import gspread

from xuka.services.sheets import _get_client

logger = logging.getLogger(__name__)

CHAM_CONG_SHEET_NAME = "Chấm công"
VN_TZ = timezone(timedelta(hours=7))

# Default time options (used if sheet is empty / new branch). Sheet values
# take precedence and are merged in.
DEFAULT_THOI_GIAN = ["1 ngày", "Nửa ngày", "1 giờ", "2 giờ", "30 phút"]

# Cache
_cache: dict = {
    "chi_nhanh": [],   # ordered by usage count desc
    "nhan_vien": [],
    "danh_muc": [],
    "thoi_gian": [],
    "loaded_at": 0,
}
CACHE_TTL = 1800  # 30 min


def _get_spreadsheet_url() -> str:
    """Read URL from env each call so reload picks up changes."""
    import os
    return os.getenv(
        "CHAM_CONG_SPREADSHEET_URL",
        "https://docs.google.com/spreadsheets/d/1Zv8RyIUmLmyYMEOFjbCyYKw_Q9Wty-4eA2mxJPuuVLs/edit",
    )


def _get_worksheet() -> gspread.Worksheet:
    client = _get_client()
    ss = client.open_by_url(_get_spreadsheet_url())
    return ss.worksheet(CHAM_CONG_SHEET_NAME)


def _load_cache(force: bool = False) -> None:
    if not force and _cache["loaded_at"] and (time.time() - _cache["loaded_at"]) < CACHE_TTL:
        return
    try:
        ws = _get_worksheet()
        vals = ws.get_all_values()
    except Exception as e:
        logger.error("[CC] Không đọc được sheet Chấm công: %s", e)
        return

    chi_nhanh: Counter = Counter()
    nhan_vien: Counter = Counter()
    danh_muc: Counter = Counter()
    thoi_gian: Counter = Counter()

    for row in vals[1:]:
        if len(row) < 6:
            continue
        if row[2].strip():
            chi_nhanh[row[2].strip()] += 1
        if row[3].strip():
            nhan_vien[row[3].strip()] += 1
        if row[4].strip():
            danh_muc[row[4].strip()] += 1
        if row[5].strip():
            thoi_gian[row[5].strip()] += 1

    # Order by frequency (most-used first)
    _cache["chi_nhanh"] = [v for v, _ in chi_nhanh.most_common()]
    _cache["nhan_vien"] = [v for v, _ in nhan_vien.most_common()]
    _cache["danh_muc"] = [v for v, _ in danh_muc.most_common()]
    # Merge sheet values + defaults, preserve order, no duplicates
    merged_tg: list = []
    for v, _ in thoi_gian.most_common():
        if v not in merged_tg:
            merged_tg.append(v)
    for v in DEFAULT_THOI_GIAN:
        if v not in merged_tg:
            merged_tg.append(v)
    _cache["thoi_gian"] = merged_tg
    _cache["loaded_at"] = time.time()
    logger.info("[CC] Cache loaded: %d nhân viên, %d danh mục, %d chi nhánh, %d thời gian",
                len(_cache["nhan_vien"]), len(_cache["danh_muc"]),
                len(_cache["chi_nhanh"]), len(_cache["thoi_gian"]))


def get_chi_nhanh() -> list[str]:
    _load_cache()
    return _cache["chi_nhanh"] or ["Sài Gòn", "Hà Nội"]


def get_nhan_vien() -> list[str]:
    _load_cache()
    return _cache["nhan_vien"]


def get_danh_muc() -> list[str]:
    _load_cache()
    return _cache["danh_muc"]


def get_thoi_gian() -> list[str]:
    _load_cache()
    return _cache["thoi_gian"]


def _format_date_label(d: datetime) -> str:
    """Format date the way the sheet expects: 'Apr-18', 'Jan-5' (no leading zero)."""
    return d.strftime("%b-") + str(d.day)


def _format_thang(d: datetime) -> str:
    """Sheet uses 'Tháng 1', 'Tháng 4', etc."""
    return f"Tháng {d.month}"


def append_cham_cong(
    chi_nhanh: str,
    nhan_vien: str,
    danh_muc: str,
    thoi_gian: str,
    ghi_chu: str = "",
    ngay: datetime | None = None,
) -> dict:
    """Append a row to the Chấm công sheet.
    Columns: A Tháng | B Ngày tháng | C Chi nhánh | D Nhân viên | E Danh mục
             | F Thời gian | G Ghi chú | H Sếp duyệt
    Returns dict with the saved fields (for delete/log).
    """
    if ngay is None:
        ngay = datetime.now(VN_TZ)
    thang = _format_thang(ngay)
    ngay_label = _format_date_label(ngay)

    ws = _get_worksheet()
    row = [thang, ngay_label, chi_nhanh, nhan_vien, danh_muc, thoi_gian, ghi_chu, ""]
    ws.append_row(row, value_input_option="USER_ENTERED")
    logger.info("[CC-SAVE] %s | %s | %s | %s | %s | %s | %r",
                thang, ngay_label, chi_nhanh, nhan_vien, danh_muc, thoi_gian, ghi_chu)

    # Invalidate cache so next list reflects new entry
    _cache["loaded_at"] = 0

    return {
        "thang": thang, "ngay_label": ngay_label,
        "chi_nhanh": chi_nhanh, "nhan_vien": nhan_vien,
        "danh_muc": danh_muc, "thoi_gian": thoi_gian,
        "ghi_chu": ghi_chu,
    }


def delete_cham_cong(saved: dict) -> bool:
    """Delete the most recent row matching saved dict. Returns True on success."""
    ws = _get_worksheet()
    vals = ws.get_all_values()
    for i in range(len(vals) - 1, 0, -1):
        row = vals[i]
        if len(row) < 6:
            continue
        if (row[1].strip() == saved["ngay_label"]
                and row[2].strip() == saved["chi_nhanh"]
                and row[3].strip() == saved["nhan_vien"]
                and row[4].strip() == saved["danh_muc"]
                and row[5].strip() == saved["thoi_gian"]):
            ws.delete_rows(i + 1)
            logger.info("[CC-DELETE] removed row %d", i + 1)
            _cache["loaded_at"] = 0
            return True
    logger.warning("[CC-DELETE-NOT-FOUND] %s", saved)
    return False
