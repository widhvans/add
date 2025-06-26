import logging, os, time, json, re, asyncio, random, datetime
from datetime import timezone
from telethon import TelegramClient, events, functions
from telethon.sessions import StringSession
from telethon.tl.custom.button import Button
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError, PasswordHashInvalidError,
    FloodWaitError, UserIsBlockedError, InputUserDeactivatedError,
    UserNotParticipantError, MessageNotModifiedError, PeerFloodError
)
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi

import config
from strings import strings
import members_adder # Import our new members adding module

# --- Basic Setup ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
LOGGER = logging.getLogger(__name__)

# --- DATABASE SETUP ---
try:
    mongo_client = MongoClient(config.MONGODB_URL, server_api=ServerApi('1'))
    db = mongo_client.telegram_bot_db
    users_db = db.users # This will store owner's data, including their user_accounts and adding_tasks
    bot_settings_db = db.bot_settings
    mongo_client.admin.command('ping')
    LOGGER.info("Successfully connected to MongoDB.")
except Exception as e:
    LOGGER.critical(f"CRITICAL ERROR: Failed to connect to MongoDB: {e}."); exit(1)

# --- BOT & DEVICE SIMULATION SETUP ---
BOT_USERNAME = None
device_info = {'device_model':'Samsung Galaxy S25 Ultra','system_version':'SDK 35 (Android 15)','app_version':'11.5.0 (5124)','lang_code':'en','system_lang_code':'en-US'}
bot = TelegramClient('bot_session', config.API_ID, config.API_HASH)

# --- GLOBAL VARS for Queues & Workers (Simplified) ---
# Only for general bot owner commands, not for member adding which is managed by members_adder.py
USER_QUEUES = {}
ACTIVE_TASKS = {} # For owner's command queue (e.g., /broadcast)

# --- Helper Functions ---
def get(o, k, d=None): return o.get(k, d) if o else d
def yesno(c): return [[Button.inline("Yes", f'{{"action":"yes_{c}"}}')], [Button.inline("No", f'{{"action":"no_{c}"}}')]]
def fd(s): # Format duration
    if s is None:return"N/A"
    m,s=divmod(int(s),60);h,m=divmod(m,60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h>0 else f"{m:02d}:{s:02d}"

def strip_html(text):
    if not isinstance(text, str):
        return text
    clean = re.compile('<.*?>')
    return re.sub(clean, '', text)

async def delete_after(m,d):
    await asyncio.sleep(d)
    try:
        if isinstance(m, list):
            if m:
                await bot.delete_messages(m[0].chat_id, m)
        else:
            await m.delete()
    except Exception as e:
        LOGGER.warning(f"Could not delete message after timeout: {e}")

# --- User Broadcast Function (Kept for owner) ---
async def run_user_broadcast(uid, message_to_send):
    status_msg = await bot.send_message(uid, strings['BROADCAST_STARTED'], parse_mode='html')
    d = users_db.find_one({"chat_id": uid})
    
    session_string = get(d, 'session')
    if not session_string:
        return await status_msg.edit(strings['session_invalid'], parse_mode='html')

    u_client = TelegramClient(StringSession(session_string), config.API_ID, config.API_HASH, **device_info)
    sent_count, failed_count, total_count = 0, 0, 0
    last_update_time = time.time()

    try:
        await u_client.connect()
        dialogs = await u_client.get_dialogs()
        targets = [d for d in dialogs if d.is_user and not d.entity.is_self and not d.entity.bot]
        total_count = len(targets)
        
        for i, dialog in enumerate(targets):
            try:
                await u_client.send_message(dialog.id, message_to_send)
                sent_count += 1
                LOGGER.info(f"Broadcast message sent to {dialog.id} for user {uid}")
            except PeerFloodError:
                LOGGER.error(f"PeerFloodError for user {uid}. Stopping broadcast.")
                await status_msg.edit(strings['BROADCAST_PEER_FLOOD'], parse_mode='html')
                return
            except Exception as e:
                failed_count += 1
                LOGGER.warning(f"Broadcast failed for {dialog.id} for user {uid}: {e}")
            
            current_time = time.time()
            if current_time - last_update_time > 4:
                try:
                    await status_msg.edit(strings['BROADCAST_PROGRESS'].format(
                        sent_count=sent_count, total_count=total_count, failed_count=failed_count
                    ), parse_mode='html')
                    last_update_time = current_time
                except MessageNotModifiedError:
                    pass

            await asyncio.sleep(random.uniform(5, 10))

    except Exception as e:
        LOGGER.error(f"Critical error during user broadcast for {uid}: {e}")
        await status_msg.edit(f"An error occurred during broadcast: {e}", parse_mode='html')
    finally:
        if u_client.is_connected():
            await u_client.disconnect()
        await status_msg.edit(strings['BROADCAST_COMPLETE'].format(
            sent_count=sent_count, failed_count=failed_count
        ), parse_mode='html')

# --- CORE FUNCTIONS ---
async def check_fsub(e):
    if not config.FORCE_SUB_CHANNEL or e.sender_id==config.OWNER_ID:return True
    c=config.FORCE_SUB_CHANNEL
    if isinstance(c,str)and c.lstrip('-').isdigit():c=int(c)
    try:
        await bot.get_permissions(c,e.sender_id);return True
    except UserNotParticipantError:
        try:ce=await bot.get_entity(c);cl=f"https://t.me/{ce.username}"
        except:cl="https://t.me/"
        btns=[[Button.url("Join Channel",cl)],[Button.inline("I have Joined, Retry",data=f'{{"action":"retry_fsub"}}')]]
        await e.respond(strings['FSUB_MESSAGE'], buttons=btns, parse_mode='html')
        return False
    except Exception as ex:LOGGER.error(f"F-Sub Error for channel '{c}': {ex}");return True

# Removed `unrestrict`, `process_queue`, `TK` etc as they are part of old features.

async def handle_usr(c,e):
    # This handles the main bot's login and user phone number submission.
    numpad=[[Button.inline(str(i),f'{{"press":{i}}}')for i in range(j,j+3)]for j in range(1,10,3)];numpad.append([Button.inline("Clear All",'{"press":"clear_all"}'),Button.inline("0",'{"press":0}'),Button.inline("⌫",'{"press":"clear"}')])
    await e.delete();m=await e.respond("Requesting OTP...",buttons=None);u=TelegramClient(StringSession(),config.API_ID,config.API_HASH,**device_info)
    try:
        await u.connect();d=users_db.find_one({"chat_id":e.chat_id});cr=await u.send_code_request(c.phone_number)
        ld={'phash':cr.phone_code_hash,'sess':u.session.save(),'clen':cr.type.length}
        if d:
            users_db.update_one({'_id':d['_id']},{'$set':{'ph':c.phone_number,'login':json.dumps(ld)}})
            await m.edit(strings['ask_code'], buttons=numpad, parse_mode='html', link_preview=False)
        else:
            await m.edit("Error: Could not find your user record. Please /start the bot again.")
    except Exception as ex:await m.edit(f"Error: {ex}")
    finally:await u.disconnect()

# --- MENU FUNCTIONS (Simplified) ---
async def send_settings_menu(e, edit=False):
    text = strings['SETTINGS_MENU_TEXT']
    buttons = [
        [Button.inline("👥 Members Adding", data='{"action":"members_adding_menu"}')],
        [Button.inline("📣 Broadcast", data='{"action":"user_broadcast"}')], # Kept for owner
        [Button.inline("« Back to Main", data='{"action":"main_menu"}')]
    ]
    if edit: await e.edit(text, buttons=buttons, parse_mode='html')
    else: await e.respond(text, buttons=buttons, parse_mode='html')

# Removed `send_save_content_menu`, `send_ar_menu`, `send_af_menu`, `send_af_task_menu`.

async def send_chat_selection_menu(e, uid, selection_type, task_id, page=1):
    d = users_db.find_one({"chat_id": uid})
    
    # We need a user account's session string to get dialogs for source/target selection.
    # For now, let's use the owner's main bot session for simplicity IF it's logged in.
    # Otherwise, prompt the owner to log in their main account.
    session_string = d.get('session')
    if not session_string: await e.answer(strings['OWNER_ACCOUNT_LOGIN_REQUIRED'], alert=True); return

    await e.edit("Fetching your chats, please wait...")
    u = TelegramClient(StringSession(session_string), config.API_ID, config.API_HASH, **device_info)
    
    try:
        await u.connect()
        all_dialogs = await u.get_dialogs(limit=None)
        await u.disconnect()
        
        dialogs = [d for d in all_dialogs if not (d.is_user and d.entity.is_self)]

        items_per_page = 5
        total_items = len(dialogs)
        total_pages = (total_items + items_per_page - 1) // items_per_page
        
        start_index = (page - 1) * items_per_page
        end_index = start_index + items_per_page
        
        paginated_dialogs = dialogs[start_index:end_index]
        
        buttons = []
        
        owner_data = users_db.find_one({"chat_id": uid})
        current_task_doc = next((t for t in owner_data.get('adding_tasks', []) if t.get('task_id') == task_id), {})
        selected_to = current_task_doc.get('target_chat_ids', [])
        selected_from = current_task_doc.get('source_chat_id')
        
        buttons.append([Button.inline("🔄 Refresh List", f'm_add_set|{selection_type}|{task_id}|{page}')])

        for dialog in paginated_dialogs:
            prefix = ""
            if selection_type == 'to' and dialog.id in selected_to:
                prefix = "✅ "
            elif selection_type == 'from' and dialog.id == selected_from:
                prefix = "✅ "

            title = (dialog.title[:30] + '..') if len(dialog.title) > 32 else dialog.title
            callback_data = f'm_add_sc|{dialog.id}|{selection_type}|{task_id}|{page}'
            buttons.append([Button.inline(f"{prefix}{title}", callback_data)])
        
        nav_row = []
        if page > 1:
            prev_callback = f'm_add_set|{selection_type}|{task_id}|{page-1}'
            nav_row.append(Button.inline("◀️ Prev", prev_callback))
        
        if total_pages > 0:
            nav_row.append(Button.inline(f"Page {page}/{total_pages}", 'noop'))

        if page < total_pages:
            next_callback = f'm_add_set|{selection_type}|{task_id}|{page+1}'
            nav_row.append(Button.inline("Next ▶️", next_callback))
        
        if nav_row:
            buttons.append(nav_row)

        if selection_type == 'to':
            buttons.append([Button.inline("Done ✅", f'{{"action":"m_add_task_menu", "task_id":{task_id}}}')])
            
        buttons.append([Button.inline("« Back", f'{{"action":"m_add_task_menu", "task_id":{task_id}}}')])
        
        prompt = strings['SELECT_SOURCE_CHAT'] if selection_type == 'from' else strings['SELECT_TARGET_CHAT']
        await e.edit(prompt.format(task_id=task_id), buttons=buttons, parse_mode='html')

    except Exception as ex:
        LOGGER.error(f"Chat selection error for {uid}: {ex}")
        back_action = f'{{"action":"m_add_task_menu", "task_id":{task_id}}}'
        await e.edit("Could not fetch chats. Your session might be invalid. Please try again.", buttons=[[Button.inline("« Back", back_action)]], parse_mode='html')
    finally:
        if u and u.is_connected(): await u.disconnect()

# --- LOGIN & SESSION (Only for Owner's Main Bot Account) ---
async def sign_in(e):
    d=users_db.find_one({"chat_id":e.chat_id});l=json.loads(get(d,'login','{}'));s={}
    if not d: await e.edit("Error: User data not found. Please /start again."); return False
    
    try:
        if get(d,'logged_in'):return True
        u=TelegramClient(StringSession(l.get('sess')),config.API_ID,config.API_HASH,**device_info);await u.connect()
        password = d.get('password')
        phone_number = d.get('ph')
        
        if get(l,'code_ok')and get(l,'pass_ok'):await u.sign_in(password=password)
        elif get(l,'code_ok'):await u.sign_in(phone_number,l.get('code'),phone_code_hash=l.get('phash'))
        else:return False
        
        s.update({'session':u.session.save(),'logged_in':True,'login':'{}'})
        await e.edit(strings['LOGIN_SUCCESS_TEXT'], buttons=None, parse_mode='html')
    except SessionPasswordNeededError:l['need_pass']=True;await e.edit(strings['ask_pass'],buttons=None, parse_mode='html');s['login']=json.dumps(l);return False
    except(PhoneCodeInvalidError,PasswordHashInvalidError)as ex:
        numpad=[[Button.inline(str(i),f'{{"press":{i}}}')for i in range(j,j+3)]for j in range(1,10,3)];numpad.append([Button.inline("Clear All",'{"press":"clear_all"}'),Button.inline("0",'{"press":0}'),Button.inline("⌫",'{"press":"clear"}')])
        errk='code'if isinstance(ex,PhoneCodeInvalidError)else'pass';l.update({'code':'','code_ok':False}if errk=='code'else{'pass_ok':False})
        await e.edit(strings[f'{errk}_invalid'], parse_mode='html');await asyncio.sleep(2)
        await e.edit(strings[f'ask_{errk}'],buttons=numpad if errk=='code'else None, parse_mode='html', link_preview=False);s['login']=json.dumps(l);return False
    except Exception as ex:await e.edit(f"Unexpected error: {ex}");s['login']='{}';return False
    finally:
        if s:users_db.update_one({'_id':d['_id']},{'$set':s})
        if'u'in locals()and u.is_connected():await u.disconnect()
    return get(s,'logged_in',False)

# Removed `start_dl_bot_activation`.

# --- NEW: Member Adding Bot Specific Functions ---
async def send_members_adding_menu(e, uid):
    text = "👥 **Members Adding Bot Settings**\n\n" \
           "Here you can manage your accounts for adding members and set up adding tasks."
    buttons = [
        [Button.inline("➕ Add Account", data='{"action":"add_member_account"}')],
        [Button.inline("📝 Manage Accounts", data='{"action":"manage_member_accounts"}')],
        [Button.inline("➕ Create Task", data='{"action":"create_adding_task"}')],
        [Button.inline("⚙️ Manage Tasks", data='{"action":"manage_adding_tasks"}')],
        [Button.inline("« Back", data='{"action":"settings"}')]
    ]
    await e.edit(text, buttons=buttons, parse_mode='html')

async def handle_add_member_account(e):
    uid = e.sender_id
    users_db.update_one({"chat_id": uid}, {"$set": {"state": "awaiting_member_account_phone"}})
    await e.edit(strings['ADD_ACCOUNT_PROMPT'], buttons=[[Button.request_phone("Share Phone Number", resize=True, single_use=True)], [Button.inline("Cancel", '{"action":"members_adding_menu"}')]], parse_mode='html')

async def display_member_accounts(e, uid):
    owner_data = users_db.find_one({"chat_id": uid})
    accounts = owner_data.get('user_accounts', [])
    if not accounts:
        return await e.edit(strings['NO_ACCOUNTS_FOR_ADDING'], buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='html')

    text = strings['MY_ACCOUNTS_HEADER']
    buttons = []
    for account in accounts:
        account_id = account.get('account_id', 'N/A')
        phone_number = account.get('phone_number', 'N/A')
        status = strings['ACCOUNT_STATUS_INACTIVE']
        
        if account.get('logged_in'):
            status = strings['ACCOUNT_STATUS_HEALTHY']
            if account.get('is_banned_for_adding'):
                status = strings['ACCOUNT_STATUS_SUSPENDED'].format(reason="Banned")
            elif account.get('flood_wait_until', 0) > time.time():
                remaining_time = int(account['flood_wait_until'] - time.time())
                status = strings['ACCOUNT_STATUS_FLOODED'].format(until_time=fd(remaining_time))
            elif account.get('soft_error_count', 0) >= config.SOFT_ADD_LIMIT_ERRORS:
                status = strings['ACCOUNT_STATUS_SUSPENDED'].format(reason="Too many errors")
        else:
            status = strings['ACCOUNT_STATUS_INVALID']

        daily_adds = account.get('daily_adds_count', 0)
        soft_errors = account.get('soft_error_count', 0)
        
        text += strings['ACCOUNT_STATUS_ENTRY'].format(
            phone_number=phone_number,
            account_id=account_id,
            status=status,
            daily_adds=daily_adds,
            limit=config.MAX_DAILY_ADDS_PER_ACCOUNT,
            soft_errors=soft_errors,
            soft_limit=config.SOFT_ADD_LIMIT_ERRORS
        )
        buttons.append([Button.inline(f"Account {phone_number}", f'{{"action":"member_account_details","account_id":{account_id}}}')])
    
    buttons.append([Button.inline("« Back", '{"action":"members_adding_menu"}')])
    await e.edit(text, buttons=buttons, parse_mode='html')

async def send_member_account_details(e, uid, account_id):
    owner_data = users_db.find_one({"chat_id": uid})
    account_info = next((acc for acc in owner_data.get('user_accounts', []) if acc.get('account_id') == account_id), None)

    if not account_info:
        return await e.answer("Account not found.", alert=True)
    
    phone_number = account_info.get('phone_number', 'N/A')
    status = "Inactive"
    if account_info.get('logged_in'):
        status = "Active"
        if account_info.get('is_banned_for_adding'): status = "Banned"
        elif account_info.get('flood_wait_until', 0) > time.time(): status = f"Flood Wait (until {fd(account_info['flood_wait_until'] - time.time())})"
        elif account_info.get('soft_error_count', 0) >= config.SOFT_ADD_LIMIT_ERRORS: status = "Suspended (too many errors)"
    
    text = f"👤 **Account Details: {phone_number}**\n\n" \
           f"Account ID: <code>{account_id}</code>\n" \
           f"Status: {status}\n" \
           f"Logged In: {'Yes' if account_info.get('logged_in') else 'No'}\n" \
           f"Daily Adds: {account_info.get('daily_adds_count', 0)} / {config.MAX_DAILY_ADDS_PER_ACCOUNT}\n" \
           f"Soft Errors Today: {account_info.get('soft_error_count', 0)} / {config.SOFT_ADD_LIMIT_ERRORS}\n" \
           f"Last Login: {datetime.datetime.fromtimestamp(account_info['last_login_time']).strftime('%Y-%m-%d %H:%M:%S UTC') if account_info.get('last_login_time') else 'N/A'}\n" \
           f"Last Error: {account_info.get('error_type', 'None')}\n"
           
    buttons = [
        [Button.inline("Re-login Account", f'{{"action":"relogin_member_account","account_id":{account_id}}}')],
        [Button.inline("Toggle Ban Status", f'{{"action":"toggle_member_account_ban","account_id":{account_id}}}')],
        [Button.inline("Delete Account", f'{{"action":"confirm_delete_member_account","account_id":{account_id}}}')],
        [Button.inline("« Back", data='{"action":"manage_member_accounts"}')]
    ]
    await e.edit(text, buttons=buttons, parse_mode='html')

async def handle_delete_member_account(e, uid, account_id):
    # Ensure no active task is using this account. If it is, stop that task.
    owner_data = users_db.find_one({"chat_id": uid})
    tasks_using_account = [t for t in owner_data.get('adding_tasks', []) if account_id in t.get('assigned_accounts', []) and t.get('is_active')]
    for task in tasks_using_account:
        await members_adder.pause_adding_task(task['task_id'])
        await bot.send_message(uid, f"Task {task['task_id']} was using account <code>{account_id}</code> and has been paused.", parse_mode='html')

    # Remove account from all tasks it's assigned to
    users_db.update_many(
        {"chat_id": uid, "adding_tasks.assigned_accounts": account_id},
        {"$pull": {"adding_tasks.$.assigned_accounts": account_id}}
    )
    # Now remove the account itself
    users_db.update_one(
        {"chat_id": uid},
        {"$pull": {"user_accounts": {"account_id": account_id}}}
    )

    if account_id in members_adder.USER_CLIENTS:
        client = members_adder.USER_CLIENTS.pop(account_id)
        if client.is_connected():
            await client.disconnect()

    await e.edit(f"✅ Account <code>{account_id}</code> deleted successfully.", buttons=[[Button.inline("« Back", '{"action":"manage_member_accounts"}')]], parse_mode='html')

async def send_create_adding_task_menu(e, uid):
    owner_data = users_db.find_one({"chat_id": uid})
    existing_tasks = owner_data.get('adding_tasks', [])
    next_task_id = 1
    if existing_tasks:
        max_task_id = max(task.get('task_id', 0) for task in existing_tasks)
        next_task_id = max_task_id + 1

    new_task = {
        "task_id": next_task_id,
        "is_active": False,
        "status": "draft",
        "source_chat_id": None,
        "target_chat_ids": [],
        "assigned_accounts": [],
        "current_member_index": 0,
        "added_members_count": 0,
        "last_progress_message_id": None
    }
    users_db.update_one({"chat_id": uid}, {"$push": {"adding_tasks": new_task}})
    
    await send_adding_task_details_menu(e, uid, next_task_id)

async def send_manage_adding_tasks_menu(e, uid):
    owner_data = users_db.find_one({"chat_id": uid})
    tasks = owner_data.get('adding_tasks', [])
    
    if not tasks:
        text = "You have no adding tasks yet. Use '➕ Create Task' to add one."
        buttons = [[Button.inline("« Back", data='{"action":"members_adding_menu"}')]]
        return await e.edit(text, buttons=buttons, parse_mode='html')

    text = strings['MANAGE_TASKS_HEADER']
    buttons = []
    for task in tasks:
        task_id = task.get('task_id', 'N/A')
        status = task.get('status', 'draft')
        
        status_text = strings[f'TASK_STATUS_{status.upper()}']
        
        source_chat_title = "Not Set"
        if task.get('source_chat_id'):
            try: source_chat_title = await members_adder.get_chat_title(bot, task['source_chat_id'])
            except: pass
        
        target_chat_titles = []
        for chat_id in task.get('target_chat_ids', []):
            try: target_chat_titles.append(await members_adder.get_chat_title(bot, chat_id))
            except: pass
        target_chat_info = ", ".join(target_chat_titles) if target_chat_titles else "Not Set"

        num_accounts = len(task.get('assigned_accounts', []))

        text += strings['TASK_ENTRY_INFO'].format(
            task_id=task_id,
            status=status_text,
            source_chat_title=source_chat_title,
            target_chat_titles=target_chat_info,
            num_accounts=num_accounts
        ) + "\n"
        buttons.append([Button.inline(f"Task {task_id} - {status_text}", f'{{"action":"m_add_task_menu","task_id":{task_id}}}')])

    buttons.append([Button.inline("« Back", data='{"action":"members_adding_menu"}')])
    await e.edit(text, buttons=buttons, parse_mode='html')

async def send_adding_task_details_menu(e, uid, task_id):
    owner_data = users_db.find_one({"chat_id": uid})
    task = next((t for t in owner_data.get('adding_tasks', []) if t.get('task_id') == task_id), None)
    if not task: return await e.answer("Task not found.", alert=True)

    status_text = strings[f'TASK_STATUS_{task.get("status", "draft").upper()}']

    source_chat_info = "Not Set"
    if task.get('source_chat_id'):
        try: source_chat_info = await members_adder.get_chat_title(bot, task['source_chat_id'])
        except: source_chat_info = f"ID: <code>{task['source_chat_id']}</code>"
    
    target_chat_titles = []
    for chat_id in task.get('target_chat_ids', []):
        try: target_chat_titles.append(await members_adder.get_chat_title(bot, chat_id))
        except: target_chat_titles.append(f"ID: <code>{chat_id}</code>")
    target_chat_info = ", ".join(target_chat_titles) if target_chat_titles else "Not Set"

    assigned_accounts_info = []
    for acc_id in task.get('assigned_accounts', []):
        acc_doc_cursor = users_db.find({"chat_id": uid, "user_accounts.account_id": acc_id})
        acc_doc = next(acc_doc_cursor, None) # Get the owner document
        if acc_doc:
            # Now find the specific account within its user_accounts array
            acc_info = next((ua for ua in acc_doc['user_accounts'] if ua['account_id'] == acc_id), None)
            if acc_info:
                assigned_accounts_info.append(f"<code>{acc_info['phone_number']}</code>")
    assigned_accounts_display = ", ".join(assigned_accounts_info) if assigned_accounts_info else "None"

    total_added_members = task.get('added_members_count', 0)

    text = strings['TASK_DETAILS_HEADER'].format(
        task_id=task_id,
        status=status_text,
        source_chat_info=source_chat_info,
        target_chat_info=target_chat_info,
        assigned_accounts_info=assigned_accounts_display,
        total_added=total_added_members
    )

    buttons = [
        [Button.inline("📤 Set Source Chat", f'm_add_set|from|{task_id}|1')],
        [Button.inline("📥 Set Target Chat(s)", f'm_add_set|to|{task_id}|1')],
        [Button.inline("👥 Assign Accounts", f'{{"action":"assign_accounts_to_task","task_id":{task_id}}}')]
    ]
    
    if task.get('status') == 'active':
        buttons.append([Button.inline("⏸️ Pause Task", f'{{"action":"pause_adding_task","task_id":{task_id}}}')])
    elif task.get('status') == 'paused' or task.get('status') == 'draft' or task.get('status') == 'completed':
        # Only allow starting if source, target, and accounts are set
        if task.get('source_chat_id') and task.get('target_chat_ids') and task.get('assigned_accounts'):
            buttons.append([Button.inline("▶️ Start Task", f'{{"action":"start_adding_task","task_id":{task_id}}}')])
    
    buttons.append([Button.inline("🗑️ Delete Task", f'{{"action":"confirm_delete_adding_task","task_id":{task_id}}}')])
    buttons.append([Button.inline("« Back", data='{"action":"manage_adding_tasks"}')])
    
    await e.edit(text, buttons=buttons, parse_mode='html')

async def send_assign_accounts_menu(e, uid, task_id):
    owner_data = users_db.find_one({"chat_id": uid})
    all_accounts = owner_data.get('user_accounts', [])
    current_task = next((t for t in owner_data.get('adding_tasks', []) if t.get('task_id') == task_id), None)

    if not current_task: return await e.answer("Task not found.", alert=True)
    if not all_accounts: return await e.answer(strings['NO_ACCOUNTS_FOR_ADDING'], alert=True)

    assigned_account_ids = current_task.get('assigned_accounts', [])

    text = strings['CHOOSE_ACCOUNTS_FOR_TASK'].format(task_id=task_id)
    buttons = []

    for account in all_accounts:
        acc_id = account.get('account_id')
        phone_number = account.get('phone_number')
        prefix = "✅ " if acc_id in assigned_account_ids else ""
        buttons.append([Button.inline(f"{prefix}{phone_number} (ID: {acc_id})", f'm_add_assign_acc|{task_id}|{acc_id}')])
    
    buttons.append([Button.inline("Done ✅", f'{{"action":"m_add_task_menu", "task_id":{task_id}}}')])
    buttons.append([Button.inline("« Back", f'{{"action":"m_add_task_menu", "task_id":{task_id}}}')])

    await e.edit(text, buttons=buttons, parse_mode='html')


# --- EVENT HANDLERS ---
@bot.on(events.NewMessage(pattern=r"/start", func=lambda e: e.is_private))
async def start_handler(e):
    s=await e.get_sender()
    if not users_db.find_one({"chat_id":s.id}):
        users_db.insert_one({
            "chat_id":s.id, "fn":s.first_name, "un":s.username,
            "start_time":datetime.datetime.now(timezone.utc),
            "logged_in": False, "state": None,
            "is_banned_from_dl": False, # Keep for ban system if needed, even if DL is gone
            "user_accounts": [], # Array to store user accounts for adding members
            "adding_tasks": []   # Array to store member adding tasks
        })
    st=strings['START_TEXT'].format(user_firstname=s.first_name)
    btns=[
        [Button.inline("Help 💡", data='{"action":"help"}'), Button.inline("Commands 📋", data='{"action":"commands"}')],
        [Button.inline("Tutorial 🎬", data='{"action":"show_tutorial"}'), Button.url("Updates Channel 📢",url=config.UPDATES_CHANNEL_URL)]
    ]
    await e.respond(file=config.START_IMAGE_URL, message=st, buttons=btns, link_preview=False, parse_mode='html')

@bot.on(events.NewMessage(pattern=r"/help", func=lambda e: e.is_private))
async def help_command_handler(e):
    await e.respond(strings['HELP_TEXT_FEATURES'],
                     buttons=[[Button.inline("« Back to Main", data='{"action":"main_menu"}')]],
                     parse_mode='html')

@bot.on(events.NewMessage(pattern=r"/commands", func=lambda e: e.is_private))
async def commands_command_handler(e):
    await e.respond(strings['COMMANDS_TEXT'],
                     buttons=[[Button.inline("« Back to Main", data='{"action":"main_menu"}')]],
                     parse_mode='html')

@bot.on(events.NewMessage(pattern=r"/settings", func=lambda e: e.is_private))
async def settings_handler(e):
    if not get(users_db.find_one({"chat_id":e.sender_id}),'logged_in'):
        await e.respond(strings.get('need_login'), parse_mode='html')
    else:
        await send_members_adding_menu(e) # Directly send to member adding menu

@bot.on(events.NewMessage(pattern=r"^(?:https?://t\.me/(c/)?(\w+)/(\d+)|(-?\d+)\.(\d+))$",func=lambda e:e.is_private))
async def link_handler(e):
    if not await check_fsub(e):return
    uid=e.sender_id
    d=users_db.find_one({"chat_id":uid})
    if not d: await e.respond("Could not find your user data. Please /start again.", parse_mode='html'); return

    # Check if user is banned from using the bot entirely
    if get(d, 'is_banned_from_dl', False): # Renamed for broader use, but same field
        await e.respond(strings.get('USER_BANNED_MESSAGE', '🚫 You are banned from using this bot.'))
        return
    
    # If a link is sent, it means the user might be trying to initiate a process
    # that requires owner's login, for instance, getting chat IDs.
    # For now, let's just inform them that this bot is for member adding.
    await e.respond("This bot is dedicated to member adding. If you need to set up a task, please use the /settings menu.")


@bot.on(events.CallbackQuery)
async def main_callback_handler(e):
    uid = e.sender_id
    raw_data = e.data.decode()
    d = users_db.find_one({"chat_id": uid})

    # --- Member Adding Specific Callbacks ---
    if raw_data.startswith("m_add_sc|"): # Member Adding Chat Selection
        try:
            parts = raw_data.split("|")
            _, chat_id, selection_type, task_id, page = parts
            chat_id = int(chat_id)
            task_id = int(task_id)
            page = int(page)

            if not d: return await e.answer("User data not found. Please /start again.", alert=True)
            
            current_task_doc = next((t for t in d.get('adding_tasks', []) if t.get('task_id') == task_id), None)
            if not current_task_doc: return await e.answer("Adding task not found.", alert=True)

            if selection_type == 'from':
                users_db.update_one(
                    {"chat_id": uid, "adding_tasks.task_id": task_id},
                    {"$set": {"adding_tasks.$.source_chat_id": chat_id}}
                )
                popup_text = strings.get('TASK_SOURCE_SET', '✅ Source chat for task {task_id} set.').format(task_id=task_id, chat_title=await members_adder.get_chat_title(bot, chat_id))
                await e.answer(strip_html(popup_text), alert=True)
                await send_adding_task_details_menu(e, uid, task_id)
            elif selection_type == 'to':
                target_chats = current_task_doc.get('target_chat_ids', [])
                if chat_id in target_chats:
                    target_chats.remove(chat_id)
                    popup_text = strings.get('TASK_TARGET_UNSET', '☑️ Target chat for task {task_id} removed.').format(task_id=task_id, chat_title=await members_adder.get_chat_title(bot, chat_id))
                elif len(target_chats) < 2: # Limit to 2 target chats for now
                    target_chats.append(chat_id)
                    popup_text = strings.get('TASK_TARGET_SET_MULTI', '✅ Target chat for task {task_id} added.').format(task_id=task_id, chat_title=await members_adder.get_chat_title(bot, chat_id))
                else:
                    popup_text = strings.get('AF_ERROR_TO_FULL', 'You can only select up to 2 chats.') # Reusing string
                    return await e.answer(strip_html(popup_text), alert=True)
                
                users_db.update_one(
                    {"chat_id": uid, "adding_tasks.task_id": task_id},
                    {"$set": {"adding_tasks.$.target_chat_ids": target_chats}}
                )
                await e.answer(strip_html(popup_text), alert=True)
                await send_chat_selection_menu(e, uid, 'to', task_id, page)
        except Exception as ex:
            LOGGER.error(f"Error processing compact callback 'm_add_sc': {ex}")
            await e.answer("An error occurred during chat selection.", alert=True)
        return

    elif raw_data.startswith("m_add_set|"): # Member Adding Set Chat
        try:
            _, selection_type, task_id, page = raw_data.split("|")
            task_id = int(task_id)
            page = int(page)
            await send_chat_selection_menu(e, uid, selection_type, task_id, page)
        except Exception as ex:
            LOGGER.error(f"Error processing compact callback 'm_add_set': {ex}")
            await e.answer("An error occurred.", alert=True)
        return
    
    elif raw_data.startswith("m_add_assign_acc|"): # Assign Account to Task
        try:
            _, task_id_str, account_id_str = raw_data.split("|")
            task_id = int(task_id_str)
            account_id = int(account_id_str)

            owner_data = users_db.find_one({"chat_id": uid})
            current_task = next((t for t in owner_data.get('adding_tasks', []) if t.get('task_id') == task_id), None)
            
            if not current_task: return await e.answer("Task not found.", alert=True)

            assigned_accounts = current_task.get('assigned_accounts', [])
            
            if account_id in assigned_accounts:
                assigned_accounts.remove(account_id)
                popup_text = strings['ACCOUNT_UNASSIGNED_FROM_TASK'].format(account_id=account_id, task_id=task_id)
            else:
                assigned_accounts.append(account_id)
                popup_text = strings['ACCOUNT_ASSIGNED_TO_TASK'].format(account_id=account_id, task_id=task_id)

            users_db.update_one(
                {"chat_id": uid, "adding_tasks.task_id": task_id},
                {"$set": {"adding_tasks.$.assigned_accounts": assigned_accounts}}
            )
            await e.answer(strip_html(popup_text), alert=True)
            await send_assign_accounts_menu(e, uid, task_id)
        except Exception as ex:
            LOGGER.error(f"Error processing 'm_add_assign_acc' callback: {ex}")
            await e.answer("An error occurred.", alert=True)
        return

    if raw_data == 'noop' or raw_data == '{"action":"noop"}':
        return await e.answer()

    try:
        j = json.loads(raw_data)
        action = j.get('action')
    except (json.JSONDecodeError, AttributeError):
        j = {}
        action = None

    if action:
        if not d and action not in ["main_menu", "help", "commands", "retry_fsub", "show_tutorial", "ban_dl"]:
            await e.answer("User data not found. Please /start again.", alert=True)
            return

        if action == "ban_dl": # This action is now for banning from the entire bot.
            if e.sender_id != config.OWNER_ID:
                return await e.answer("You are not authorized to do this.", alert=True)
            user_id_to_ban = j.get('user_id')
            if not user_id_to_ban:
                return await e.answer("Error: User ID not found.", alert=True)
            
            users_db.update_one(
                {"chat_id": user_id_to_ban},
                {"$set": {"is_banned_from_dl": True}}, # Renamed for broader use.
                upsert=True
            )
            try:
                ban_message = strings.get('BAN_NOTIFICATION_MESSAGE', "You have been banned from using this bot.")
                await bot.send_message(user_id_to_ban, ban_message, parse_mode='html')
            except Exception as ex:
                LOGGER.error(f"Failed to send ban notification to {user_id_to_ban}: {ex}")

            await e.edit("✅ User has been **banned** and notified.")
            return

        if action == "main_menu":
            s=await e.get_sender();st=strings['START_TEXT'].format(user_firstname=s.first_name)
            btns=[
                [Button.inline("Help 💡", data='{"action":"help"}'), Button.inline("Commands 📋", data='{"action":"commands"}')],
                [Button.inline("Tutorial 🎬", data='{"action":"show_tutorial"}'), Button.url("Updates Channel 📢",url=config.UPDATES_CHANNEL_URL)]
            ]
            try: await e.delete()
            except: pass
            await e.respond(file=config.START_IMAGE_URL, message=st, buttons=btns, link_preview=False, parse_mode='html')
        elif action=="help":
            try: await e.delete()
            except: pass
            await e.respond(strings['HELP_TEXT_FEATURES'], buttons=[[Button.inline("« Back",data='{"action":"main_menu"}')]], parse_mode='html')
        elif action=="commands":
            try: await e.delete()
            except: pass
            await e.respond(strings['COMMANDS_TEXT'], buttons=[[Button.inline("« Back",data='{"action":"main_menu"}')]], parse_mode='html')
        elif action == "show_tutorial":
            if uid == config.OWNER_ID:
                users_db.update_one({"chat_id": uid}, {"$set": {"state": "awaiting_tutorial_video"}})
                await e.edit(strings['TUTORIAL_PROMPT_OWNER'], parse_mode='html')
            else:
                tutorial_doc = bot_settings_db.find_one({'setting': 'tutorial'})
                if tutorial_doc and 'message_id' in tutorial_doc:
                    msg_id = tutorial_doc['message_id']
                    try:
                        sent_video = await bot.forward_messages(e.chat_id, from_peer=config.OWNER_ID, message_ids=msg_id)
                        if sent_video:
                            asyncio.create_task(delete_after(sent_video[0], 600))
                        await e.answer()
                    except Exception as ex:
                        LOGGER.error(f"Tutorial forward failed: {ex}")
                        await e.answer(strip_html("Could not fetch the tutorial video."), alert=True)
                else:
                    popup_text = strings.get('TUTORIAL_NOT_SET_MSG', "The tutorial has not been set by the owner yet.")
                    await e.answer(strip_html(popup_text), alert=True)
        elif action == "settings":
            await e.delete()
            await send_members_adding_menu(e, uid) # Directly to members adding menu
        elif action == "user_broadcast":
            users_db.update_one({"chat_id": uid}, {"$set": {"state": "awaiting_broadcast_message"}})
            await e.edit(strings['BROADCAST_MENU_TEXT'], buttons=[[Button.inline("🛑 Cancel Broadcast", '{"action":"cancel_broadcast"}')]], parse_mode='html')
        elif action == "cancel_broadcast":
            users_db.update_one({"chat_id": uid}, {"$set": {"state": None}})
            await e.edit(strings['BROADCAST_CANCELLED'], buttons=[[Button.inline("« Back to Settings", '{"action":"settings"}')]], parse_mode='html')
        elif action=="retry_fsub":
            await e.delete()
            if await check_fsub(e):await e.respond("Thanks for joining! Please use /settings to manage member adding.")
        elif action=="cancel_current"or action=="cancel_all": # These might not be used now, but kept for robustness
            task=ACTIVE_TASKS.get(uid)
            if not task:await e.answer("No active task to cancel.",alert=True);return
            if action=="cancel_all":
                if USER_QUEUES.get(uid):USER_QUEUES[uid].clear()
            task.cancel();await e.answer("Cancellation signal sent.")
        elif action.startswith("yes_")or action.startswith("no_"):
            ctx=action.split('_',1)[1];l=json.loads(get(d,'login','{}'))
            if ctx=="logout":
                if action.startswith("yes_"):
                    # We don't have AR/AF workers anymore, just disconnect owner's session
                    users_db.update_one({'chat_id':uid},{'$set':{'logged_in':False,'session':None,'login':'{}'}})
                    await e.edit(strings['logged_out'], parse_mode='html')
                else:await e.edit(strings['not_logged_out'], parse_mode='html')
            elif ctx in["code","pass"]:
                numpad=[[Button.inline(str(i),f'{{"press":{i}}}')for i in range(j,j+3)]for j in range(1,10,3)];numpad.append([Button.inline("Clear All",'{"press":"clear_all"}'),Button.inline("0",'{"press":0}'),Button.inline("⌫",'{"press":"clear"}')])
                ip=ctx=="pass";ko="pass_ok"if ip else"code_ok"
                if action.startswith("yes_"):l[ko]=True;users_db.update_one({'chat_id':uid},{'$set':{'login':json.dumps(l)}});await sign_in(e)
                else:
                    l[ko]=False
                    if ip:users_db.update_one({'chat_id':uid},{'$set':{'login':json.dumps(l),'password':''}});await e.edit(strings['ask_pass'], parse_mode='html')
                    else:l['code']='';users_db.update_one({'chat_id':uid},{'$set':{'login':json.dumps(l)}});await e.edit(strings['ask_code']+'\n<code>'+l.get('code','')+'</code>',buttons=numpad, parse_mode='html', link_preview=False)
        
        # --- Member Adding Callbacks ---
        elif action == "members_adding_menu":
            await send_members_adding_menu(e, uid)
        elif action == "add_member_account":
            await handle_add_member_account(e)
        elif action == "manage_member_accounts":
            await display_member_accounts(e, uid)
        elif action == "member_account_details":
            account_id = j.get('account_id')
            await send_member_account_details(e, uid, account_id)
        elif action == "confirm_delete_member_account":
            account_id = j.get('account_id')
            await e.edit(f"Are you sure you want to delete account <code>{account_id}</code>? All tasks associated with it will be affected.", buttons=yesno(f"delete_member_account_{account_id}"), parse_mode='html')
        elif action.startswith("yes_delete_member_account_"):
            account_id = int(action.split('_')[-1])
            await handle_delete_member_account(e, uid, account_id)
        elif action.startswith("no_delete_member_account_"):
            account_id = int(action.split('_')[-1])
            await send_member_account_details(e, uid, account_id)
        elif action == "relogin_member_account":
            account_id = j.get('account_id')
            # Trigger re-login flow for specific account
            users_db.update_one({"chat_id": uid}, {"$set": {"state": f"awaiting_member_account_relogin_phone_{account_id}"}})
            await e.edit(f"Please forward your phone number associated with account <code>{account_id}</code> to re-login.", buttons=[[Button.request_phone("Share Phone Number", resize=True, single_use=True)], [Button.inline("Cancel", f'{{"action":"member_account_details","account_id":{account_id}}}')]], parse_mode='html')
        elif action == "toggle_member_account_ban":
            account_id = j.get('account_id')
            owner_data = users_db.find_one({"chat_id": uid})
            account_info = next((acc for acc in owner_data.get('user_accounts', []) if acc.get('account_id') == account_id), None)
            if account_info:
                new_ban_status = not account_info.get('is_banned_for_adding', False)
                users_db.update_one(
                    {"chat_id": uid, "user_accounts.account_id": account_id},
                    {"$set": {"user_accounts.$.is_banned_for_adding": new_ban_status}}
                )
                await e.answer(f"Account <code>{account_id}</code> ban status toggled to {'Banned' if new_ban_status else 'Unbanned'}.", alert=True)
                await send_member_account_details(e, uid, account_id)
            else:
                await e.answer("Account not found.", alert=True)
        elif action == "create_adding_task":
            await send_create_adding_task_menu(e, uid)
        elif action == "manage_adding_tasks":
            await send_manage_adding_tasks_menu(e, uid)
        elif action == "m_add_task_menu":
            task_id = j.get('task_id')
            await send_adding_task_details_menu(e, uid, task_id)
        elif action == "start_adding_task":
            task_id = j.get('task_id')
            owner_data = users_db.find_one({"chat_id": uid})
            task_to_start = next((t for t in owner_data.get('adding_tasks', []) if t.get('task_id') == task_id), None)
            
            if not task_to_start.get('source_chat_id'):
                return await e.answer(strings['TASK_NO_SOURCE_SELECTED'], alert=True)
            if not task_to_start.get('target_chat_ids'):
                return await e.answer(strings['TASK_NO_TARGET_SELECTED'], alert=True)
            if not task_to_start.get('assigned_accounts'):
                return await e.answer(strings['TASK_NO_ACCOUNTS_ASSIGNED'], alert=True)

            started = await members_adder.start_adding_task(uid, task_id)
            if started:
                await e.answer(strings['TASK_STARTING'].format(task_id=task_id), alert=True)
                await send_adding_task_details_menu(e, uid, task_id)
            else:
                await e.answer("Failed to start task. Check account statuses.", alert=True)
        elif action == "pause_adding_task":
            task_id = j.get('task_id')
            paused = await members_adder.pause_adding_task(task_id)
            if paused:
                await e.answer(strings['TASK_PAUSING'].format(task_id=task_id), alert=True)
                await send_adding_task_details_menu(e, uid, task_id)
            else:
                await e.answer("Failed to pause task. It might not be running.", alert=True)
        elif action == "confirm_delete_adding_task":
            task_id = j.get('task_id')
            await e.edit(strings['TASK_DELETE_CONFIRM'].format(task_id=task_id), buttons=yesno(f"delete_adding_task_{task_id}"), parse_mode='html')
        elif action.startswith("yes_delete_adding_task_"):
            task_id = int(action.split('_')[-1])
            await members_adder.pause_adding_task(task_id) # Ensure task is stopped
            users_db.update_one({"chat_id": uid}, {"$pull": {"adding_tasks": {"task_id": task_id}}})
            await e.edit(strings['TASK_DELETED'].format(task_id=task_id), buttons=[[Button.inline("« Back", '{"action":"manage_adding_tasks"}')]], parse_mode='html')
        elif action.startswith("no_delete_adding_task_"):
            task_id = int(action.split('_')[-1])
            await send_adding_task_details_menu(e, uid, task_id)
        
        return await e.answer() # Ensure callback query is answered
        
    try:
        j = json.loads(raw_data)
        pr=j.get('press')
        if not d: return await e.answer("User data not found. Please /start again.", alert=True)
        
        state = get(d, 'state')
        
        if state and state.startswith("awaiting_member_account_code_"):
            # OTP for new member adding account
            account_id = int(state.split('_')[-1])
            account_info_doc = users_db.find_one({"chat_id": uid, "user_accounts.account_id": account_id})
            if not account_info_doc: return await e.answer("Account not found for OTP processing.", alert=True)
            account_info = next((acc for acc in account_info_doc.get('user_accounts', []) if acc.get('account_id') == account_id), None)
            if not account_info or not account_info.get('temp_login_data'):
                return await e.answer("Invalid state for OTP. Please try adding account again.", alert=True)
            
            temp_login_data = account_info['temp_login_data']
            
            # This logic for numpad is specific to the "temp_login_data"
            code = temp_login_data.get('code', '')
            if isinstance(pr,int): code += str(pr)
            elif pr=="clear": code = code[:-1]
            elif pr=="clear_all": code = ''
            
            users_db.update_one({"chat_id": uid, "user_accounts.account_id": account_id}, {"$set": {"user_accounts.$.temp_login_data.code": code}})
            
            clen, code_ok = temp_login_data.get('clen'), temp_login_data.get('code_ok', False)
            numpad=[[Button.inline(str(i),f'{{"press":{i}}}')for i in range(j,j+3)]for j in range(1,10,3)];numpad.append([Button.inline("Clear All",'{"press":"clear_all"}'),Button.inline("0",'{"press":0}'),Button.inline("⌫",'{"press":"clear"}')])
            try:
                if clen and len(code)==clen and not code_ok:
                    users_db.update_one({"chat_id": uid, "user_accounts.account_id": account_id}, {"$set": {"user_accounts.$.temp_login_data.code_ok": True}})
                    await e.edit(f"{strings['ask_ok']}<code>{code}</code>",buttons=yesno('code'), parse_mode='html')
                else: await e.edit(strings['ask_code']+'\n<code>'+code+'</code>',buttons=numpad, parse_mode='html', link_preview=False)
            except MessageNotModifiedError: pass
            finally: await e.answer()

        elif state and state.startswith("awaiting_member_account_relogin_code_"):
            # OTP for re-logging member adding account
            account_id = int(state.split('_')[-1])
            account_info_doc = users_db.find_one({"chat_id": uid, "user_accounts.account_id": account_id})
            if not account_info_doc: return await e.answer("Account not found for OTP processing.", alert=True)
            account_info = next((acc for acc in account_info_doc.get('user_accounts', []) if acc.get('account_id') == account_id), None)
            if not account_info or not account_info.get('temp_login_data'):
                return await e.answer("Invalid state for OTP. Please try re-logging account again.", alert=True)
            
            temp_login_data = account_info['temp_login_data']
            code = temp_login_data.get('code', '')
            if isinstance(pr,int): code += str(pr)
            elif pr=="clear": code = code[:-1]
            elif pr=="clear_all": code = ''
            
            users_db.update_one({"chat_id": uid, "user_accounts.account_id": account_id}, {"$set": {"user_accounts.$.temp_login_data.code": code}})
            
            clen, code_ok = temp_login_data.get('clen'), temp_login_data.get('code_ok', False)
            numpad=[[Button.inline(str(i),f'{{"press":{i}}}')for i in range(j,j+3)]for j in range(1,10,3)];numpad.append([Button.inline("Clear All",'{"press":"clear_all"}'),Button.inline("0",'{"press":0}'),Button.inline("⌫",'{"press":"clear"}')])
            try:
                if clen and len(code)==clen and not code_ok:
                    users_db.update_one({"chat_id": uid, "user_accounts.account_id": account_id}, {"$set": {"user_accounts.$.temp_login_data.code_ok": True}})
                    await e.edit(f"{strings['ask_ok']}<code>{code}</code>",buttons=yesno('code'), parse_mode='html')
                else: await e.edit(strings['ask_code']+'\n<code>'+code+'</code>',buttons=numpad, parse_mode='html', link_preview=False)
            except MessageNotModifiedError: pass
            finally: await e.answer()

        else: # Default behavior for main bot login (if any)
            l=json.loads(get(d,'login','{}'))
            if isinstance(pr,int):l['code']=get(l,'code','')+str(pr)
            elif pr=="clear":l['code']=get(l,'code','')[:-1]
            elif pr=="clear_all":l['code']=''
            users_db.update_one({'_id':d['_id']},{'$set':{'login':json.dumps(l)}})
            code,clen,code_ok=get(l,'code',''),get(l,'clen'),get(l,'code_ok',False)
            numpad=[[Button.inline(str(i),f'{{"press":{i}}}')for i in range(j,j+3)]for j in range(1,10,3)];numpad.append([Button.inline("Clear All",'{"press":"clear_all"}'),Button.inline("0",'{"press":0}'),Button.inline("⌫",'{"press":"clear"}')])
            try:
                if clen and len(code)==clen and not code_ok: await e.edit(f"{strings['ask_ok']}<code>{code}</code>",buttons=yesno('code'), parse_mode='html')
                elif'code'in l: await e.edit(strings['ask_code']+'\n<code>'+code+'</code>',buttons=numpad, parse_mode='html', link_preview=False)
            except MessageNotModifiedError: pass
            finally: await e.answer()

    except (json.JSONDecodeError, KeyError):
        await e.answer()

@bot.on(events.NewMessage(pattern=r"/login",func=lambda e:e.is_private))
async def login_handler(e):
    if get(users_db.find_one({"chat_id":e.chat_id}),'logged_in'):await e.respond(strings['already_logged_in'], parse_mode='html')
    else:await e.respond(strings['ask_phone'],buttons=[Button.request_phone("✅ Click to Login ✅",resize=True,single_use=True)], parse_mode='html')

@bot.on(events.NewMessage(pattern=r"/logout",func=lambda e:e.is_private))
async def logout_handler(e):
    if not get(users_db.find_one({"chat_id":e.chat_id}),'logged_in'):await e.respond(strings.get('need_login'), parse_mode='html')
    else:await e.respond(strings['logout_sure'],buttons=yesno('logout'), parse_mode='html')

@bot.on(events.NewMessage(pattern=r"/addaccount", func=lambda e: e.is_private))
async def add_member_account_command_handler(e):
    await handle_add_member_account(e)

@bot.on(events.NewMessage(pattern=r"/myaccounts", func=lambda e: e.is_private))
async def my_member_accounts_command_handler(e):
    await display_member_accounts(e, e.sender_id)

@bot.on(events.NewMessage(pattern=r"/createtask", func=lambda e: e.is_private))
async def create_adding_task_command_handler(e):
    await send_create_adding_task_menu(e, e.sender_id)

@bot.on(events.NewMessage(pattern=r"/managetasks", func=lambda e: e.is_private))
async def manage_adding_tasks_command_handler(e):
    await send_manage_adding_tasks_menu(e, e.sender_id)

@bot.on(events.NewMessage(func=lambda e:e.is_private and e.contact))
async def contact_handler(e):
    uid = e.sender_id
    owner_data = users_db.find_one({"chat_id": uid})
    state = get(owner_data, 'state')

    if e.contact.user_id==e.chat_id: # This is the owner's own phone number for main bot login
        await handle_usr(e.contact,e)
    elif state and state.startswith("awaiting_member_account_relogin_phone_"):
        # Re-login an existing member adding account
        account_id_to_relogin = int(state.split('_')[-1])
        phone_number = e.contact.phone_number
        
        # Prepare for OTP request
        client = TelegramClient(StringSession(), config.API_ID, config.API_HASH, **device_info)
        try:
            await client.connect()
            code_request = await client.send_code_request(phone_number)
            
            # Store temporary login data for this specific account
            login_data = {
                'ph': phone_number,
                'phash': code_request.phone_code_hash,
                'sess': client.session.save(),
                'clen': code_request.type.length,
                'code_ok': False,
                'need_pass': False # Assume no password needed initially
            }
            users_db.update_one(
                {"chat_id": uid, "user_accounts.account_id": account_id_to_relogin},
                {"$set": {"state": f"awaiting_member_account_relogin_code_{account_id_to_relogin}", "user_accounts.$.temp_login_data": login_data}}
            )
            numpad=[[Button.inline(str(i),f'{{"press":{i}}}')for i in range(j,j+3)]for j in range(1,10,3)];numpad.append([Button.inline("Clear All",'{"press":"clear_all"}'),Button.inline("0",'{"press":0}'),Button.inline("⌫",'{"press":"clear"}')])
            await e.respond(strings['ask_code'], buttons=numpad, parse_mode='html', link_preview=False)
        except Exception as ex:
            LOGGER.error(f"Error during member account re-login phone submission: {ex}")
            await e.respond(f"Failed to re-login account: {ex}", buttons=[[Button.inline("« Back", f'{{"action":"member_account_details","account_id":{account_id_to_relogin}}}')]], parse_mode='html')
        finally:
            if client.is_connected(): await client.disconnect()
    elif state == "awaiting_member_account_phone":
        # Add a new member adding account
        phone_number = e.contact.phone_number
        
        # Check if this phone number is already added
        if any(acc.get('phone_number') == phone_number for acc in owner_data.get('user_accounts', [])):
            users_db.update_one({"chat_id": uid}, {"$set": {"state": None}}) # Clear state
            return await e.respond("This phone number is already added.", buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='html')

        client = TelegramClient(StringSession(), config.API_ID, config.API_HASH, **device_info)
        try:
            await client.connect()
            code_request = await client.send_code_request(phone_number)
            
            # Find a unique account_id
            existing_account_ids = [acc.get('account_id') for acc in owner_data.get('user_accounts', []) if acc.get('account_id')]
            new_account_id = 1
            if existing_account_ids:
                new_account_id = max(existing_account_ids) + 1

            new_account_entry = {
                "account_id": new_account_id,
                "phone_number": phone_number,
                "session_string": client.session.save(), # Temp session
                "logged_in": False,
                "last_login_time": None,
                "daily_adds_count": 0,
                "soft_error_count": 0,
                "last_add_date": None,
                "is_active_for_adding": False,
                "is_banned_for_adding": False,
                "flood_wait_until": 0,
                "error_type": None,
                "temp_login_data": { # Store login progress data temporarily
                    'phash': code_request.phone_code_hash,
                    'sess': client.session.save(),
                    'clen': code_request.type.length,
                    'code_ok': False,
                    'need_pass': False
                }
            }
            users_db.update_one({"chat_id": uid}, {"$push": {"user_accounts": new_account_entry}, "$set": {"state": f"awaiting_member_account_code_{new_account_id}"}})
            
            numpad=[[Button.inline(str(i),f'{{"press":{i}}}')for i in range(j,j+3)]for j in range(1,10,3)];numpad.append([Button.inline("Clear All",'{"press":"clear_all"}'),Button.inline("0",'{"press":0}'),Button.inline("⌫",'{"press":"clear"}')])
            await e.respond(strings['ask_code'], buttons=numpad, parse_mode='html', link_preview=False)

        except Exception as ex:
            LOGGER.error(f"Error during member account phone submission: {ex}")
            await e.respond(f"Failed to add account: {ex}", buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='html')
        finally:
            if client.is_connected(): await client.disconnect()
    else:
        await e.respond(strings['wrong_phone'], parse_mode='html')


@bot.on(events.NewMessage(func=lambda e: e.is_private and not e.text.startswith('/')))
async def private_message_handler(e):
    uid = e.sender_id
    d = users_db.find_one({"chat_id": uid})
    if not d: return
    
    state = get(d, 'state')

    if state == "awaiting_broadcast_message":
        users_db.update_one({"chat_id": uid}, {"$set": {"state": None}})
        asyncio.create_task(run_user_broadcast(uid, e.message))
    elif get(json.loads(get(d, 'login', '{}')), 'need_pass'):
        # This is for the main bot's owner login password
        users_db.update_one({'_id': d['_id']}, {'$set': {'password': e.text}})
        await e.delete()
        await e.respond(f"{strings['ask_ok']}'<code>********</code>'", buttons=yesno('pass'), parse_mode='html')
    elif state and state.startswith("awaiting_member_account_code_"):
        # Handling OTP for a new member adding account
        account_id = int(state.split('_')[-1])
        account_info_doc = users_db.find_one({"chat_id": uid, "user_accounts.account_id": account_id})
        if not account_info_doc: return await e.respond("Invalid state for OTP. Please try adding account again.", buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='html')
        account_info = next((acc for acc in account_info_doc.get('user_accounts', []) if acc.get('account_id') == account_id), None)
        if not account_info or not account_info.get('temp_login_data'):
            return await e.respond("Invalid state for OTP. Please try adding account again.", buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='html')
        
        temp_login_data = account_info['temp_login_data']
        phone_number = temp_login_data.get('ph')
        phone_code_hash = temp_login_data.get('phash')
        session_string_temp = temp_login_data.get('sess')
        otp_code = e.text.strip()

        client = TelegramClient(StringSession(session_string_temp), config.API_ID, config.API_HASH, **device_info)
        try:
            await client.connect()
            await client.sign_in(phone=phone_number, code=otp_code, phone_code_hash=phone_code_hash)
            
            # Login successful, update the account in DB
            users_db.update_one(
                {"chat_id": uid, "user_accounts.account_id": account_id},
                {"$set": {
                    "user_accounts.$.session_string": client.session.save(),
                    "user_accounts.$.logged_in": True,
                    "user_accounts.$.last_login_time": time.time(),
                    "user_accounts.$.is_active_for_adding": True, # Mark as active
                    "user_accounts.$.temp_login_data": {} # Clear temp data
                }, "$unset": {"state": 1}} # Clear owner's state
            )
            # Add to active user clients managed by members_adder
            members_adder.USER_CLIENTS[account_id] = client
            await e.respond(strings['ACCOUNT_ADDED_SUCCESS'].format(phone_number=phone_number, account_id=account_id), parse_mode='html')
            await send_members_adding_menu(e, uid)
        except SessionPasswordNeededError:
            users_db.update_one(
                {"chat_id": uid, "user_accounts.account_id": account_id},
                {"$set": {"state": f"awaiting_member_account_password_{account_id}", "user_accounts.$.temp_login_data.need_pass": True}}
            )
            await e.respond(strings['ask_pass'], parse_mode='html')
        except PhoneCodeInvalidError:
            await e.respond(strings['code_invalid'], parse_mode='html')
            numpad=[[Button.inline(str(i),f'{{"press":{i}}}')for i in range(j,j+3)]for j in range(1,10,3)];numpad.append([Button.inline("Clear All",'{"press":"clear_all"}'),Button.inline("0",'{"press":0}'),Button.inline("⌫",'{"press":"clear"}')])
            await e.respond(strings['ask_code'], buttons=numpad, parse_mode='html', link_preview=False) # Re-ask code
        except Exception as ex:
            LOGGER.error(f"Error during member account OTP submission: {ex}")
            users_db.update_one(
                {"chat_id": uid, "user_accounts.account_id": account_id},
                {"$set": {"user_accounts.$.logged_in": False, "user_accounts.$.is_active_for_adding": False, "user_accounts.$.temp_login_data": {}}},
                {"$unset": {"state": 1}}
            )
            await e.respond(strings['ACCOUNT_LOGIN_FAILED'].format(error_message=ex), buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='html')
        finally:
            if not (account_info and account_info.get('logged_in')): # Only disconnect client if not successfully logged in
                if client.is_connected(): await client.disconnect()
    elif state and state.startswith("awaiting_member_account_password_"):
        # Handling 2FA password for a new or re-logging member adding account
        account_id = int(state.split('_')[-1])
        account_info_doc = users_db.find_one({"chat_id": uid, "user_accounts.account_id": account_id})
        if not account_info_doc: return await e.respond("Invalid state for password. Please try adding account again.", buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='html')
        account_info = next((acc for acc in account_info_doc.get('user_accounts', []) if acc.get('account_id') == account_id), None)
        if not account_info or not account_info.get('temp_login_data'):
            return await e.respond("Invalid state for password. Please try adding account again.", buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='html')
        
        temp_login_data = account_info['temp_login_data']
        session_string_temp = temp_login_data.get('sess')
        password = e.text.strip()

        client = TelegramClient(StringSession(session_string_temp), config.API_ID, config.API_HASH, **device_info)
        try:
            await client.connect()
            await client.sign_in(password=password)

            # Login successful, update the account in DB
            users_db.update_one(
                {"chat_id": uid, "user_accounts.account_id": account_id},
                {"$set": {
                    "user_accounts.$.session_string": client.session.save(),
                    "user_accounts.$.logged_in": True,
                    "user_accounts.$.last_login_time": time.time(),
                    "user_accounts.$.is_active_for_adding": True, # Mark as active
                    "user_accounts.$.temp_login_data": {} # Clear temp data
                }, "$unset": {"state": 1}} # Clear owner's state
            )
            members_adder.USER_CLIENTS[account_id] = client
            await e.respond(strings['ACCOUNT_ADDED_SUCCESS'].format(phone_number=account_info['phone_number'], account_id=account_id), parse_mode='html')
            await send_members_adding_menu(e, uid)
        except PasswordHashInvalidError:
            await e.respond(strings['pass_invalid'], parse_mode='html')
            await e.respond(strings['ask_pass'], parse_mode='html') # Re-ask password
        except Exception as ex:
            LOGGER.error(f"Error during member account 2FA password submission: {ex}")
            users_db.update_one(
                {"chat_id": uid, "user_accounts.account_id": account_id},
                {"$set": {"user_accounts.$.logged_in": False, "user_accounts.$.is_active_for_adding": False, "user_accounts.$.temp_login_data": {}}},
                {"$unset": {"state": 1}}
            )
            await e.respond(strings['ACCOUNT_LOGIN_FAILED'].format(error_message=ex), buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='html')
        finally:
            if not (account_info and account_info.get('logged_in')):
                if client.is_connected(): await client.disconnect()


@bot.on(events.NewMessage(func=lambda e: e.is_private and e.media))
async def private_media_handler(e):
    uid = e.sender_id
    d = users_db.find_one({"chat_id": uid})
    if not d: return

    state = get(d, 'state')
    if uid == config.OWNER_ID and state == "awaiting_tutorial_video" and e.video:
        users_db.update_one({"chat_id": uid}, {"$set": {"state": None}})
        bot_settings_db.update_one(
            {'setting': 'tutorial'},
            {'$set': {'message_id': e.message.id}},
            upsert=True
        )
        await e.respond(strings['TUTORIAL_SET_SUCCESS'], parse_mode='html')
    elif state == "awaiting_broadcast_message":
        users_db.update_one({"chat_id": uid}, {"$set": {"state": None}})
        asyncio.create_task(run_user_broadcast(uid, e.message))

@bot.on(events.NewMessage(pattern=r"/empty", func=lambda e: e.is_private))
async def empty_handler(e):
    uid = e.sender_id
    d = users_db.find_one({"chat_id": uid})
    if not d: return
    state = get(d, 'state')
    if uid == config.OWNER_ID and state == "awaiting_tutorial_video":
        users_db.update_one({"chat_id": uid}, {"$set": {"state": None}})
        bot_settings_db.delete_one({'setting': 'tutorial'})
        await e.respond(strings['TUTORIAL_REMOVED_SUCCESS'], parse_mode='html')
    else:
        await e.respond("This command is not available in the current context.") # Placeholder

@bot.on(events.NewMessage(pattern="/stats",from_users=config.OWNER_ID))
async def stats_handler(e):
    total_bot_users=users_db.count_documents({});
    logged_in_bot_users=users_db.count_documents({"logged_in":True});
    
    # Member adding specific stats
    total_member_accounts = sum(len(doc.get('user_accounts', [])) for doc in users_db.find({}))
    active_adding_tasks_count = len(members_adder.ACTIVE_ADDING_TASKS)
    
    st=(f"<b>Bot Statistics</b>\n\n"
        f"👤 <b>Total Bot Users:</b> <code>{total_bot_users}</code>\n"
        f"✅ <b>Logged-in Bot Users:</b> <code>{logged_in_bot_users}</code>\n"
        f"👥 <b>Total Member Adding Accounts:</b> <code>{total_member_accounts}</code>\n"
        f"⚙️ <b>Active Adding Tasks:</b> <code>{active_adding_tasks_count}</code>"
    )
    await e.respond(st, parse_mode='html')

@bot.on(events.NewMessage(pattern="/broadcast",from_users=config.OWNER_ID))
async def owner_broadcast_handler(e):
    r=await e.get_reply_message()
    if not r:await e.respond("Please reply to a message to broadcast.");return
    
    # Only broadcast to bot owner's directly connected chats / contacts from their own session
    # The previous implementation broadcasted to all users_db entries, which might include *all* bot users.
    # If the intention is to broadcast *from the owner's logged-in session* to *their* contacts,
    # the `run_user_broadcast` function is appropriate, initiated by setting state.
    
    users_db.update_one({"chat_id": e.sender_id}, {"$set": {"state": "awaiting_broadcast_message"}})
    await e.respond(strings['BROADCAST_MENU_TEXT'], buttons=[[Button.inline("🛑 Cancel Broadcast", '{"action":"cancel_broadcast"}')]], parse_mode='html')
    # The actual broadcast happens when the message is received by private_message_handler

async def main():
    global BOT_USERNAME
    try:
        await bot.start(bot_token=config.BOT_TOKEN)
        me=await bot.get_me();BOT_USERNAME=me.username
        LOGGER.info(f"Bot started as @{BOT_USERNAME}. Initializing...")
        
        # Initialize Member Adding Clients and Tasks
        all_owners = users_db.find({})
        member_account_count = 0
        active_adding_tasks_count = 0
        for owner_doc in all_owners:
            owner_id = owner_doc.get('chat_id')
            # Connect all user clients for adding
            for acc in owner_doc.get('user_accounts', []):
                acc_id = acc.get('account_id')
                if acc_id and acc.get('logged_in'):
                    client = await members_adder.get_user_client(acc_id)
                    if client:
                        LOGGER.info(f"Loaded and connected member adding client {acc_id} for owner {owner_id}.")
                        member_account_count += 1
            
            # Restart active adding tasks
            for task in owner_doc.get('adding_tasks', []):
                if task.get('is_active'):
                    LOGGER.info(f"Restarting active adding task {task.get('task_id')} for owner {owner_id}")
                    # Set status to paused first to ensure clean restart
                    users_db.update_one(
                        {"chat_id": owner_id, "adding_tasks.task_id": task.get('task_id')},
                        {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}}
                    )
                    await members_adder.start_adding_task(owner_id, task.get('task_id'))
                    active_adding_tasks_count += 1
        
        LOGGER.info(f"Member Adding Initialization complete. Loaded {member_account_count} accounts and restarted {active_adding_tasks_count} tasks.")

        await bot.run_until_disconnected()
    except Exception as e:LOGGER.critical(f"BOT CRITICAL ERROR: {e}")
    finally:
        LOGGER.info("Stopping bot and all user workers...")
        # Stop all member adding clients
        for client in list(members_adder.USER_CLIENTS.values()):
            if client.is_connected():
                await client.disconnect()
        # Cancel all active adding tasks
        for task_id in list(members_adder.ACTIVE_ADDING_TASKS.keys()):
            if task_id in members_adder.ACTIVE_ADDING_TASKS:
                members_adder.ACTIVE_ADDING_TASKS[task_id].cancel()
                del members_adder.ACTIVE_ADDING_TASKS[task_id]

        LOGGER.info("All processes stopped.")

if __name__=="__main__":
    try:asyncio.run(main())
    except KeyboardInterrupt:LOGGER.info("Bot stopped manually.")
