import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AppConfig:
    telegram_bot_token: str
    spreadsheet_id: str | None
    data_tab_name: str
    users_tab_name: str
    notifications_tab_name: str
    admin_usernames: list[str]
    admin_user_ids: list[int]
    channel_id: str | None
    google_credentials_path: str | None


def load_config() -> AppConfig:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN in environment")

    spreadsheet_id = os.getenv("SPREADSHEET_ID")
    data_tab_name = os.getenv("DATA_TAB_NAME", "Form Responses")
    users_tab_name = os.getenv("USERS_TAB_NAME", "Users")
    notifications_tab_name = os.getenv("NOTIFICATIONS_TAB_NAME", "Notifications")
    admin_usernames_env = os.getenv("ADMIN_USERNAMES", "")
    admin_usernames = [u.strip().lstrip("@").lower() for u in admin_usernames_env.split(",") if u.strip()]
    admin_user_ids_env = os.getenv("ADMIN_USER_IDS", "")
    admin_user_ids: list[int] = []
    for v in admin_user_ids_env.split(","):
        v = v.strip()
        if not v:
            continue
        try:
            admin_user_ids.append(int(v))
        except ValueError:
            pass
    channel_id = os.getenv("CHANNEL_ID")  # can be @channel_username or numeric id
    google_credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

    return AppConfig(
        telegram_bot_token=token,
        spreadsheet_id=spreadsheet_id,
        data_tab_name=data_tab_name,
        users_tab_name=users_tab_name,
        notifications_tab_name=notifications_tab_name,
        admin_usernames=admin_usernames,
        admin_user_ids=admin_user_ids,
        channel_id=channel_id,
        google_credentials_path=google_credentials_path,
    )


