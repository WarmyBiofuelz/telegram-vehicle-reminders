from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import datetime as dt

from .sheets_client import SheetsClient


USERS_HEADERS = [
    "telegram_user_id",
    "telegram_username",
    "telegram_chat_id",
    "status",
    "approved_at",
    "approved_by",
    "invite_link_last_sent_at",
    "role",
]


@dataclass
class UserRow:
    telegram_user_id: int
    telegram_username: str | None
    telegram_chat_id: int | None
    status: str
    approved_at: str | None
    approved_by: str | None
    invite_link_last_sent_at: str | None
    role: str | None


class UsersRepo:
    def __init__(self, client: SheetsClient, tab_name: str):
        self.client = client
        self.tab_name = tab_name
        self.ws = self.client.get_or_create_worksheet(tab_name, USERS_HEADERS)

    def find_by_user_id(self, user_id: int) -> Optional[tuple[int, UserRow]]:
        values = self.ws.get_all_records()
        for idx, rec in enumerate(values, start=2):
            try:
                uid = int(rec.get("telegram_user_id", 0))
            except Exception:
                uid = 0
            if uid == user_id:
                return idx, UserRow(
                    telegram_user_id=uid,
                    telegram_username=(rec.get("telegram_username") or None),
                    telegram_chat_id=int(rec.get("telegram_chat_id")) if rec.get("telegram_chat_id") else None,
                    status=str(rec.get("status", "")).strip(),
                    approved_at=(rec.get("approved_at") or None),
                    approved_by=(rec.get("approved_by") or None),
                    invite_link_last_sent_at=(rec.get("invite_link_last_sent_at") or None),
                    role=(rec.get("role") or None),
                )
        return None

    def upsert_pending(self, user_id: int, username: str | None, chat_id: int | None) -> None:
        found = self.find_by_user_id(user_id)
        if found:
            row_idx, row = found
            self.ws.update_cell(row_idx, 2, username or "")
            self.ws.update_cell(row_idx, 3, chat_id or "")
            if row.status == "":
                self.ws.update_cell(row_idx, 4, "pending")
            return
        # append new
        self.ws.append_row([
            user_id,
            username or "",
            chat_id or "",
            "pending",
            "",
            "",
            "",
            "user",
        ])

    def approve(self, user_id: int, approved_by: str) -> bool:
        found = self.find_by_user_id(user_id)
        if not found:
            return False
        row_idx, _ = found
        self.ws.update_cell(row_idx, 4, "approved")
        self.ws.update_cell(row_idx, 5, dt.datetime.now().isoformat(timespec="seconds"))
        self.ws.update_cell(row_idx, 6, approved_by)
        return True

    def reject(self, user_id: int, rejected_by: str) -> bool:
        found = self.find_by_user_id(user_id)
        if not found:
            return False
        row_idx, _ = found
        self.ws.update_cell(row_idx, 4, "rejected")
        self.ws.update_cell(row_idx, 5, dt.datetime.now().isoformat(timespec="seconds"))
        self.ws.update_cell(row_idx, 6, rejected_by)
        return True

    def list_pending(self) -> list[UserRow]:
        values = self.ws.get_all_records()
        result: list[UserRow] = []
        for rec in values:
            if str(rec.get("status", "")).strip() == "pending":
                try:
                    uid = int(rec.get("telegram_user_id", 0))
                except Exception:
                    uid = 0
                result.append(
                    UserRow(
                        telegram_user_id=uid,
                        telegram_username=(rec.get("telegram_username") or None),
                        telegram_chat_id=int(rec.get("telegram_chat_id")) if rec.get("telegram_chat_id") else None,
                        status="pending",
                        approved_at=(rec.get("approved_at") or None),
                        approved_by=(rec.get("approved_by") or None),
                        invite_link_last_sent_at=(rec.get("invite_link_last_sent_at") or None),
                        role=(rec.get("role") or None),
                    )
                )
        return result

    def list_approved(self) -> list[UserRow]:
        values = self.ws.get_all_records()
        result: list[UserRow] = []
        for rec in values:
            if str(rec.get("status", "")).strip() == "approved":
                try:
                    uid = int(rec.get("telegram_user_id", 0))
                except Exception:
                    uid = 0
                result.append(
                    UserRow(
                        telegram_user_id=uid,
                        telegram_username=(rec.get("telegram_username") or None),
                        telegram_chat_id=int(rec.get("telegram_chat_id")) if rec.get("telegram_chat_id") else None,
                        status="approved",
                        approved_at=(rec.get("approved_at") or None),
                        approved_by=(rec.get("approved_by") or None),
                        invite_link_last_sent_at=(rec.get("invite_link_last_sent_at") or None),
                        role=(rec.get("role") or None),
                    )
                )
        return result

    def list_all(self) -> list[UserRow]:
        values = self.ws.get_all_records()
        result: list[UserRow] = []
        for rec in values:
            try:
                uid = int(rec.get("telegram_user_id", 0))
            except Exception:
                uid = 0
            result.append(
                UserRow(
                    telegram_user_id=uid,
                    telegram_username=(rec.get("telegram_username") or None),
                    telegram_chat_id=int(rec.get("telegram_chat_id")) if rec.get("telegram_chat_id") else None,
                    status=str(rec.get("status", "")).strip(),
                    approved_at=(rec.get("approved_at") or None),
                    approved_by=(rec.get("approved_by") or None),
                    invite_link_last_sent_at=(rec.get("invite_link_last_sent_at") or None),
                    role=(rec.get("role") or None),
                )
            )
        return result

    def delete_user(self, user_id: int) -> bool:
        found = self.find_by_user_id(user_id)
        if not found:
            return False
        row_idx, _ = found
        # Delete that row
        self.ws.delete_rows(row_idx)
        return True

