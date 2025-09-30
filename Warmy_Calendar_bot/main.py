"""
Main bot application with JSON-based local storage.
Replaces Google Sheets API caching with persistent local data.
"""

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters
import re
import sys
import time
import asyncio
import datetime as dt
from datetime import timezone, timedelta

from .config import load_config
from .data_model import latest_by_plate_event, compute_windows, format_summary_lt
from .users_repo import UsersRepo
from .sheets_client import SheetsClient
from .data_sync import data_sync

load_dotenv()
cfg = load_config()

# User approval cache (still needed for Users sheet)
_users_cache = {
    'approved_users': set(),
    'timestamp': 0,
    'ttl': 300  # 5 minutes cache
}

def get_cached_approved_users():
    """Get approved user IDs with caching to reduce Users sheet API calls"""
    now = time.time()
    if _users_cache['approved_users'] and (now - _users_cache['timestamp']) < _users_cache['ttl']:
        print(f"👥 Using cached user approvals (age: {int(now - _users_cache['timestamp'])}s)")
        return _users_cache['approved_users']
    
    print("🔄 Fetching fresh user approvals from Google Sheets...")
    if not (cfg.spreadsheet_id and cfg.google_credentials_path):
        return set()
    
    try:
        client = SheetsClient(cfg.spreadsheet_id, cfg.google_credentials_path)
        repo = UsersRepo(client, cfg.users_tab_name)
        approved = repo.list_approved()
        approved_ids = {user.telegram_user_id for user in approved}
        
        # Cache the result
        _users_cache['approved_users'] = approved_ids
        _users_cache['timestamp'] = now
        print(f"✅ Cached {len(approved_ids)} approved users")
        return approved_ids
        
    except Exception as e:
        print(f"❌ Error fetching user approvals: {e}")
        # Return cached data if available, even if expired
        if _users_cache['approved_users']:
            print("⚠️ Using expired user cache due to API error")
            return _users_cache['approved_users']
        return set()

async def send_daily_reminders():
    """Send daily vehicle reminders using local JSON data"""
    print("🕐 Starting daily reminder sending...")
    
    try:
        # First, sync data from Google Sheets
        success, message = await data_sync.sync_from_google_sheets()
        if not success:
            print(f"⚠️ Sync failed: {message}")
            # Continue with existing data if available
            if not data_sync.is_data_available():
                print("❌ No data available, skipping reminders")
                return
        
        # Get processed data from JSON storage
        tuples = data_sync.get_processed_data_for_reminders()
        if not tuples:
            print("📭 No vehicle data for reminders")
            return
        
        # Process deadlines
        latest = latest_by_plate_event(tuples)
        today = dt.date.today()
        upcoming, expired = compute_windows(today, latest)
        text = format_summary_lt(upcoming, expired)
        
        if not text.strip() or "Šiandien priminimų nėra" in text:
            print("📭 No reminders to send today")
            return
        
        # Get approved users + admins
        if not (cfg.spreadsheet_id and cfg.google_credentials_path):
            print("⚠️ Users sheet configuration missing")
            return
            
        client = SheetsClient(cfg.spreadsheet_id, cfg.google_credentials_path)
        repo = UsersRepo(client, cfg.users_tab_name)
        approved = repo.list_approved()
        all_users = repo.list_all()
        
        # Collect recipients: approved users + admins
        recipients = set()  # Use set to avoid duplicates
        
        # Add approved users
        for user in approved:
            if user.telegram_chat_id:
                recipients.add((user.telegram_chat_id, user.telegram_username or str(user.telegram_user_id)))
        
        # Add admins (if they exist in Users sheet)
        for user in all_users:
            if user.telegram_user_id in cfg.admin_user_ids and user.telegram_chat_id:
                recipients.add((user.telegram_chat_id, f"Admin: {user.telegram_username or str(user.telegram_user_id)}"))
        
        if not recipients:
            print("📭 No users or admins to send reminders to")
            return
        
        # Get bot instance for sending messages
        from telegram import Bot
        bot = Bot(token=cfg.telegram_bot_token)
        
        sent_count = 0
        error_count = 0
        
        for chat_id, name in recipients:
            try:
                await bot.send_message(chat_id=chat_id, text=text)
                sent_count += 1
                print(f"📨 Daily reminder sent to {name}")
                
                # Small delay to avoid rate limits
                await asyncio.sleep(1)
                
            except Exception as e:
                error_count += 1
                print(f"❌ Error sending reminder to {chat_id}: {e}")
        
        print(f"✅ Daily reminder sending completed: {sent_count} sent, {error_count} errors")
        
    except Exception as e:
        print(f"❌ Error in daily reminder sending: {e}")

async def daily_job(context: ContextTypes.DEFAULT_TYPE):
    """Daily job function for telegram job queue"""
    await send_daily_reminders()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    # Try to upsert user as pending in Users sheet
    try:
        if cfg.spreadsheet_id and cfg.google_credentials_path:
            client = SheetsClient(cfg.spreadsheet_id, cfg.google_credentials_path)
            repo = UsersRepo(client, cfg.users_tab_name)
            repo.upsert_pending(user.id, user.username, chat.id)
            await update.message.reply_text('Sveiki! Jūsų registracija pateikta. Laukite administratoriaus patvirtinimo.')
        else:
            await update.message.reply_text('Sveiki! Botas veikia. (/start)')
    except Exception as e:
        print(f"Error in start: {e}")
        await update.message.reply_text('Sveiki! Botas veikia. (/start)')

def main():
    print("🤖 Initializing Telegram bot...", flush=True)
    
    # Add startup delay to avoid conflicts
    time.sleep(3)
    
    app = Application.builder().token(cfg.telegram_bot_token).build()
    
    # Error handler
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        if "Conflict" in str(context.error):
            print(f"⚠️ Conflict detected - another bot instance may be running", flush=True)
        else:
            print(f"ERROR: {context.error}", flush=True)
    
    app.add_error_handler(error_handler)
    
    # Add start handler
    app.add_handler(CommandHandler('start', start))
    
    # Helper functions
    def is_admin(update: Update) -> bool:
        u = update.effective_user
        if not u:
            return False
        if u.id in cfg.admin_user_ids:
            return True
        uname = (u.username or '').lstrip('@').lower()
        return uname in cfg.admin_usernames

    async def is_approved_user(update: Update) -> bool:
        # Admins always pass
        if is_admin(update):
            return True
        # If Users sheet not configured, allow by default
        if not (cfg.spreadsheet_id and cfg.google_credentials_path):
            return True
        u = update.effective_user
        if not u:
            return False
        approved_users = get_cached_approved_users()
        return u.id in approved_users
    
    # Help command
    async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = """
Galimos komandos:
/start - Registracija
/pagalba - Šis pranešimas
/info - Šiandienos priminimas
/sarasas - Visų numerių sąrašas
/id <numeris> - Konkretaus numerio duomenys

Administratoriaus komandos:
/dryrun - Peržiūrėti šiandienos pranešimą
/pending - Patvirtinti laukiančius vartotojus
/approve <user_id> - Patvirtinti vartotoją
/users - Vartotojų sąrašas
/update - Atnaujinti duomenis iš Google Sheets
/remove <numeris> - Pašalinti numerį iš pranešimų
/sendtoday - Išsiųsti šiandienos pranešimą
/whoami - Sužinoti savo ID
        """
        await update.message.reply_text(help_text)

    app.add_handler(CommandHandler('pagalba', help_cmd))
    
    # Admin dry-run command
    async def dryrun(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update):
            await update.message.reply_text("Neturite teisės naudoti šios komandos.")
            return
        
        if not data_sync.is_data_available():
            await update.message.reply_text("❌ Duomenų nėra. Naudokite /update.")
            return
        
        tuples = data_sync.get_processed_data_for_reminders()
        latest = latest_by_plate_event(tuples)
        today = dt.date.today()
        upcoming, expired = compute_windows(today, latest)
        text = format_summary_lt(upcoming, expired)
        await update.message.reply_text(text)

    app.add_handler(CommandHandler('dryrun', dryrun))

    # User info command
    async def info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await is_approved_user(update):
            await update.message.reply_text("Jūsų prieiga dar nepatvirtinta.")
            return
        
        if not data_sync.is_data_available():
            await update.message.reply_text("❌ Duomenų nėra. Susisiekite su administratoriumi.")
            return
        
        tuples = data_sync.get_processed_data_for_reminders()
        latest = latest_by_plate_event(tuples)
        today = dt.date.today()
        upcoming, expired = compute_windows(today, latest)
        text = format_summary_lt(upcoming, expired)
        await update.message.reply_text(text)

    app.add_handler(CommandHandler('info', info_cmd))

    # List all plates
    async def sarasas(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await is_approved_user(update):
            await update.message.reply_text("Jūsų prieiga dar nepatvirtinta.")
            return
        
        plates = data_sync.get_all_active_plates()
        if not plates:
            await update.message.reply_text("Sąrašas tuščias.")
            return
        
        buttons = []
        for plate in plates:
            buttons.append([InlineKeyboardButton(plate, callback_data=f"plate:{plate}")])
        
        await update.message.reply_text(
            "Numerių sąrašas:\n" + "\n".join(plates),
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    app.add_handler(CommandHandler('sarasas', sarasas))

    # Get specific plate details
    async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await is_approved_user(update):
            await update.message.reply_text("Jūsų prieiga dar nepatvirtinta.")
            return
        
        args = context.args or []
        if not args:
            await update.message.reply_text("Naudojimas: /id <numeris>")
            return
        
        plate = args[0].strip().upper()
        vehicle_data = data_sync.get_vehicle_details(plate)
        
        if not vehicle_data or vehicle_data.get("excluded", False):
            await update.message.reply_text("Numeris nerastas.")
            return
        
        # Format response - show ALL possible parameters
        lines = [f"{plate}:"]
        today = dt.date.today()
        
        # Define all possible parameters that should be shown
        all_parameters = [
            ("lv_road_toll", "LV kelių mokestis"),
            ("lt_road_toll", "LT kelių mokestis"), 
            ("inspection", "Techninė apžiūra"),
            ("insurance", "Draudimas"),
            ("registration_certificate", "Registracijos liudijimas")
        ]
        
        # Create lookup for existing events
        events_lookup = {}
        for event in vehicle_data["events"]:
            events_lookup[event["event_type"]] = event
        
        # Display all parameters in consistent order
        for event_type, label in all_parameters:
            if event_type in events_lookup:
                event = events_lookup[event_type]
                expires = event.get("expires")
                doc_links = event.get("doc_links", [])
                
                if event_type == "registration_certificate":
                    # Special handling for registration certificate - show document links
                    if doc_links:
                        lines.append(f"- {label}:")
                        for i, link in enumerate(doc_links, 1):
                            lines.append(f"  Dokumentas {i}: {link}")
                    else:
                        lines.append(f"- {label}: (dokumentų nėra)")
                else:
                    # Regular event with expiry date
                    if expires:
                        try:
                            exp_date = dt.datetime.fromisoformat(expires).date()
                            if exp_date < today:
                                status = "nebegalioja"
                            else:
                                status = f"galioja iki {exp_date.isoformat()}"
                            lines.append(f"- {label}: {status}")
                        except Exception:
                            lines.append(f"- {label}: (data neteisinga)")
                    else:
                        lines.append(f"- {label}: (duomenų nėra)")
            else:
                # Parameter not found in data - show as missing
                if event_type == "registration_certificate":
                    lines.append(f"- {label}: (dokumentų nėra)")
                else:
                    lines.append(f"- {label}: (duomenų nėra)")
        
        await update.message.reply_text("\n".join(lines))

    app.add_handler(CommandHandler('id', cmd_id))

    # Admin update command
    async def update_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update):
            await update.message.reply_text("Neturite teisės naudoti šios komandos.")
            return
        
        await update.message.reply_text("🔄 Atnaujinami duomenys...")
        success, message = await data_sync.sync_from_google_sheets(force=True)
        await update.message.reply_text(message)

    app.add_handler(CommandHandler('update', update_cmd))

    # Admin remove command
    async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update):
            await update.message.reply_text("Neturite teisės naudoti šios komandos.")
            return
        
        args = context.args or []
        if not args:
            await update.message.reply_text("Naudojimas: /remove <numeris>")
            return
        
        plate = args[0].strip().upper()
        admin_name = update.effective_user.username or str(update.effective_user.id)
        
        success, message = data_sync.exclude_vehicle(plate, admin_name)
        await update.message.reply_text(message)
        
        if success:
            # Show updated exclusion list
            excluded_list = data_sync.get_excluded_vehicles_list()
            await update.message.reply_text(excluded_list)

    app.add_handler(CommandHandler('remove', remove_cmd))

    # Callback handler for inline buttons
    async def on_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        try:
            await q.answer("Apdorojama…")
        except Exception:
            pass
        
        data = q.data or ""
        if data.startswith("plate:"):
            if not await is_approved_user(update):
                return
            
            plate = data.split(":", 1)[1].strip().upper()
            vehicle_data = data_sync.get_vehicle_details(plate)
            
            if not vehicle_data or vehicle_data.get("excluded", False):
                await q.edit_message_text("Numeris nerastas.")
                return
            
            # Format detailed response - show ALL possible parameters
            lines = [f"{plate}:"]
            today = dt.date.today()
            
            # Define all possible parameters that should be shown
            all_parameters = [
                ("lv_road_toll", "LV kelių mokestis"),
                ("lt_road_toll", "LT kelių mokestis"), 
                ("inspection", "Techninė apžiūra"),
                ("insurance", "Draudimas"),
                ("registration_certificate", "Registracijos liudijimas")
            ]
            
            # Create lookup for existing events
            events_lookup = {}
            for event in vehicle_data["events"]:
                events_lookup[event["event_type"]] = event
            
            # Display all parameters in consistent order
            for event_type, label in all_parameters:
                if event_type in events_lookup:
                    event = events_lookup[event_type]
                    expires = event.get("expires")
                    doc_links = event.get("doc_links", [])
                    
                    if event_type == "registration_certificate":
                        # Special handling for registration certificate - show document links
                        if doc_links:
                            lines.append(f"- {label}:")
                            for i, link in enumerate(doc_links, 1):
                                lines.append(f"  Dokumentas {i}: {link}")
                        else:
                            lines.append(f"- {label}: (dokumentų nėra)")
                    else:
                        # Regular event with expiry date
                        if expires:
                            try:
                                exp_date = dt.datetime.fromisoformat(expires).date()
                                if exp_date < today:
                                    status = "nebegalioja"
                                else:
                                    status = f"galioja iki {exp_date.isoformat()}"
                                lines.append(f"- {label}: {status}")
                            except Exception:
                                lines.append(f"- {label}: (data neteisinga)")
                        else:
                            lines.append(f"- {label}: (duomenų nėra)")
                else:
                    # Parameter not found in data - show as missing
                    if event_type == "registration_certificate":
                        lines.append(f"- {label}: (dokumentų nėra)")
                    else:
                        lines.append(f"- {label}: (duomenų nėra)")
            
            await q.edit_message_text("\n".join(lines))
        
        elif data.startswith("approve:"):
            # Approve user from pending list
            if not is_admin(update):
                return
            
            try:
                user_id = int(data.split(":", 1)[1])
            except ValueError:
                return
            
            if not (cfg.spreadsheet_id and cfg.google_credentials_path):
                return
            
            client = SheetsClient(cfg.spreadsheet_id, cfg.google_credentials_path)
            repo = UsersRepo(client, cfg.users_tab_name)
            admin_name = update.effective_user.username or str(update.effective_user.id)
            
            success = repo.approve(user_id, admin_name)
            if success:
                # Clear user cache to reflect changes immediately
                _users_cache['approved_users'] = set()
                _users_cache['timestamp'] = 0
                await q.edit_message_text(f"✅ Vartotojas {user_id} patvirtintas.")
                
                # Send welcome message to approved user
                try:
                    from telegram import Bot
                    bot = Bot(token=cfg.telegram_bot_token)
                    welcome_msg = """
🎉 Sveikiname! Jūsų prieiga patvirtinta.

Galimos komandos:
/info - Šiandienos priminimas
/sarasas - Visų numerių sąrašas  
/id <numeris> - Konkretaus numerio duomenys

Botas automatiškai siųs priminimus kiekvieną dieną 8:00 val.
                    """
                    await bot.send_message(chat_id=user_id, text=welcome_msg.strip())
                except Exception as e:
                    print(f"Failed to send welcome message to {user_id}: {e}")
            else:
                await q.edit_message_text(f"❌ Nepavyko patvirtinti vartotojo {user_id}.")
        
        elif data.startswith("reject:"):
            # Reject user from pending list
            if not is_admin(update):
                return
            
            try:
                user_id = int(data.split(":", 1)[1])
            except ValueError:
                return
            
            if not (cfg.spreadsheet_id and cfg.google_credentials_path):
                return
            
            client = SheetsClient(cfg.spreadsheet_id, cfg.google_credentials_path)
            repo = UsersRepo(client, cfg.users_tab_name)
            admin_name = update.effective_user.username or str(update.effective_user.id)
            
            success = repo.reject(user_id, admin_name)
            if success:
                await q.edit_message_text(f"❌ Vartotojas {user_id} atmestas.")
            else:
                await q.edit_message_text(f"❌ Nepavyko atmesti vartotojo {user_id}.")
        
        elif data.startswith("delete_user:"):
            # Delete user from users list
            if not is_admin(update):
                return
            
            try:
                user_id = int(data.split(":", 1)[1])
            except ValueError:
                return
            
            if not (cfg.spreadsheet_id and cfg.google_credentials_path):
                return
            
            client = SheetsClient(cfg.spreadsheet_id, cfg.google_credentials_path)
            repo = UsersRepo(client, cfg.users_tab_name)
            
            success = repo.delete_user(user_id)
            if success:
                # Clear user cache to reflect changes immediately
                _users_cache['approved_users'] = set()
                _users_cache['timestamp'] = 0
                await q.edit_message_text(f"🗑️ Vartotojas {user_id} ištrintas.")
            else:
                await q.edit_message_text(f"❌ Nepavyko ištrinti vartotojo {user_id}.")
        
        elif data.startswith("user_info:"):
            # Show user info (placeholder for future enhancement)
            await q.answer("Vartotojo informacija")

    app.add_handler(CallbackQueryHandler(on_cb))

    # Whoami command
    async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
        u = update.effective_user
        if not u:
            return
        await update.message.reply_text(f"user_id={u.id}, username={(u.username or '')}")

    app.add_handler(CommandHandler('whoami', whoami))

    # Admin pending users
    async def pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update):
            await update.message.reply_text("Neturite teisės naudoti šios komandos.")
            return
        
        if not (cfg.spreadsheet_id and cfg.google_credentials_path):
            await update.message.reply_text("Trūksta Sheets konfigūracijos.")
            return
        
        client = SheetsClient(cfg.spreadsheet_id, cfg.google_credentials_path)
        repo = UsersRepo(client, cfg.users_tab_name)
        pending_users = repo.list_pending()
        
        if not pending_users:
            await update.message.reply_text("Nėra laukiančių vartotojų.")
            return
        
        buttons = []
        for user in pending_users:
            username = user.telegram_username or str(user.telegram_user_id)
            buttons.append([
                InlineKeyboardButton(f"✅ {username}", callback_data=f"approve:{user.telegram_user_id}"),
                InlineKeyboardButton(f"❌ {username}", callback_data=f"reject:{user.telegram_user_id}")
            ])
        
        await update.message.reply_text(
            f"Laukiantys vartotojai ({len(pending_users)}):",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    app.add_handler(CommandHandler('pending', pending))

    # Admin users management
    async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update):
            await update.message.reply_text("Neturite teisės naudoti šios komandos.")
            return
        
        if not (cfg.spreadsheet_id and cfg.google_credentials_path):
            await update.message.reply_text("Trūksta Sheets konfigūracijos.")
            return
        
        client = SheetsClient(cfg.spreadsheet_id, cfg.google_credentials_path)
        repo = UsersRepo(client, cfg.users_tab_name)
        all_users = repo.list_all()
        
        if not all_users:
            await update.message.reply_text("Vartotojų nėra.")
            return
        
        buttons = []
        for user in all_users:
            username = user.telegram_username or str(user.telegram_user_id)
            status = user.status or "unknown"
            status_emoji = {"approved": "✅", "pending": "⏳", "rejected": "❌"}.get(status, "❓")
            
            buttons.append([
                InlineKeyboardButton(
                    f"{status_emoji} {username} ({status})", 
                    callback_data=f"user_info:{user.telegram_user_id}"
                ),
                InlineKeyboardButton(
                    "🗑️ Delete", 
                    callback_data=f"delete_user:{user.telegram_user_id}"
                )
            ])
        
        await update.message.reply_text(
            f"Visi vartotojai ({len(all_users)}):",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    app.add_handler(CommandHandler('users', users_cmd))

    # Admin approve command
    async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update):
            await update.message.reply_text("Neturite teisės naudoti šios komandos.")
            return
        
        args = context.args or []
        if not args:
            await update.message.reply_text("Naudojimas: /approve <user_id>")
            return
        
        try:
            user_id = int(args[0])
        except ValueError:
            await update.message.reply_text("Neteisingas user_id.")
            return
        
        if not (cfg.spreadsheet_id and cfg.google_credentials_path):
            await update.message.reply_text("Trūksta Sheets konfigūracijos.")
            return
        
        client = SheetsClient(cfg.spreadsheet_id, cfg.google_credentials_path)
        repo = UsersRepo(client, cfg.users_tab_name)
        admin_name = update.effective_user.username or str(update.effective_user.id)
        
        success = repo.approve(user_id, admin_name)
        if success:
            # Clear user cache to reflect changes immediately
            _users_cache['approved_users'] = set()
            _users_cache['timestamp'] = 0
            await update.message.reply_text(f"✅ Vartotojas {user_id} patvirtintas.")
        else:
            await update.message.reply_text(f"❌ Nepavyko patvirtinti vartotojo {user_id}.")

    app.add_handler(CommandHandler('approve', approve_cmd))

    # Manual send today command
    async def sendtoday_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update):
            await update.message.reply_text("Neturite teisės naudoti šios komandos.")
            return
        
        if not data_sync.is_data_available():
            await update.message.reply_text("❌ Duomenų nėra. Naudokite /update.")
            return
        
        # Get processed data from JSON storage
        tuples = data_sync.get_processed_data_for_reminders()
        if not tuples:
            await update.message.reply_text("📭 Nėra duomenų pranešimams.")
            return
        
        # Process deadlines
        latest = latest_by_plate_event(tuples)
        today = dt.date.today()
        upcoming, expired = compute_windows(today, latest)
        text = format_summary_lt(upcoming, expired)
        
        if not text.strip() or "Šiandien priminimų nėra" in text:
            await update.message.reply_text("📭 Šiandien priminimų nėra.")
            return
        
        # Send to all approved users
        if not (cfg.spreadsheet_id and cfg.google_credentials_path):
            await update.message.reply_text("Trūksta Sheets konfigūracijos.")
            return
        
        client = SheetsClient(cfg.spreadsheet_id, cfg.google_credentials_path)
        repo = UsersRepo(client, cfg.users_tab_name)
        approved = repo.list_approved()
        
        sent = 0
        for user in approved:
            if user.telegram_chat_id:
                try:
                    await context.bot.send_message(chat_id=user.telegram_chat_id, text=text)
                    sent += 1
                except Exception:
                    pass
        
        await update.message.reply_text(f"Išsiųsta {sent} vartotojams.")

    app.add_handler(CommandHandler('sendtoday', sendtoday_cmd))

    # Schedule daily reminders
    print("📅 Scheduling daily reminders for 08:00 Europe/Vilnius...", flush=True)
    
    # Use pytz for accurate timezone handling
    try:
        import pytz
        vilnius_tz = pytz.timezone('Europe/Vilnius')
        # Convert 8:00 AM Vilnius time to UTC
        vilnius_time = dt.datetime.now(vilnius_tz).replace(hour=8, minute=0, second=0, microsecond=0)
        utc_time = vilnius_time.astimezone(pytz.UTC).time()
        app.job_queue.run_daily(daily_job, time=utc_time)
        print(f"✅ Daily reminders scheduled for {utc_time} UTC (08:00 Vilnius)", flush=True)
    except ImportError:
        # Fallback: Calculate UTC time manually
        # Lithuania is UTC+2 (UTC+3 in summer DST)
        now = dt.datetime.now()
        if now.month >= 3 and now.month <= 10:  # Rough DST period
            utc_hour = 8 - 3  # 05:00 UTC = 08:00 UTC+3
        else:
            utc_hour = 8 - 2  # 06:00 UTC = 08:00 UTC+2
        
        if utc_hour < 0:
            utc_hour += 24
        
        app.job_queue.run_daily(daily_job, time=dt.time(utc_hour, 0))
        print(f"✅ Daily reminders scheduled for {utc_hour:02d}:00 UTC (08:00 Vilnius)", flush=True)
    
    print("🤖 Starting bot...", flush=True)
    sys.stdout.flush()
    
    # Start the bot
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
