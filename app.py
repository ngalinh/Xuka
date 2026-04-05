"""Xuka - Kế Toán Bot API."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import FastAPI, File, UploadFile, Form
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from bot.services.ocr import extract_transfer, OCRResult
from bot.services.mapper import map_entry
from bot.services.memory import memory_upsert
from bot.services.sheets import (
    append_entries,
    get_unique_nganh_nghe,
    get_unique_danh_muc,
    get_unique_pttt,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

VN_TZ = timezone(timedelta(hours=7))
app = FastAPI(title="Xuka - Kế Toán Bot")


# --- Helpers ---

def _format_amount(amount: int) -> str:
    return f"{amount:,}".replace(",", ".")


def _parse_amount(text: str) -> int | None:
    cleaned = text.strip().lower()
    mult = 1
    if cleaned.endswith("k"):
        cleaned = cleaned[:-1]
        mult = 1000
    cleaned = cleaned.replace(",", "").replace(".", "")
    if cleaned.isdigit() and int(cleaned) > 0:
        return int(cleaned) * mult
    return None


# --- Models ---

class SaveRequest(BaseModel):
    entries: list[dict[str, Any]]


# --- Endpoints ---

@app.get("/api/debug")
async def debug_info():
    """Debug endpoint."""
    import traceback
    try:
        from bot.services.sheets import _get_worksheet, _load_cache
        from bot.services.sheets import _unique_nganh_nghe, _unique_danh_muc_chi, _unique_pttt
        from bot.config import GOOGLE_CREDENTIALS_FILE as resolved_path
        ws = _get_worksheet()
        _load_cache()
        return {
            "status": "ok",
            "resolved_path": resolved_path,
            "sheet_name": ws.title,
            "row_count": ws.row_count,
            "cache_nganh_nghe": len(_unique_nganh_nghe),
            "cache_danh_muc_chi": len(_unique_danh_muc_chi),
            "cache_pttt": len(_unique_pttt),
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

@app.get("/api/options")
async def get_options():
    """Trả dropdown values cho frontend."""
    return {
        "nganh_nghe": get_unique_nganh_nghe(),
        "danh_muc_thu": get_unique_danh_muc("Thu"),
        "danh_muc_chi": get_unique_danh_muc("Chi"),
        "pttt": get_unique_pttt(),
    }


@app.post("/api/process")
async def process_images(
    files: list[UploadFile] = File(default=[]),
    note: str = Form(default=""),
):
    """OCR + auto-map 1 hoặc nhiều ảnh chuyển khoản."""
    if not files:
        return {"error": "Không có ảnh nào được gửi."}

    # Read all files
    file_data = []
    for f in files:
        content = await f.read()
        file_data.append((content, f.content_type or "image/png"))

    # OCR concurrently
    loop = asyncio.get_event_loop()
    ocr_tasks = [loop.run_in_executor(None, extract_transfer, data, mt) for data, mt in file_data]
    ocr_results: list[OCRResult] = await asyncio.gather(*ocr_tasks)

    # Map each entry
    entries = []
    for i, ocr in enumerate(ocr_results):
        mapping = map_entry(
            nguoi_nhan=ocr.nguoi_nhan,
            noi_dung=ocr.noi_dung,
            ngan_hang=ocr.ngan_hang,
            user_note=note,
        )
        loai = "Thu" if ocr.loai == "thu" else "Chi"
        entries.append({
            "id": f"entry_{i}",
            "ocr": {
                "so_tien": ocr.so_tien,
                "nguoi_nhan": ocr.nguoi_nhan,
                "ngan_hang": ocr.ngan_hang,
                "ngay": ocr.ngay or datetime.now(VN_TZ).strftime("%d/%m/%Y"),
                "noi_dung": ocr.noi_dung,
            },
            "mapping": {
                "nganh_nghe": mapping.nganh_nghe,
                "danh_muc": mapping.danh_muc,
                "pttt": mapping.pttt,
                "confidence": mapping.confidence,
                "source": mapping.source,
            },
            "loai": loai,
        })

    # Check batch consistency
    if len(entries) > 1:
        first = entries[0]["mapping"]
        consistent = all(
            e["mapping"]["nganh_nghe"] == first["nganh_nghe"]
            and e["mapping"]["danh_muc"] == first["danh_muc"]
            and e["mapping"]["pttt"] == first["pttt"]
            for e in entries[1:]
        )
    else:
        consistent = True

    shared = None
    if consistent and entries:
        m = entries[0]["mapping"]
        shared = {"nganh_nghe": m["nganh_nghe"], "danh_muc": m["danh_muc"], "pttt": m["pttt"]}

    return {
        "entries": entries,
        "options": {
            "nganh_nghe": get_unique_nganh_nghe(),
            "danh_muc_thu": get_unique_danh_muc("Thu"),
            "danh_muc_chi": get_unique_danh_muc("Chi"),
            "pttt": get_unique_pttt(),
        },
        "batch_consistent": consistent,
        "shared_mapping": shared,
        "note": note,
    }


@app.post("/api/save")
async def save_entries(req: SaveRequest):
    """Lưu entries đã confirm vào Google Sheets + update memory."""
    now = datetime.now(VN_TZ)
    rows = []
    for e in req.entries:
        so_tien = e.get("so_tien", 0)
        loai = e.get("loai", "Chi")
        ngay_tt = e.get("ngay_tt", now.strftime("%d/%m/%Y"))
        try:
            thang = str(int(ngay_tt.split("/")[1]))
        except (IndexError, ValueError):
            thang = str(now.month)

        thu_str = _format_amount(so_tien) if loai == "Thu" else ""
        chi_str = _format_amount(so_tien) if loai == "Chi" else ""

        rows.append({
            "thang": thang,
            "ngay_tt": ngay_tt,
            "nganh_nghe": e.get("nganh_nghe", ""),
            "danh_muc": e.get("danh_muc", ""),
            "noi_dung": e.get("noi_dung", ""),
            "thu": thu_str,
            "chi": chi_str,
            "pttt": e.get("pttt", ""),
            "ghi_chu": "",
            "nguoi_nhan": e.get("nguoi_nhan", ""),
        })

        # Update memory
        nguoi_nhan = e.get("nguoi_nhan", "")
        if nguoi_nhan and e.get("nganh_nghe"):
            try:
                memory_upsert(nguoi_nhan, e["nganh_nghe"], e.get("danh_muc", ""), e.get("pttt", ""))
            except Exception as ex:
                logger.error("Memory upsert error: %s", ex)

    try:
        append_entries(rows)
        return {"success": True, "count": len(rows)}
    except Exception as e:
        logger.error("Save error: %s", e)
        return {"success": False, "error": str(e)}


@app.post("/api/chat")
async def chat_text(note: str = Form(default="")):
    """Text-only fallback - user nhập thủ công."""
    mapping = map_entry(nguoi_nhan="", noi_dung=note, ngan_hang="", user_note=note)
    now = datetime.now(VN_TZ)
    return {
        "entry": {
            "id": "manual_0",
            "ocr": {
                "so_tien": 0,
                "nguoi_nhan": "",
                "ngan_hang": "",
                "ngay": now.strftime("%d/%m/%Y"),
                "noi_dung": note,
            },
            "mapping": {
                "nganh_nghe": mapping.nganh_nghe,
                "danh_muc": mapping.danh_muc,
                "pttt": mapping.pttt,
                "confidence": mapping.confidence,
                "source": mapping.source,
            },
            "loai": "Chi",
        },
        "options": {
            "nganh_nghe": get_unique_nganh_nghe(),
            "danh_muc_thu": get_unique_danh_muc("Thu"),
            "danh_muc_chi": get_unique_danh_muc("Chi"),
            "pttt": get_unique_pttt(),
        },
    }


# Static files (must be last)
app.mount("/", StaticFiles(directory="static", html=True), name="static")
