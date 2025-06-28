from telethon.tl.custom.button import Button
import datetime
import time
import json # Import json for creating callback data
from telethon.errors import UserNotParticipantError

from config import config
import utils
import db
from strings import strings

bot_client = None

def set_bot_client(client):
    global bot_client
    bot_client = client

def yesno(c):
    # These are simple string callbacks, handled in the 'except' block in handlers.py
    return [[Button.inline("Yes", f'yes_{c}')], [Button.inline("No", f'no_{c}')]]

async def check_fsub(e):
    if not config.FORCE_SUB_CHANNEL or e.sender_id == config.OWNER_ID:
        return True
    c = config.FORCE_SUB_CHANNEL
    if isinstance(c, str) and c.lstrip('-').isdigit():
        c = int(c)
    try:
        await bot_client.get_permissions(c, e.sender_id)
        return True
    except UserNotParticipantError:
        try:
            ce = await bot_client.get_entity(c)
            cl = f"https://t.me/{ce.username}"
        except:
            cl = "https://t.me/"
        btns = [[Button.url("Join Channel", cl)], [Button.inline("I have Joined, Retry", data=f'{{"action":"retry_fsub"}}')]]
        await e.respond(strings['FSUB_MESSAGE'], buttons=btns, parse_mode='html')
        return False
    except Exception as ex:
        pass
        return True

async def send_main_menu(e):
    s = await e.get_sender()
    st = strings['START_TEXT'].format(user_firstname=s.first_name)
    btns = [
        [Button.inline("🚀 Let's Go!", data='{"action":"members_adding_menu"}')]
    ]
    try:
        await e.edit(message=st, buttons=btns, link_preview=False, parse_mode='Markdown')
    except Exception:
        await e.respond(file=config.START_IMAGE_URL, message=st, buttons=btns, link_preview=False, parse_mode='Markdown')

async def send_help_menu(e):
    try:
        await e.edit(strings['HELP_TEXT_FEATURES'],
                              buttons=[[Button.inline("« Back to Main", data='{"action":"main_menu"}')]],
                              parse_mode='Markdown')
    except Exception:
        await e.respond(strings['HELP_TEXT_FEATURES'],
                              buttons=[[Button.inline("« Back to Main", data='{"action":"main_menu"}')]],
                              parse_mode='Markdown')

async def send_commands_menu(e):
    try:
        await e.edit(strings['COMMANDS_TEXT'],
                              buttons=[[Button.inline("« Back to Main", data='{"action":"main_menu"}')]],
                              parse_mode='Markdown')
    except Exception:
        await e.respond(strings['COMMANDS_TEXT'],
                              buttons=[[Button.inline("« Back to Main", data='{"action":"main_menu"}')]],
                              parse_mode='Markdown')

async def send_settings_menu(e):
    text = strings['SETTINGS_MENU_TEXT']
    buttons = [
        [Button.inline("👥 Members Adding", data='{"action":"members_adding_menu"}')],
        [Button.inline("📣 Broadcast", data='{"action":"user_broadcast"}')],
        [Button.inline("« Back to Main", data='{"action":"main_menu"}')]
    ]
    try:
        await e.edit(text, buttons=buttons, parse_mode='Markdown')
    except Exception:
        await e.respond(text, buttons=buttons, parse_mode='Markdown')


async def send_members_adding_menu(e, uid):
    owner_data = db.get_user_data(uid)
    num_accounts = len([acc for acc in utils.get(owner_data, 'user_accounts', []) if utils.get(acc, 'logged_in')])

    add_account_button_text = strings['BUTTON_ADD_ACCOUNT']
    if num_accounts > 0:
        add_account_button_text = strings['BUTTON_ADD_MORE_ACCOUNT']

    text = "👥 **Members Adding Bot Settings**\n\n" \
           "Here you can manage your accounts for adding members and set up adding tasks."
    buttons = [
        [Button.inline(add_account_button_text, data='{"action":"add_member_account"}')],
        [Button.inline("📝 Manage Accounts", data='{"action":"manage_member_accounts"}')],
        [Button.inline("➕ Create Task", data='{"action":"create_adding_task"}')],
        [Button.inline("⚙️ Manage Tasks", data='{"action":"manage_adding_tasks"}')],
        [Button.inline("« Back", data='{"action":"main_menu"}')]
    ]
    try:
        await e.edit(text, buttons=buttons, parse_mode='Markdown')
    except Exception:
        await e.respond(text, buttons=buttons, parse_mode='Markdown')

async def display_member_accounts(e, uid):
    owner_data = db.get_user_data(uid)
    accounts = utils.get(owner_data, 'user_accounts', [])

    accounts_to_remove_ids = []
    for account in accounts:
        if not utils.get(account, 'logged_in') and not utils.get(account, 'session_string'):
            accounts_to_remove_ids.append(utils.get(account, 'account_id'))
    
    if accounts_to_remove_ids:
        db.users_db.update_one(
            {"chat_id": uid},
            {"$pull": {"user_accounts": {"account_id": {"$in": accounts_to_remove_ids}}}}
        )
        owner_data = db.get_user_data(uid)
        accounts = utils.get(owner_data, 'user_accounts', [])


    if not accounts:
        try:
            return await e.edit(strings['NO_ACCOUNTS_FOR_ADDING'], buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='Markdown')
        except Exception:
            return await e.respond(strings['NO_ACCOUNTS_FOR_ADDING'], buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='Markdown')

    text = strings['MY_ACCOUNTS_HEADER']
    buttons = []
    for account in accounts:
        account_id = utils.get(account, 'account_id', 'N/A')
        phone_number = utils.get(account, 'phone_number', 'N/A')
        status = strings['ACCOUNT_STATUS_INACTIVE']
        
        if utils.get(account, 'logged_in'):
            status = strings['ACCOUNT_STATUS_HEALTHY']
            if utils.get(account, 'is_banned_for_adding'):
                status = strings['ACCOUNT_STATUS_SUSPENDED'].format(reason="Banned")
            elif utils.get(account, 'flood_wait_until', 0) > time.time():
                remaining_time = int(utils.get(account, 'flood_wait_until', 0) - time.time())
                status = strings['ACCOUNT_STATUS_FLOODED'].format(until_time=utils.fd(remaining_time))
            elif utils.get(account, 'soft_error_count', 0) >= config.SOFT_ADD_LIMIT_ERRORS:
                status = strings['ACCOUNT_STATUS_SUSPENDED'].format(reason="Too many errors")
        
        daily_adds = utils.get(account, 'daily_adds_count', 0)
        soft_errors = utils.get(account, 'soft_error_count', 0)
        
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
    
    buttons.append([Button.inline("« Back", data='{"action":"members_adding_menu"}')])
    try:
        await e.edit(text, buttons=buttons, parse_mode='Markdown')
    except Exception:
        await e.respond(text, buttons=buttons, parse_mode='Markdown')

async def send_member_account_details(e, uid, account_id):
    owner_data = db.get_user_data(uid)
    account_info = utils.get(owner_data, 'user_accounts', [])
    account_info = next((acc for acc in account_info if utils.get(acc, 'account_id') == account_id), None)

    if not account_info:
        return await e.answer("Account not found.", alert=True)
    
    phone_number = utils.get(account_info, 'phone_number', 'N/A')
    status = "Inactive"
    if utils.get(account_info, 'logged_in'):
        status = "Active"
        if utils.get(account_info, 'is_banned_for_adding'): status = "Banned"
        elif utils.get(account_info, 'flood_wait_until', 0) > time.time(): status = f"Flood Wait (until {utils.fd(utils.get(account_info, 'flood_wait_until', 0) - time.time())})"
        elif utils.get(account_info, 'soft_error_count', 0) >= config.SOFT_ADD_LIMIT_ERRORS: status = "Suspended (too many errors)"
    
    text = f"👤 **Account Details:** {phone_number}\n\n" \
           f"Account ID: `{account_id}`\n" \
           f"Status: {status}\n" \
           f"Logged In: {'Yes' if utils.get(account_info, 'logged_in') else 'No'}\n" \
           f"Daily Adds: {utils.get(account_info, 'daily_adds_count', 0)} / {config.MAX_DAILY_ADDS_PER_ACCOUNT}\n" \
           f"Soft Errors Today: {utils.get(account_info, 'soft_error_count', 0)} / {config.SOFT_ADD_LIMIT_ERRORS}\n" \
           f"Last Login: {datetime.datetime.fromtimestamp(utils.get(account_info, 'last_login_time', 0)).strftime('%Y-%m-%d %H:%M:%S UTC') if utils.get(account_info, 'last_login_time') else 'N/A'}\n" \
           f"Last Error: {utils.get(account_info, 'error_type', 'None')}\n"
           
    buttons = [
        [Button.inline("Re-login Account", f'{{"action":"relogin_member_account","account_id":{account_id}}}')],
        [Button.inline("Toggle Ban Status", f'{{"action":"toggle_member_account_ban","account_id":{account_id}}}')],
        [Button.inline("Delete Account", f'{{"action":"confirm_delete_member_account","account_id":{account_id}}}')],
        [Button.inline("« Back", data='{"action":"manage_member_accounts"}')]
    ]
    try:
        await e.edit(text, buttons=buttons, parse_mode='Markdown')
    except Exception:
        await e.respond(text, buttons=buttons, parse_mode='Markdown')


async def send_create_adding_task_menu(e, uid):
    owner_data = db.get_user_data(uid)
    existing_tasks = utils.get(owner_data, 'adding_tasks', [])
    next_task_id = 1
    if existing_tasks:
        max_task_id = max(utils.get(task, 'task_id', 0) for task in existing_tasks)
        next_task_id = max_task_id + 1

    all_user_accounts = utils.get(owner_data, 'user_accounts', [])
    assigned_account_ids = [
        acc.get('account_id') for acc in all_user_accounts 
        if acc.get('logged_in') and not acc.get('is_banned_for_adding')
    ]

    new_task = {
        "task_id": next_task_id,
        "is_active": False,
        "status": "draft",
        "source_chat_ids": [],
        "target_chat_id": None,
        "assigned_accounts": assigned_account_ids,
        "current_member_index": 0,
        "added_members_count": 0,
        "last_progress_message_id": None
    }
    db.update_user_data(uid, {"$push": {"adding_tasks": new_task}})
    
    await send_adding_task_details_menu(e, uid, next_task_id)


async def send_manage_adding_tasks_menu(e, uid):
    owner_data = db.get_user_data(uid)
    tasks = utils.get(owner_data, 'adding_tasks', [])
    
    if not tasks:
        text = "You have no configured adding tasks yet. Use '➕ Create Task' to add one."
        buttons = [[Button.inline("« Back", data='{"action":"members_adding_menu"}')]]
        try:
            return await e.edit(text, buttons=buttons, parse_mode='Markdown')
        except Exception:
            return await e.respond(text, buttons=buttons, parse_mode='Markdown')

    # FIX: Remove the detailed task list from the message text as requested.
    text = strings['MANAGE_TASKS_HEADER']
    buttons = []
    
    for task in tasks:
        task_id = utils.get(task, 'task_id', 'N/A')
        status = utils.get(task, 'status', 'draft')
        status_text = strings.get(f'TASK_STATUS_{status.upper()}', status.capitalize())
        
        # The summary text is no longer added to the message body.
        # It is now just a list of buttons.
        buttons.append([Button.inline(f"Task {task_id} - {status_text}", f'{{"action":"m_add_task_menu","task_id":{task_id}}}')])

    buttons.append([Button.inline("« Back", data='{"action":"members_adding_menu"}')])
    try:
        await e.edit(text, buttons=buttons, parse_mode='Markdown')
    except Exception:
        await e.respond(text, buttons=buttons, parse_mode='Markdown')

async def send_adding_task_details_menu(e, uid, task_id):
    owner_data = db.get_user_data(uid)
    task = db.get_task_in_owner_doc(uid, task_id)
    if not task: return await e.answer("Task not found.", alert=True)

    status_text = strings.get(f'TASK_STATUS_{utils.get(task, "status", "draft").upper()}', utils.get(task, "status", "draft").capitalize())

    source_chat_info_display = "Not Set"
    import members_adder 
    source_chat_ids = utils.get(task, 'source_chat_ids', [])
    if source_chat_ids:
        source_titles = []
        for chat_id in source_chat_ids:
            try:
                title = await members_adder.get_chat_title(bot_client, chat_id)
                source_titles.append(f"- {title}")
            except:
                source_titles.append(f"- ID: `{chat_id}`")
        source_chat_info_display = "\n".join(source_titles)

    target_chat_info_display = "Not Set"
    target_chat_id = utils.get(task, 'target_chat_id')
    if target_chat_id:
        try:
            target_chat_info_display = await members_adder.get_chat_title(bot_client, target_chat_id)
        except:
            target_chat_info_display = f"ID: `{target_chat_id}`"

    assigned_accounts_info = []
    for acc_id in utils.get(task, 'assigned_accounts', []):
        acc_info = db.find_user_account_in_owner_doc(uid, acc_id)
        if acc_info:
            assigned_accounts_info.append(f"`{utils.get(acc_info, 'phone_number', f'ID: {acc_id}')}`")
    assigned_accounts_display = ", ".join(assigned_accounts_info) if assigned_accounts_info else "None"

    total_added_members = utils.get(task, 'added_members_count', 0)

    text = strings['TASK_DETAILS_HEADER'].format(
        task_id=task_id,
        status=status_text,
        source_chat_info=source_chat_info_display,
        target_chat_info=target_chat_info_display,
        assigned_accounts_display=assigned_accounts_display,
        total_added=total_added_members
    )
    
    # FIX: Use clean JSON for callbacks
    buttons = [
        [
            Button.inline("➕ Add Source Chat", data=json.dumps({"action": "m_add_addsource", "task_id": task_id})),
            Button.inline("🗑️ Clear Sources", data=json.dumps({"action": "m_add_clearsource", "task_id": task_id}))
        ],
        [Button.inline("📥 Set/Change Target", data=json.dumps({"action": "m_add_settarget", "task_id": task_id}))]
    ]
    
    action_buttons = []
    task_status = utils.get(task, 'status')
    if task_status == 'active':
        action_buttons.append(Button.inline("⏸️ Pause Task", data=json.dumps({"action":"pause_adding_task", "task_id":task_id})))
    else: 
        if utils.get(task, 'source_chat_ids') and utils.get(task, 'target_chat_id') and utils.get(task, 'assigned_accounts'):
            action_buttons.append(Button.inline("▶️ Start Task", data=json.dumps({"action":"start_adding_task", "task_id":task_id})))

    if action_buttons:
        buttons.append(action_buttons)
    
    buttons.append([Button.inline("🗑️ Delete Task", data=json.dumps({"action":"confirm_delete_adding_task", "task_id":task_id}))])
    buttons.append([Button.inline("« Back", data='{"action":"manage_adding_tasks"}')])
    
    try:
        await e.edit(text, buttons=buttons, parse_mode='Markdown')
    except Exception:
        await e.respond(text, buttons=buttons, parse_mode='Markdown')
