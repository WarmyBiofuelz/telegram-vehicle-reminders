from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Iterable

import gspread
from google.oauth2.service_account import Credentials


SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]


@dataclass
class RawRow:
    plate: str
    event_raw: str
    expiry_raw: str
    doc1: str | None
    doc2: str | None
    timestamp: str | None


class SheetsClient:
    def __init__(self, spreadsheet_id: str, credentials_path: str):
        creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPE)
        self.gc = gspread.authorize(creds)
        self.spreadsheet = self.gc.open_by_key(spreadsheet_id)

    def read_data_rows(self, tab_name: str) -> list[RawRow]:
        ws = self.spreadsheet.worksheet(tab_name)
        values = ws.get_all_records()
        rows: list[RawRow] = []
        for rec in values:
            rows.append(
                RawRow(
                    plate=str(rec.get("Transport priemonė", "")).strip(),
                    event_raw=str(rec.get("Įvykis", "")).strip(),
                    expiry_raw=str(rec.get("Galiojimo terminas", "")).strip(),
                    doc1=(rec.get("Dokumentas") or None),
                    doc2=(rec.get("Dokumentas 2") or None),
                    timestamp=(rec.get("Timestamp") or rec.get("Laiko žyma") or None),
                )
            )
        return rows

    @staticmethod
    def parse_mmddyyyy(date_text: str) -> dt.date | None:
        if not date_text:
            return None
        for fmt in ("%m/%d/%Y", "%m/%d/%y"):
            try:
                return dt.datetime.strptime(date_text, fmt).date()
            except ValueError:
                continue
        return None

    def get_or_create_worksheet(self, tab_name: str, headers: list[str] | None = None, rows: int = 1000, cols: int = 20):
        try:
            ws = self.spreadsheet.worksheet(tab_name)
            return ws
        except gspread.WorksheetNotFound:
            ws = self.spreadsheet.add_worksheet(title=tab_name, rows=str(rows), cols=str(cols))
            if headers:
                ws.append_row(headers)
            return ws


