# Warmy Calendar Bot

A Telegram bot that sends daily reminders about vehicle document expiries (road tolls, insurance, inspection, etc.) by reading data from Google Sheets.

## Features

- 📅 **Daily reminders** at 08:00 Europe/Vilnius
- ⚠️ **Smart notifications**: 5 days before, 1 day before, and daily after expiry
- 🔐 **Admin approval system** for user access
- 📊 **Google Sheets integration** for vehicle data
- 🇱🇹 **Lithuanian interface**
- 🚗 **Quick plate lookup** with `/ABC123` shortcuts
- 📋 **Document links** for registration certificates

## Commands

### For Users
- `/start` — register for access
- `/info` — today's reminders
- `/sarasas` — list all plates with buttons
- `/ABC123` — quick plate details
- `/pagalba` — help

### For Admins
- `/pending` — approve/reject users with buttons
- `/users` — manage all users with delete buttons
- `/dryrun` — preview today's summary
- `/sendtoday` — send summary to all approved users

## Setup

### Prerequisites
- Python 3.11+
- Telegram Bot Token
- Google Service Account with Sheets API access
- Google Spreadsheet with vehicle data

### Installation

1. **Clone and install dependencies:**
```bash
git clone <your-repo>
cd WarmyCalendar
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

2. **Environment variables (.env):**
```bash
TELEGRAM_BOT_TOKEN=your_bot_token
SPREADSHEET_ID=your_google_sheet_id
DATA_TAB_NAME=Transport
USERS_TAB_NAME=Users
NOTIFICATIONS_TAB_NAME=Notifications
ADMIN_USER_IDS=your_telegram_user_id
ADMIN_USERNAMES=your_telegram_username
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service_account.json
```

3. **Google Sheets setup:**
- Create `Users` and `Notifications` tabs in your spreadsheet
- Share the spreadsheet with your service account email
- Ensure your main data tab has columns: `Transport priemonė`, `Įvykis`, `Galiojimo terminas`, `Dokumentas`, `Dokumentas 2`, `Timestamp`

4. **Run:**
```bash
python -m Warmy_Calendar_bot.main
```

## Google Sheets Structure

### Transport Tab (your existing data)
- `Transport priemonė` — license plate
- `Įvykis` — event type (LV Kelių mokestis, LT Kelių mokestis, TA galiojimas, CA draudimas iki, Registracijos liudijimas)
- `Galiojimo terminas` — expiry date (MM/DD/YYYY format)
- `Dokumentas` — document link 1
- `Dokumentas 2` — document link 2
- `Timestamp` — form submission time

### Users Tab (auto-created)
- `telegram_user_id`, `telegram_username`, `telegram_chat_id`, `status`, `approved_at`, `approved_by`, `invite_link_last_sent_at`, `role`

### Notifications Tab (auto-created)
- For tracking sent notifications and preventing duplicates

## Deployment

### Render.com
1. Connect your GitHub repo to Render
2. Set environment variables in Render dashboard
3. Upload Google service account JSON via Render's file upload
4. Deploy as a Background Worker

## License

MIT License
