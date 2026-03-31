import gspread
from google.oauth2.service_account import Credentials

from bot.config import GOOGLE_CREDENTIALS_FILE, SPREADSHEET_URL, SHEET_NAME

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_client: gspread.Client | None = None


def _get_client() -> gspread.Client:
    global _client
    if _client is None:
        creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_FILE, scopes=SCOPES)
        _client = gspread.authorize(creds)
    return _client


def _get_worksheet() -> gspread.Worksheet:
    client = _get_client()
    spreadsheet = client.open_by_url(SPREADSHEET_URL)
    try:
        ws = spreadsheet.worksheet(SHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=SHEET_NAME, rows=1000, cols=7)
    # Ensure headers exist
    row1 = ws.row_values(1)
    if not row1:
        ws.update("A1:G1", [["Ngay", "Loai", "Danh muc", "So tien", "Ghi chu", "Nguoi nhap", "Thoi gian"]])
    return ws


def append_entry(
    ngay: str,
    loai: str,
    danh_muc: str,
    so_tien: str,
    ghi_chu: str,
    nguoi_nhap: str,
    thoi_gian: str,
) -> None:
    """Append a row to the Thu Chi sheet."""
    ws = _get_worksheet()
    ws.append_row(
        [ngay, loai, danh_muc, so_tien, ghi_chu, nguoi_nhap, thoi_gian],
        value_input_option="USER_ENTERED",
    )
