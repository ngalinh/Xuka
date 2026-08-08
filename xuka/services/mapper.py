"""Mapping orchestration - tìm Ngành nghề, Danh mục, PTTT cho giao dịch."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from xuka.services.memory import memory_lookup
from xuka.services.sheets import find_by_recipient, find_mapping

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
    (["claude", "claude pro", "openai", "chatgpt", "gpt", "tool order", "tool", "api"], "CÔNG TY", "CHI - Tool order"),
    (["website", "hosting", "domain", "code"], "CÔNG TY", "CHI - Website"),
    (["luong", "lương", "nv", "nhan vien"], "CÔNG TY", "CHI - Tiền lương NV"),
    (["van phong", "văn phòng", "internet", "dien"], "CÔNG TY", "CHI - Văn Phòng"),
    (["dropship", "nhap hang"], "HÀNG STOCK DROPSHIP", "CHI - Nhập hàng dropship"),
    (["stock", "hang stock"], "HÀNG STOCK DROPSHIP", "CHI - Nhập hàng stock"),
    (["tra lai", "hoan tien", "hoàn tiền"], "CÔNG TY", "THU - Khoản thu khác"),
]


def _matches_loai(danh_muc: str, loai: str) -> bool:
    """Check if a danh_muc matches the expected direction (chi/thu).
    Empty danh_muc passes (don't reject because no info)."""
    if not danh_muc:
        return True
    dm_upper = danh_muc.upper().strip()
    if loai == "chi":
        return not dm_upper.startswith("THU")
    if loai == "thu":
        return not dm_upper.startswith("CHI")
    return True


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
    loai: str = "chi",
) -> MappingResult:
    """Map giao dịch → Ngành nghề, Danh mục, PTTT. Priority: memory > recipient > text > heuristic.

    loai ('chi' or 'thu') filters out wrong-direction matches — e.g. a chi
    transaction will not be assigned a 'THU - ...' danh_muc from history.
    """

    nn = ""
    dm = ""
    pt = ""
    confidence = 0
    source = "none"

    # 1. Bot Memory (explicit user preference — highest trust, updated on every confirmed save)
    mem = memory_lookup(nguoi_nhan) if nguoi_nhan else None
    if mem and mem.get("nganh_nghe"):
        m_dm = mem.get("danh_muc", "")
        if _matches_loai(m_dm, loai):
            nn = mem["nganh_nghe"]
            dm = m_dm
            pt = mem.get("pttt", "")
            confidence = min(95, 75 + mem.get("count", 1) * 5)
            source = "memory"
            logger.info("Memory match: %s → %s/%s/%s (count=%d)", nguoi_nhan, nn, dm, pt, mem.get("count", 0))
        else:
            logger.info("Memory match REJECTED (loai=%s but dm=%r): %s",
                        loai, m_dm, nguoi_nhan)
            pt = mem.get("pttt", "")
    elif nguoi_nhan:
        logger.info("[MAP-MISS] memory miss nguoi_nhan=%r", nguoi_nhan)

    # 2. Sheet history by NOI_DUNG (text content)
    # If noi_dung matches a known content (e.g. "Binh Thanh"), use that
    if not nn and noi_dung:
        hist = find_mapping(noi_dung)
        if hist:
            h_nn, h_dm, h_pt = hist
            if _matches_loai(h_dm, loai):
                nn, dm, pt = h_nn, h_dm, h_pt
                confidence = 90
                source = "history_text"
                logger.info("Text history match: %s → %s/%s/%s", noi_dung, nn, dm, pt)
            else:
                logger.info("Text history match REJECTED (loai=%s but dm=%r): %s",
                            loai, h_dm, noi_dung)
                # Keep PTTT from history even if dm direction is wrong
                pt = pt or h_pt
        else:
            logger.info("[MAP-MISS] find_mapping miss noi_dung=%r", noi_dung)

    # 3. Sheet history by recipient name
    if not nn and nguoi_nhan:
        hist = find_by_recipient(nguoi_nhan)
        if hist:
            h_nn, h_dm, h_pt = hist
            if _matches_loai(h_dm, loai):
                nn, dm = h_nn, h_dm
                pt = pt or h_pt
                confidence = 85
                source = "history_recipient"
                logger.info("Recipient history match: %s → %s/%s/%s", nguoi_nhan, nn, dm, pt)
            else:
                logger.info("Recipient history REJECTED (loai=%s but dm=%r): %s",
                            loai, h_dm, nguoi_nhan)
                pt = pt or h_pt
        else:
            logger.info("[MAP-MISS] find_by_recipient miss nguoi_nhan=%r", nguoi_nhan)

    # 4. Keyword inference (fallback)
    search_text = (user_note or "") + " " + (noi_dung or "")
    search_lower = search_text.lower()
    if not nn or not dm:
        from unidecode import unidecode
        search_ascii = unidecode(search_lower)
        for keywords, kw_nn, kw_dm in KEYWORD_MAP:
            if not _matches_loai(kw_dm, loai):
                continue  # skip THU keywords for chi transaction and vice versa
            if any(kw in search_ascii or kw in search_lower for kw in keywords):
                if not nn:
                    nn = kw_nn
                if not dm:
                    dm = kw_dm
                if confidence == 0:
                    confidence = 70
                    source = "keyword"
                break

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
