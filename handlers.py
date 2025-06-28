from telethon.tl.custom.button import Button
import datetime
import time
from telethon.errors import UserNotParticipantError, MessageNotModifiedError
import random

from config import config
import utils
import db
from strings import strings

bot_client = None

def set_bot_client(client):
    global bot_client
    bot_client = client

def yesno(c):
    return [[Button.inline("Yes", f'yes_{c}')], [Button.inline("No", f'no_{c}')]]

async def check_fsub(e):
    if not config.FORCE_SUB_CHANNEL or e.sender_id == config.OWNER_ID:
        return True
    c = config.FORCE_SUB_CHANNEL
    if isinstance(c, str) and c.lstrip('-').isdigit(): c = int(c)
    try:
        await bot_client.get_permissions(c, e.sender_id)
        return True
    except UserNotParticipantError:
        try:
            cl = (await bot_client.get_entity(c)).username
            cl = f"https://t.me/{cl}"
        except:
            cl = "https://t.me/" # Fallback
        btns = [[Button.url("Join Channel", cl)], [Button.inline("I have Joined, Retry", data='{"action":"retry_fsub"}')]]
        await e.respond(strings['FSUB_MESSAGE'], buttons=btns, parse_mode='html')
        return False
    except Exception:
        return True

async def send_main_menu(e):
    s = await e.get_sender()
    st = strings['START_TEXT'].format(user_firstname=s.first_name)
    btns = [[Button.inline("🚀 Members Adding Bot", data='{"action":"members_adding_menu"}')]]
    try:
        await e.edit(st, buttons=btns, file=config.START_IMAGE_URL, parse_mode='Markdown')
    except Exception:
        await e.respond(st, buttons=btns, file=config.START_IMAGE_URL, parse_mode='Markdown')

async def send_help_menu(e):
    try:
        await e.edit(strings['HELP_TEXT_FEATURES'], buttons=[[Button.inline("« Back", '{"action":"main_menu"}')]], parse_mode='Markdown')
    except MessageNotModifiedError: pass

async def send_commands_menu(e):
    try:
        await e.edit(strings['COMMANDS_TEXT'], buttons=[[Button.inline("« Back", '{"action":"main_menu"}')]], parse_mode='Markdown')
    except MessageNotModifiedError: pass

async def send_settings_menu(e):
    buttons = [
        [Button.inline("👥 Members Adding", data='{"action":"members_adding_menu"}')],
        [Button.inline("« Back", '{"action":"main_menu"}')]
    ]
    try:
        await e.edit(strings['SETTINGS_MENU_TEXT'], buttons=buttons, parse_mode='Markdown')
    except MessageNotModifiedError: pass

async def send_members_adding_menu(e, uid):
    text = "👥 **Members Adding Bot Settings**\n\nHere you can manage your accounts and tasks."
    buttons = [
        [Button.inline("➕ Add Account", data='{"action":"add_member_account"}')],
        [Button.inline("📝 Manage Accounts", data='{"action":"manage_member_accounts"}')],
        [Button.inline("➕ Create Task", data='{"action":"create_adding_task"}')],
        [Button.inline("⚙️ Manage Tasks", data='{"action":"manage_adding_tasks"}')],
        [Button.inline("« Back", '{"action":"settings"}')]
    ]
    try:
        await e.edit(text, buttons=buttons, parse_mode='Markdown')
    except MessageNotModifiedError:
        pass
    except Exception:
        await e.respond(text, buttons=buttons, parse_mode='Markdown')

async def display_member_accounts(e, uid):
    owner_data = db.get_user_data(uid)
    accounts = utils.get(owner_data, 'user_accounts', [])
    
    valid_accounts = [acc for acc in accounts if acc.get('logged_in') and acc.get('session_string')]
    
    if not valid_accounts:
        try:
            return await e.edit(strings['NO_ACCOUNTS_FOR_ADDING'], buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='Markdown')
        except MessageNotModifiedError: return

    text = strings['MY_ACCOUNTS_HEADER']
    buttons = []
    for account in valid_accounts:
        phone = utils.get(account, 'phone_number', 'N/A')
        acc_id = utils.get(account, 'account_id', 'N/A')
        status = strings['ACCOUNT_STATUS_HEALTHY']
        if utils.get(account, 'is_banned_for_adding'): status = "⛔ Banned"
        elif utils.get(account, 'flood_wait_until', 0) > time.time(): status = f"⏳ Flood Wait"
        
        text += strings['ACCOUNT_STATUS_ENTRY'].format(
            phone_number=phone, account_id=acc_id, status=status,
            daily_adds=utils.get(account, 'daily_adds_count', 0), limit=config.MAX_DAILY_ADDS_PER_ACCOUNT,
            soft_errors=utils.get(account, 'soft_error_count', 0), soft_limit=config.SOFT_ADD_LIMIT_ERRORS
        )
        buttons.append([Button.inline(f"Account {phone}", f'{{"action":"member_account_details","account_id":{acc_id}}}')])
    
    buttons.append([Button.inline("« Back", '{"action":"members_adding_menu"}')])
    await e.edit(text, buttons=buttons, parse_mode='Markdown')

async def send_member_account_details(e, uid, account_id):
    account_info = db.find_user_account_in_owner_doc(uid, account_id)
    if not account_info: return await e.answer("Account not found.", alert=True)
    
    phone = utils.get(account_info, 'phone_number', 'N/A')
    text = f"👤 **Account Details:** `{phone}`"
    buttons = [
        [Button.inline("🔄 Re-login", f'{{"action":"relogin_member_account","account_id":{account_id}}}'),
         Button.inline("🗑️ Delete", f'{{"action":"confirm_delete_member_account","account_id":{account_id}}}')],
        [Button.inline("« Back", '{"action":"manage_member_accounts"}')]
    ]
    await e.edit(text, buttons=buttons, parse_mode='Markdown')

# FIX: Added resolver_client as a parameter with a default value of None
async def send_create_adding_task_menu(e, uid, resolver_client=None):
    owner_data = db.get_user_data(uid)
    existing_tasks = utils.get(owner_data, 'adding_tasks', [])
    next_task_id = max([t.get('task_id', 0) for t in existing_tasks] + [0]) + 1
    
    active_account_ids = [acc.get('account_id') for acc in utils.get(owner_data, 'user_accounts', []) if acc.get('logged_in')]
    
    new_task = {
        "task_id": next_task_id, "is_active": False, "status": "draft",
        "source_chat_ids": [], "target_chat_id": None, "assigned_accounts": active_account_ids,
        "current_member_index": 0, "added_members_count": 0, "last_progress_message_id": None
    }
    db.update_user_data(uid, {"$push": {"adding_tasks": new_task}})
    await send_adding_task_details_menu(e, uid, next_task_id, resolver_client)

# FIX: Added resolver_client as a parameter with a default value of None
async def send_manage_adding_tasks_menu(e, uid, resolver_client=None):
    owner_data = db.get_user_data(uid)
    tasks = utils.get(owner_data, 'adding_tasks', [])
    if not tasks:
        try:
            return await e.edit("You have no tasks. Use 'Create Task' to add one.", buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]])
        except MessageNotModifiedError: return

    text = strings['MANAGE_TASKS_HEADER']
    buttons = []
    import members_adder
    
    client_to_use = resolver_client or bot_client

    for task in tasks:
        task_id = utils.get(task, 'task_id', 'N/A')
        status = strings.get(f'TASK_STATUS_{utils.get(task, "status", "draft").upper()}', 'Draft')
        
        source_title = "Not Set"
        if utils.get(task, 'source_chat_ids'):
            try:
                source_title = await members_adder.get_chat_title(client_to_use, task['source_chat_ids'][0])
            except Exception:
                source_title = f"ID: `{task['source_chat_ids'][0]}`"
        
        target_title = "Not Set"
        if utils.get(task, 'target_chat_id'):
            try:
                target_title = await members_adder.get_chat_title(client_to_use, task['target_chat_id'])
            except Exception:
                target_title = f"ID: `{task['target_chat_id']}`"
            
        text += f"• Task {task_id} ({status}): {source_title} ➡️ {target_title}\n"
        buttons.append([Button.inline(f"Task {task_id} - {status}", f'{{"action":"m_add_task_menu","task_id":{task_id}}}')])

    buttons.append([Button.inline("« Back", '{"action":"members_adding_menu"}')])
    await e.edit(text, buttons=buttons, parse_mode='Markdown')

async def send_adding_task_details_menu(e, uid, task_id, resolver_client=None):
    task = db.get_task_in_owner_doc(uid, task_id)
    if not task: return await e.answer("Task not found.", alert=True)

    import members_adder
    client_to_use = resolver_client or bot_client

    status_text = strings.get(f'TASK_STATUS_{utils.get(task, "status", "draft").upper()}', "Draft")
    
    source_info = "Not Set"
    if scids := utils.get(task, 'source_chat_ids'):
        titles = []
        for cid in scids:
            try:
                titles.append(await members_adder.get_chat_title(client_to_use, cid))
            except Exception:
                titles.append(f"ID: `{cid}`")
        source_info = ", ".join(titles)

    target_info = "Not Set"
    if tcid := utils.get(task, 'target_chat_id'):
        try:
            target_info = await members_adder.get_chat_title(client_to_use, tcid)
        except Exception:
            target_info = f"ID: `{tcid}`"

    text = strings['TASK_DETAILS_HEADER'].format(
        task_id=task_id, status=status_text, source_chat_info=source_info,
        target_chat_info=target_info, assigned_accounts_info=f"{len(utils.get(task, 'assigned_accounts',[]))} accounts",
        total_added=utils.get(task, 'added_members_count', 0)
    )
    buttons = [
        [Button.inline("📤 Set Source Chat(s)", f'{{"action":"set_task_source_chat","task_id":{task_id}}}')],
        [Button.inline("📥 Set Target Chat", f'{{"action":"set_task_target_chat","task_id":{task_id}}}')]
    ]
    if utils.get(task, 'status') == 'active':
        buttons.append([Button.inline("⏸️ Pause Task", f'{{"action":"pause_adding_task","task_id":{task_id}}}')])
    else:
        if task.get('source_chat_ids') and task.get('target_chat_id'):
            buttons.append([Button.inline("▶️ Start Task", f'{{"action":"start_adding_task","task_id":{task_id}}}')])
    
    buttons.extend([
        [Button.inline("🗑️ Delete Task", f'{{"action":"confirm_delete_adding_task","task_id":{task_id}}}')],
        [Button.inline("« Back", '{"action":"manage_adding_tasks"}')]
    ])
    
    try:
        await e.edit(text, buttons=buttons, parse_mode='Markdown')
    except MessageNotModifiedError:
        pass
