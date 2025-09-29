from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters
import re
from .config import load_config
from .sheets_client import SheetsClient
from .data_model import normalize_event, latest_by_plate_event, compute_windows, format_summary_lt
import datetime as dt
import asyncio
from datetime import timezone, timedelta
from .users_repo import UsersRepo

load_dotenv()

cfg = load_config()

async def send_daily_reminders():
    """Send daily vehicle reminders to all approved users at 8:00 AM Lithuanian time."""
    lithuania_tz = timezone(timedelta(hours=2))  # Lithuania is UTC+2 (UTC+3 in summer)
    print("🕐 Starting daily reminder sending...")
    
    try:
        if not (cfg.spreadsheet_id and cfg.google_credentials_path):
            print("⚠️ Sheets configuration missing, skipping daily reminders")
            return
            
        # Get vehicle data and process deadlines
        client = SheetsClient(cfg.spreadsheet_id, cfg.google_credentials_path)
        raw = client.read_data_rows(cfg.data_tab_name)
        
        # Normalize data
        tuples = []
        for r in raw:
            ev = normalize_event(r.event_raw)
            if not ev:
                continue
            exp = SheetsClient.parse_mmddyyyy(r.expiry_raw)
            ts = None
            if r.timestamp:
                try:
                    ts = dt.datetime.strptime(r.timestamp, "%m/%d/%Y %H:%M:%S")
                except Exception:
                    ts = None
            tuples.append((r.plate, ev, exp, ts))
        
        # Process deadlines
        latest = latest_by_plate_event(tuples)
        today = dt.date.today()
        upcoming, expired = compute_windows(today, latest)
        text = format_summary_lt(upcoming, expired)
        
        # Get approved users + admins
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

async def schedule_daily_reminders():
    """Schedule daily reminder sending at 8:00 AM Lithuanian time."""
    lithuania_tz = timezone(timedelta(hours=2))  # Lithuania is UTC+2 (UTC+3 in summer)
    
    while True:
        try:
            now = dt.datetime.now(lithuania_tz)
            target_time = now.replace(hour=8, minute=0, second=0, microsecond=0)
            
            # If target time has passed today, set for tomorrow
            if now >= target_time:
                target_time += timedelta(days=1)
            
            # Calculate wait time
            wait_seconds = (target_time - now).total_seconds()
            print(f"📅 Next daily reminder scheduled for: {target_time} (in {wait_seconds/3600:.2f} hours)")
            
            # Wait until target time
            await asyncio.sleep(wait_seconds)
            
            # Send daily reminders
            await send_daily_reminders()
            
        except Exception as e:
            print(f"❌ Error in reminder scheduler: {e}")
            # Wait 1 hour before retrying
            await asyncio.sleep(3600)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    # Try to upsert user as pending in Users sheet
    try:
        if cfg.spreadsheet_id and cfg.google_credentials_path:
            client = SheetsClient(cfg.spreadsheet_id, cfg.google_credentials_path)
            repo = UsersRepo(client, cfg.users_tab_name)
            found = repo.find_by_user_id(user.id)
            if found:
                _, row = found
                # Update username/chat id just in case
                repo.upsert_pending(user.id, user.username, chat.id if chat else None)
                if row.status == 'approved':
                    await update.message.reply_text('Jūsų paskyra jau patvirtinta. Naudokite /pagalba.')
                    return
                elif row.status == 'pending':
                    await update.message.reply_text('Jūsų prašymas jau laukia patvirtinimo. Administratorius netrukus patvirtins.')
                    return
                else:
                    await update.message.reply_text('Sveiki! Prašymas užregistruotas. Administratorius netrukus patvirtins.')
                    return
            else:
                repo.upsert_pending(user.id, user.username, chat.id if chat else None)
                await update.message.reply_text('Sveiki! Prašymas užregistruotas. Administratorius netrukus patvirtins.')
                return
        else:
            await update.message.reply_text('Sveiki! Botas veikia. (/start)')
            return
    except Exception:
        # Fail-safe generic reply
        await update.message.reply_text("Sveiki! Botas veikia. (/start)")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Komandos:\n"
        "/start — registracija arba būsena\n"
        "/pagalba — ši pagalba\n"
        "/info — šiandienos priminimai\n"
        "/sarasas — visų numerių sąrašas\n"
        "/id <numeris> — rodyti vieno numerio įvykius (pvz.: /id ABC123)\n"
        "Arba tiesiog /ABC123 — greitasis numerio peržiūra"
    )

def main():
    app = Application.builder().token(cfg.telegram_bot_token).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('pagalba', help_cmd))
    
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
        client = SheetsClient(cfg.spreadsheet_id, cfg.google_credentials_path)
        repo = UsersRepo(client, cfg.users_tab_name)
        found = repo.find_by_user_id(u.id)
        return bool(found and found[1].status == 'approved')
    
    # Add a dry-run command for admins to preview today's summary in DM
    async def dryrun(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if is_admin(update):
            if not (cfg.spreadsheet_id and cfg.google_credentials_path):
                await update.message.reply_text("Trūksta Sheets konfigūracijos.")
                return
            client = SheetsClient(cfg.spreadsheet_id, cfg.google_credentials_path)
            raw = client.read_data_rows(cfg.data_tab_name)
            # Normalize
            tuples = []
            for r in raw:
                ev = normalize_event(r.event_raw)
                if not ev:
                    continue
                exp = SheetsClient.parse_mmddyyyy(r.expiry_raw)
                ts = None
                if r.timestamp:
                    try:
                        ts = dt.datetime.strptime(r.timestamp, "%m/%d/%Y %H:%M:%S")
                    except Exception:
                        ts = None
                tuples.append((r.plate, ev, exp, ts))
            latest = latest_by_plate_event(tuples)
            today = dt.date.today()
            upcoming, expired = compute_windows(today, latest)
            text = format_summary_lt(upcoming, expired)
            await update.message.reply_text(text)
        else:
            await update.message.reply_text("Neturite teisės naudoti šios komandos.")

    app.add_handler(CommandHandler('dryrun', dryrun))

    async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
        u = update.effective_user
        if not u:
            return
        await update.message.reply_text(f"user_id={u.id}, username={(u.username or '')}")

    app.add_handler(CommandHandler('whoami', whoami))

    # Info command - today's summary for approved users
    async def info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await is_approved_user(update):
            await update.message.reply_text("Jūsų prieiga dar nepatvirtinta.")
            return
        if not (cfg.spreadsheet_id and cfg.google_credentials_path):
            await update.message.reply_text("Trūksta Sheets konfigūracijos.")
            return
        client = SheetsClient(cfg.spreadsheet_id, cfg.google_credentials_path)
        raw = client.read_data_rows(cfg.data_tab_name)
        # Normalize
        tuples = []
        for r in raw:
            ev = normalize_event(r.event_raw)
            if not ev:
                continue
            exp = SheetsClient.parse_mmddyyyy(r.expiry_raw)
            ts = None
            if r.timestamp:
                try:
                    ts = dt.datetime.strptime(r.timestamp, "%m/%d/%Y %H:%M:%S")
                except Exception:
                    ts = None
            tuples.append((r.plate, ev, exp, ts))
        latest = latest_by_plate_event(tuples)
        today = dt.date.today()
        upcoming, expired = compute_windows(today, latest)
        text = format_summary_lt(upcoming, expired)
        await update.message.reply_text(text)

    app.add_handler(CommandHandler('info', info_cmd))

    # Public user commands (require approval)
    async def sarasas(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await is_approved_user(update):
            await update.message.reply_text("Jūsų prieiga dar nepatvirtinta.")
            return
        if not (cfg.spreadsheet_id and cfg.google_credentials_path):
            await update.message.reply_text("Trūksta Sheets konfigūracijos.")
            return
        client = SheetsClient(cfg.spreadsheet_id, cfg.google_credentials_path)
        raw = client.read_data_rows(cfg.data_tab_name)
        tuples = []
        for r in raw:
            ev = normalize_event(r.event_raw)
            if not ev:
                continue
            exp = SheetsClient.parse_mmddyyyy(r.expiry_raw)
            ts = None
            if r.timestamp:
                try:
                    ts = dt.datetime.strptime(r.timestamp, "%m/%d/%Y %H:%M:%S")
                except Exception:
                    ts = None
            if r.plate:
                tuples.append((r.plate.strip().upper(), ev, exp, ts))
        latest = latest_by_plate_event(tuples)
        plates = sorted({r.plate for r in latest})
        if not plates:
            await update.message.reply_text("Sąrašas tuščias.")
            return
        rows = []
        buttons = []
        for p in plates:
            rows.append(p)
            buttons.append([InlineKeyboardButton(p, callback_data=f"plate:{p}")])
        await update.message.reply_text(
            "Numerių sąrašas:\n" + "\n".join(rows),
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    app.add_handler(CommandHandler('sarasas', sarasas))

    async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await is_approved_user(update):
            await update.message.reply_text("Jūsų prieiga dar nepatvirtinta.")
            return
        args = context.args or []
        if not args:
            await update.message.reply_text("Naudojimas: /id <numeris>")
            return
        plate = args[0].strip().upper()
        if not (cfg.spreadsheet_id and cfg.google_credentials_path):
            await update.message.reply_text("Trūksta Sheets konfigūracijos.")
            return
        client = SheetsClient(cfg.spreadsheet_id, cfg.google_credentials_path)
        raw = client.read_data_rows(cfg.data_tab_name)
        tuples = []
        for r in raw:
            ev = normalize_event(r.event_raw)
            if not ev:
                continue
            exp = SheetsClient.parse_mmddyyyy(r.expiry_raw)
            ts = None
            if r.timestamp:
                try:
                    ts = dt.datetime.strptime(r.timestamp, "%m/%d/%Y %H:%M:%S")
                except Exception:
                    ts = None
            if r.plate:
                tuples.append((r.plate.strip().upper(), ev, exp, ts))
        latest = latest_by_plate_event(tuples)
        items = [r for r in latest if r.plate == plate]
        if not items:
            await update.message.reply_text("Numeris nerastas.")
            return
        # Build details
        lines = [f"{plate}:"]
        today = dt.date.today()
        for r in sorted(items, key=lambda x: (x.event_type)):
            label = {
                "lv_road_toll": "LV kelių mokestis",
                "lt_road_toll": "LT kelių mokestis",
                "inspection": "TA galiojimas",
                "insurance": "CA draudimas",
                "registration_certificate": "Registracijos liudijimas",
            }.get(r.event_type, r.event_type)
            if r.event_type == 'registration_certificate':
                # Find the raw row to get document links
                doc_links = []
                for raw_r in raw:
                    if (raw_r.plate and raw_r.plate.strip().upper() == plate and 
                        normalize_event(raw_r.event_raw) == 'registration_certificate'):
                        if raw_r.doc1:
                            doc_links.append(raw_r.doc1)
                        if raw_r.doc2:
                            doc_links.append(raw_r.doc2)
                        break
                if doc_links:
                    lines.append(f"- {label}:")
                    for i, link in enumerate(doc_links, 1):
                        lines.append(f"  Dokumentas {i}: {link}")
                else:
                    lines.append(f"- {label}: (dokumentų nėra)")
            else:
                if r.expiry_date:
                    status = "nebegalioja" if r.expiry_date < today else f"galioja iki {r.expiry_date.isoformat()}"
                    lines.append(f"- {label}: {status}")
        await update.message.reply_text("\n".join(lines))

    app.add_handler(CommandHandler('id', cmd_id))

    # Fallback: treat unknown commands like /ABC123 as plate queries
    async def plate_shortcut(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return
        text = update.message.text.strip()
        if not text.startswith('/'):
            return
        cmd = text[1:].strip()
        # Ignore known commands
        known = {
            'start','pagalba','sarasas','id','dryrun','whoami','pending','approve','sendtoday','users','info'
        }
        if cmd.lower() in known or not cmd:
            return
        # If not a plate-like token, respond with unknown command help
        if not re.fullmatch(r'[A-Za-z0-9-]{2,}', cmd):
            await update.message.reply_text("Nesupratau komandos. Naudokite /pagalba.")
            return
        # Gate access (admins bypass)
        if not await is_approved_user(update):
            await update.message.reply_text("Jūsų prieiga dar nepatvirtinta.")
            return
        plate = cmd.upper()
        if not (cfg.spreadsheet_id and cfg.google_credentials_path):
            await update.message.reply_text("Trūksta Sheets konfigūracijos.")
            return
        client = SheetsClient(cfg.spreadsheet_id, cfg.google_credentials_path)
        raw = client.read_data_rows(cfg.data_tab_name)
        tuples = []
        for r in raw:
            ev = normalize_event(r.event_raw)
            if not ev:
                continue
            exp = SheetsClient.parse_mmddyyyy(r.expiry_raw)
            ts = None
            if r.timestamp:
                try:
                    ts = dt.datetime.strptime(r.timestamp, "%m/%d/%Y %H:%M:%S")
                except Exception:
                    ts = None
            if r.plate:
                tuples.append((r.plate.strip().upper(), ev, exp, ts))
        latest = latest_by_plate_event(tuples)
        items = [r for r in latest if r.plate == plate]
        if not items:
            await update.message.reply_text("Numeris nerastas.")
            return
        lines = [f"{plate}:"]
        today = dt.date.today()
        for r in sorted(items, key=lambda x: (x.event_type)):
            label = {
                "lv_road_toll": "LV kelių mokestis",
                "lt_road_toll": "LT kelių mokestis",
                "inspection": "TA galiojimas",
                "insurance": "CA draudimas",
                "registration_certificate": "Registracijos liudijimas",
            }.get(r.event_type, r.event_type)
            if r.event_type == 'registration_certificate':
                # Find the raw row to get document links
                doc_links = []
                for raw_r in raw:
                    if (raw_r.plate and raw_r.plate.strip().upper() == plate and 
                        normalize_event(raw_r.event_raw) == 'registration_certificate'):
                        if raw_r.doc1:
                            doc_links.append(raw_r.doc1)
                        if raw_r.doc2:
                            doc_links.append(raw_r.doc2)
                        break
                if doc_links:
                    lines.append(f"- {label}:")
                    for i, link in enumerate(doc_links, 1):
                        lines.append(f"  Dokumentas {i}: {link}")
                else:
                    lines.append(f"- {label}: (dokumentų nėra)")
            else:
                if r.expiry_date:
                    status = "nebegalioja" if r.expiry_date < today else f"galioja iki {r.expiry_date.isoformat()}"
                    lines.append(f"- {label}: {status}")
        await update.message.reply_text("\n".join(lines))

    async def pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            await update.message.reply_text("Debug: /pending received")
            if not is_admin(update):
                await update.message.reply_text("Neturite teisės naudoti šios komandos.")
                return
            await update.message.reply_text("Debug: admin check passed")
            if not (cfg.spreadsheet_id and cfg.google_credentials_path):
                await update.message.reply_text("Trūksta Sheets konfigūracijos.")
                return
            await update.message.reply_text("Debug: config check passed")
            client = SheetsClient(cfg.spreadsheet_id, cfg.google_credentials_path)
            repo = UsersRepo(client, cfg.users_tab_name)
            pend = repo.list_pending()
            await update.message.reply_text(f"Debug: found {len(pend)} pending users")
            if not pend:
                await update.message.reply_text("Laukiančių nėra.")
                return
            buttons = []
            text_lines = []
            for p in pend:
                text_lines.append(f"@{p.telegram_username or p.telegram_user_id} (id={p.telegram_user_id})")
                buttons.append([
                    InlineKeyboardButton("✅ Patvirtinti", callback_data=f"approve:{p.telegram_user_id}"),
                    InlineKeyboardButton("❌ Atmesti", callback_data=f"reject:{p.telegram_user_id}"),
                ])
            await update.message.reply_text(
                "Laukiantys:\n" + "\n".join(text_lines),
                reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
            )
        except Exception as e:
            await update.message.reply_text(f"Debug error: {str(e)}")

    app.add_handler(CommandHandler('pending', pending))

    async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update):
            await update.message.reply_text("Neturite teisės naudoti šios komandos.")
            return
        args = context.args or []
        if not args:
            await update.message.reply_text("Naudojimas: /approve <user_id>")
            return
        try:
            uid = int(args[0])
        except Exception:
            await update.message.reply_text("Netinkamas user_id")
            return
        client = SheetsClient(cfg.spreadsheet_id, cfg.google_credentials_path)
        repo = UsersRepo(client, cfg.users_tab_name)
        ok = repo.approve(uid, approved_by=(update.effective_user.username or str(update.effective_user.id)))
        await update.message.reply_text("Patvirtinta" if ok else "Nerastas vartotojas")

    app.add_handler(CommandHandler('approve', approve))

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
        lines = []
        buttons = []
        for u in all_users:
            uname = f"@{u.telegram_username}" if u.telegram_username else "(be username)"
            lines.append(f"{uname} id={u.telegram_user_id} status={u.status}")
            buttons.append([InlineKeyboardButton(f"🗑️ Šalinti {u.telegram_user_id}", callback_data=f"deluser:{u.telegram_user_id}")])
        await update.message.reply_text("Vartotojai:\n" + "\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))

    app.add_handler(CommandHandler('users', users_cmd))

    async def on_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        # Acknowledge ASAP to avoid Telegram timeout
        try:
            await q.answer("Apdorojama…")
        except Exception:
            pass
        data = q.data or ""
        if data.startswith("plate:"):
            # Plate detail from inline selection (requires approval)
            if not await is_approved_user(update):
                return
            plate = data.split(":", 1)[1].strip().upper()
            if not (cfg.spreadsheet_id and cfg.google_credentials_path):
                return
            client = SheetsClient(cfg.spreadsheet_id, cfg.google_credentials_path)
            raw = client.read_data_rows(cfg.data_tab_name)
            tuples = []
            for r in raw:
                ev = normalize_event(r.event_raw)
                if not ev:
                    continue
                exp = SheetsClient.parse_mmddyyyy(r.expiry_raw)
                ts = None
                if r.timestamp:
                    try:
                        ts = dt.datetime.strptime(r.timestamp, "%m/%d/%Y %H:%M:%S")
                    except Exception:
                        ts = None
                if r.plate:
                    tuples.append((r.plate.strip().upper(), ev, exp, ts))
            latest = latest_by_plate_event(tuples)
            items = [r for r in latest if r.plate == plate]
            if not items:
                await q.edit_message_text("Numeris nerastas.")
                return
            lines = [f"{plate}:"]
            today = dt.date.today()
            for r in sorted(items, key=lambda x: (x.event_type)):
                label = {
                    "lv_road_toll": "LV kelių mokestis",
                    "lt_road_toll": "LT kelių mokestis",
                    "inspection": "TA galiojimas",
                    "insurance": "CA draudimas",
                    "registration_certificate": "Registracijos liudijimas",
                }.get(r.event_type, r.event_type)
                if r.event_type == 'registration_certificate':
                    # Find the raw row to get document links
                    doc_links = []
                    for raw_r in raw:
                        if (raw_r.plate and raw_r.plate.strip().upper() == plate and 
                            normalize_event(raw_r.event_raw) == 'registration_certificate'):
                            if raw_r.doc1:
                                doc_links.append(raw_r.doc1)
                            if raw_r.doc2:
                                doc_links.append(raw_r.doc2)
                            break
                    if doc_links:
                        lines.append(f"- {label}:")
                        for i, link in enumerate(doc_links, 1):
                            lines.append(f"  Dokumentas {i}: {link}")
                    else:
                        lines.append(f"- {label}: (dokumentų nėra)")
                else:
                    if r.expiry_date:
                        status = "nebegalioja" if r.expiry_date < today else f"galioja iki {r.expiry_date.isoformat()}"
                        lines.append(f"- {label}: {status}")
            try:
                await q.edit_message_text("\n".join(lines))
            except Exception:
                pass
            return

        # Admin-only actions
        action, _, id_str = data.partition(":")
        if action in ("approve", "reject", "deluser"):
            if not is_admin(update):
                return
            try:
                uid = int(id_str)
            except Exception:
                return
            client = SheetsClient(cfg.spreadsheet_id, cfg.google_credentials_path)
            repo = UsersRepo(client, cfg.users_tab_name)
            admin_name = (update.effective_user.username or str(update.effective_user.id))
            user_chat_id = None
            found = repo.find_by_user_id(uid)
            if found:
                _, row = found
                user_chat_id = row.telegram_chat_id
            if action == "approve":
                ok = repo.approve(uid, approved_by=admin_name)
                if ok and user_chat_id:
                    try:
                        welcome_msg = (
                            "🎉 Sveiki! Jūsų prieiga patvirtinta.\n\n"
                            "Galimos komandos:\n"
                            "📋 /info — šiandienos priminimai\n"
                            "📝 /sarasas — visų numerių sąrašas\n"
                            "🔍 /ABC123 — greitasis numerio peržiūra\n"
                            "❓ /pagalba — visų komandų sąrašas\n\n"
                            "Gausite automatinius priminimus kasdien 08:00 apie artėjančius ir pasibaigusius terminus."
                        )
                        await context.bot.send_message(chat_id=user_chat_id, text=welcome_msg)
                    except Exception:
                        pass
                try:
                    await q.edit_message_reply_markup(reply_markup=None)
                except Exception:
                    pass
                return
            if action == "reject":
                ok = repo.reject(uid, rejected_by=admin_name)
                if ok and user_chat_id:
                    try:
                        await context.bot.send_message(chat_id=user_chat_id, text="Jūsų prašymas atmestas. Susisiekite su administratoriumi, jei manote, kad tai klaida.")
                    except Exception:
                        pass
                try:
                    await q.edit_message_reply_markup(reply_markup=None)
                except Exception:
                    pass
                return
            if action == "deluser":
                ok = repo.delete_user(uid)
                try:
                    await q.edit_message_text("Vartotojas ištrintas." if ok else "Vartotojas nerastas.")
                except Exception:
                    pass
                return

    app.add_handler(CallbackQueryHandler(on_cb))

    async def sendtoday(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update):
            await update.message.reply_text("Neturite teisės naudoti šios komandos.")
            return
        if not (cfg.spreadsheet_id and cfg.google_credentials_path):
            await update.message.reply_text("Trūksta Sheets konfigūracijos.")
            return
        client = SheetsClient(cfg.spreadsheet_id, cfg.google_credentials_path)
        # Compose summary once
        raw = client.read_data_rows(cfg.data_tab_name)
        tuples = []
        for r in raw:
            ev = normalize_event(r.event_raw)
            if not ev:
                continue
            exp = SheetsClient.parse_mmddyyyy(r.expiry_raw)
            ts = None
            if r.timestamp:
                try:
                    ts = dt.datetime.strptime(r.timestamp, "%m/%d/%Y %H:%M:%S")
                except Exception:
                    ts = None
            tuples.append((r.plate, ev, exp, ts))
        latest = latest_by_plate_event(tuples)
        today = dt.date.today()
        upcoming, expired = compute_windows(today, latest)
        text = format_summary_lt(upcoming, expired)
        # Send to all approved users with a chat_id
        repo = UsersRepo(client, cfg.users_tab_name)
        approved = repo.list_approved()
        sent = 0
        for u in approved:
            if u.telegram_chat_id:
                try:
                    await context.bot.send_message(chat_id=u.telegram_chat_id, text=text)
                    sent += 1
                except Exception:
                    pass
        await update.message.reply_text(f"Išsiųsta {sent} vartotojams.")

    app.add_handler(CommandHandler('sendtoday', sendtoday))

    # Must be after ALL specific command handlers
    app.add_handler(MessageHandler(filters.COMMAND, plate_shortcut))

    print("🤖 Starting bot...")
    
    # Add error handler for conflicts (less verbose)
    async def error_handler(update, context):
        import logging
        logging.basicConfig(level=logging.ERROR)  # Only show errors, not warnings
        logger = logging.getLogger(__name__)
        
        if "Conflict" in str(context.error):
            # Don't log conflicts - they're handled automatically
            return
        logger.error(f"❌ Bot error: {context.error}")
    
    app.add_error_handler(error_handler)
    
    # Add simple daily job using telegram's job queue
    async def daily_job(context: ContextTypes.DEFAULT_TYPE):
        print("🕐 Daily job triggered - sending reminders...")
        await send_daily_reminders()
    
    # Schedule daily job at 8:00 AM Lithuania time
    try:
        import pytz
        lithuania_tz = pytz.timezone('Europe/Vilnius')
        print("✅ Using pytz for Lithuania timezone")
    except ImportError:
        # Fallback - Lithuania is UTC+2 in winter, UTC+3 in summer
        from datetime import datetime
        now = datetime.now()
        # Simple DST check: DST is roughly March-October
        if 3 <= now.month <= 10:
            lithuania_tz = timezone(timedelta(hours=3))  # Summer time
            print("🌞 Using UTC+3 (summer time)")
        else:
            lithuania_tz = timezone(timedelta(hours=2))  # Winter time  
            print("❄️ Using UTC+2 (winter time)")
    
    # Add the daily job
    job_time = dt.time(hour=8, minute=0, tzinfo=lithuania_tz)
    app.job_queue.run_daily(daily_job, time=job_time, name="daily_reminders")
    print(f"📅 Daily reminder job scheduled for 08:00 {lithuania_tz}")
    
    # Start the bot normally
    print("🤖 Starting bot with daily reminders...")
    print("⏳ Waiting 3 seconds for any previous instances to clear...")
    
    import time
    time.sleep(3)  # Give time for previous instance to clear
    
    print("🚀 Starting polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()