"""Mapping orchestration - tìm Ngành nghề, Danh mục, PTTT cho giao dịch."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from bot.services.memory import memory_lookup
from bot.services.sheets import find_by_recipient, find_mapping

logger = logging.getLogger(__name__)

BANK_PTTT_MAP = {
    "mb": "MB LB", "mbbank": "MB LB", "mb bank": "MB LB",
    "techcombank": "Tech Dzuong", "tcb": "Tech Dzuong",
    "vpbank": "VP Dzuong", "vp bank": "VP Dzuong",
}

# Keywords → (nganh_nghe, danh_muc)
KEYWORD_MAP = [
    (["ship", "van chuyen", "vận chuyển", "giao hang"], "ORDER CHECKOUT", "CHI - Vận chuyển nội địa"),
    (["checkout", "thanh toan ho", "ck ho"], "ORDER CHECKOUT", "CHI - Checkout hộ"),
    (["tool order", "tool", "api"], "CÔNG TY", "CHI - Tool order"),
    (["website", "hosting", "domain", "code"], "CÔNG TY", "CHI - Website"),
    (["luong", "lương", "nv", "nhan vien"], "CÔNG TY", "CHI - Tiền lương NV"),
    (["van phong", "văn phòng", "internet", "dien"], "CÔNG TY", "CHI - Văn Phòng"),
    (["dropship", "nhap hang"], "HÀNG STOCK DROPSHIP", "CHI - Nhập hàng dropship"),
    (["stock", "hang stock"], "HÀNG STOCK DROPSHIP", "CHI - Nhập hàng stock"),
    (["tra lai", "hoan tien", "hoàn tiền", "thu"], "CÔNG TY", "THU - Khoản thu khác"),
]


@dataclass
class MappingResult:
    nganh_nghe: str
    danh_muc: str
    pttt: str
    confidence: int  # 0-100
    source: str  # "memory" | "history_recipient" | "history_text" | "heuristic" | "none"


def map_entry(
    nguoi_nhan: str,
    noi_dung: str,
    ngan_hang: str,
    user_note: str | None = None,
) -> MappingResult:
    """Map giao dịch → Ngành nghề, Danh mục, PTTT. Priority: memory > recipient > text > heuristic."""

    nn = ""
    dm = ""
    pt = ""
    confidence = 0
    source = "none"

    # 1. Bot Memory (by normalized recipient name)
    mem = memory_lookup(nguoi_nhan) if nguoi_nhan else None
    if mem and mem.get("nganh_nghe"):
        nn = mem["nganh_nghe"]
        dm = mem["danh_muc"]
        pt = mem["pttt"]
        confidence = min(95, 70 + mem.get("count", 1) * 5)
        source = "memory"
        logger.info("Memory match: %s → %s/%s/%s (count=%d)", nguoi_nhan, nn, dm, pt, mem.get("count", 0))

    # 2. Sheet history by recipient
    if not nn and nguoi_nhan:
        hist = find_by_recipient(nguoi_nhan)
        if hist:
            nn, dm, pt = hist
            confidence = 85
            source = "history_recipient"
            logger.info("Recipient history match: %s", nguoi_nhan)

    # 3. Keyword inference FIRST (more reliable than fuzzy text match)
    search_text = (user_note or "") + " " + (noi_dung or "")
    search_lower = search_text.lower()
    if not nn or not dm:
        from unidecode import unidecode
        search_ascii = unidecode(search_lower)
        for keywords, kw_nn, kw_dm in KEYWORD_MAP:
            if any(kw in search_ascii or kw in search_lower for kw in keywords):
                if not nn:
                    nn = kw_nn
                if not dm:
                    dm = kw_dm
                if confidence == 0:
                    confidence = 70
                    source = "keyword"
                break

    # 4. Sheet history by text (fallback if keyword didn't match)
    if not nn and noi_dung:
        hist = find_mapping(noi_dung)
        if hist:
            nn, dm, pt = hist
            confidence = 80
            source = "history_text"

    # 5. Bank → PTTT heuristic
    if not pt and ngan_hang:
        bank_lower = ngan_hang.lower()
        for bk, pttt_val in BANK_PTTT_MAP.items():
            if bk in bank_lower:
                pt = pttt_val
                break

    # Final confidence
    if not nn and not dm:
        confidence = 0
        source = "none"
    elif source == "none" and (nn or dm):
        confidence = 50
        source = "heuristic"

    return MappingResult(
        nganh_nghe=nn,
        danh_muc=dm,
        pttt=pt,
        confidence=confidence,
        source=source,
    )
