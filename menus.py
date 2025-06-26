from telethon.tl.custom.button import Button
import datetime
import time
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
    return [[Button.inline("Yes", f'{{"action":"yes_{c}"}}')], [Button.inline("No", f'{{"action":"no_{c}"}}')]]

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

async def send_main_menu(e): # Main menu for the bot's simplified interface
    s = await e.get_sender()
    st = strings['START_TEXT'].format(user_firstname=s.first_name)
    btns = [
        [Button.inline("🚀 Let's Go!", data='{"action":"members_adding_menu"}')] # Direct to member adding menu
    ]
    try:
        await e.edit(message=st, buttons=btns, link_preview=False, parse_mode='Markdown') # Use Markdown
    except Exception:
        await e.respond(file=config.START_IMAGE_URL, message=st, buttons=btns, link_preview=False, parse_mode='Markdown') # Use Markdown

async def send_help_menu(e):
    try:
        await e.edit(strings['HELP_TEXT_FEATURES'],
                         buttons=[[Button.inline("« Back to Main", data='{"action":"main_menu"}')]],
                         parse_mode='Markdown') # Use Markdown
    except Exception:
        await e.respond(strings['HELP_TEXT_FEATURES'],
                         buttons=[[Button.inline("« Back to Main", data='{"action":"main_menu"}')]],
                         parse_mode='Markdown') # Use Markdown

async def send_commands_menu(e):
    try:
        await e.edit(strings['COMMANDS_TEXT'],
                         buttons=[[Button.inline("« Back to Main", data='{"action":"main_menu"}')]],
                         parse_mode='Markdown') # Use Markdown
    except Exception:
        await e.respond(strings['COMMANDS_TEXT'],
                         buttons=[[Button.inline("« Back to Main", data='{"action":"main_menu"}')]],
                         parse_mode='Markdown') # Use Markdown

async def send_settings_menu(e): # This is the main members adding dashboard now
    text = strings['SETTINGS_MENU_TEXT']
    buttons = [
        [Button.inline("👥 Members Adding", data='{"action":"members_adding_menu"}')], # This is a placeholder for the menu title
        [Button.inline("📣 Broadcast", data='{"action":"user_broadcast"}')],
        [Button.inline("« Back to Main", data='{"action":"main_menu"}')]
    ]
    try:
        await e.edit(text, buttons=buttons, parse_mode='Markdown') # Use Markdown
    except Exception:
        await e.respond(text, buttons=buttons, parse_mode='Markdown') # Use Markdown


async def send_members_adding_menu(e, uid):
    text = "👥 **Members Adding Bot Settings**\n\n" \
           "Here you can manage your accounts for adding members and set up adding tasks."
    buttons = [
        [Button.inline("➕ Add Account", data='{"action":"add_member_account"}')],
        [Button.inline("📝 Manage Accounts", data='{"action":"manage_member_accounts"}')],
        [Button.inline("➕ Create Task", data='{"action":"create_adding_task"}')],
        [Button.inline("⚙️ Manage Tasks", data='{"action":"manage_adding_tasks"}')],
        [Button.inline("« Back", data='{"action":"main_menu"}')] # Back to main menu
    ]
    try:
        await e.edit(text, buttons=buttons, parse_mode='Markdown') # Use Markdown
    except Exception:
        await e.respond(text, buttons=buttons, parse_mode='Markdown') # Use Markdown

async def display_member_accounts(e, uid):
    owner_data = db.get_user_data(uid)
    accounts = utils.get(owner_data, 'user_accounts', [])
    if not accounts:
        try:
            return await e.edit(strings['NO_ACCOUNTS_FOR_ADDING'], buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='Markdown') # Use Markdown
        except Exception:
            return await e.respond(strings['NO_ACCOUNTS_FOR_ADDING'], buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='Markdown') # Use Markdown

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
        else:
            status = strings['ACCOUNT_STATUS_INVALID']

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
        await e.edit(text, buttons=buttons, parse_mode='Markdown') # Use Markdown
    except Exception:
        await e.respond(text, buttons=buttons, parse_mode='Markdown') # Use Markdown

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
        await e.edit(text, buttons=buttons, parse_mode='Markdown') # Use Markdown
    except Exception:
        await e.respond(text, buttons=buttons, parse_mode='Markdown') # Use Markdown


async def send_create_adding_task_menu(e, uid):
    owner_data = db.get_user_data(uid)
    existing_tasks = utils.get(owner_data, 'adding_tasks', [])
    next_task_id = 1
    if existing_tasks:
        max_task_id = max(utils.get(task, 'task_id', 0) for task in existing_tasks)
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
    db.update_user_data(uid, {"$push": {"adding_tasks": new_task}})
    
    await send_adding_task_details_menu(e, uid, next_task_id)


async def send_manage_adding_tasks_menu(e, uid):
    owner_data = db.get_user_data(uid)
    tasks = utils.get(owner_data, 'adding_tasks', [])
    
    # CRITICAL FIX: Only show tasks that are configured.
    # A task is "configured" if it has at least a source, target, OR assigned accounts.
    # This avoids displaying empty "Draft" tasks that were just created.
    # However, if there are NO configured tasks at all, we should still show *all* tasks
    # so the user has something to interact with (e.g., their newly created draft).
    
    configured_tasks = [t for t in tasks if utils.get(t, 'source_chat_id') or utils.get(t, 'target_chat_ids') or utils.get(t, 'assigned_accounts')]
    
    if not configured_tasks and tasks: # If some tasks exist but none are configured yet, display all.
        display_tasks = tasks
    else: # Otherwise, only display configured tasks.
        display_tasks = configured_tasks

    if not display_tasks:
        text = "You have no configured adding tasks yet. Use '➕ Create Task' to add and configure one."
        buttons = [[Button.inline("« Back", data='{"action":"members_adding_menu"}')]]
        try:
            return await e.edit(text, buttons=buttons, parse_mode='Markdown') # Use Markdown
        except Exception:
            return await e.respond(text, buttons=buttons, parse_mode='Markdown') # Use Markdown

    text = strings['MANAGE_TASKS_HEADER'] # This header is now used only when displaying actual tasks
    buttons = []
    import members_adder 
    for task in display_tasks:
        task_id = utils.get(task, 'task_id', 'N/A')
        status = utils.get(task, 'status', 'draft')
        
        status_text = strings[f'TASK_STATUS_{status.upper()}']
        
        source_chat_title = "Not Set"
        if utils.get(task, 'source_chat_id'):
            try: source_chat_title = await members_adder.get_chat_title(bot_client, task['source_chat_id'])
            except: pass
        
        target_chat_titles = []
        for chat_id in utils.get(task, 'target_chat_ids', []):
            try: target_chat_titles.append(await members_adder.get_chat_title(bot_client, chat_id))
            except: pass
        target_chat_info = ", ".join(target_chat_titles) if target_chat_titles else "Not Set"

        num_accounts = len(utils.get(task, 'assigned_accounts', []))

        text += strings['TASK_ENTRY_INFO'].format(
            task_id=task_id,
            status=status_text,
            source_chat_title=source_chat_title,
            target_chat_titles=target_chat_info,
            num_accounts=num_accounts
        ) + "\n"
        buttons.append([Button.inline(f"Task {task_id} - {status_text}", f'{{"action":"m_add_task_menu","task_id":{task_id}}}')])

    buttons.append([Button.inline("« Back", data='{"action":"members_adding_menu"}')])
    try:
        await e.edit(text, buttons=buttons, parse_mode='Markdown') # Use Markdown
    except Exception:
        await e.respond(text, buttons=buttons, parse_mode='Markdown') # Use Markdown

async def send_adding_task_details_menu(e, uid, task_id):
    owner_data = db.get_user_data(uid)
    task = db.get_task_in_owner_doc(uid, task_id)
    if not task: return await e.answer("Task not found.", alert=True)

    status_text = strings[f'TASK_STATUS_{utils.get(task, "status", "draft").upper()}']

    source_chat_info = "Not Set"
    import members_adder 
    if utils.get(task, 'source_chat_id'):
        try: source_chat_info = await members_adder.get_chat_title(bot_client, task['source_chat_id'])
        except: source_chat_info = f"ID: `{task['source_chat_id']}`" # Use Markdown for code block
    
    target_chat_titles = []
    for chat_id in utils.get(task, 'target_chat_ids', []):
        try: target_chat_titles.append(await members_adder.get_chat_title(bot_client, chat_id))
        except: target_chat_titles.append(f"ID: `{chat_id}`") # Use Markdown for code block
    target_chat_info = ", ".join(target_chat_titles) if target_chat_titles else "Not Set"

    assigned_accounts_info = []
    for acc_id in utils.get(task, 'assigned_accounts', []):
        acc_info = db.find_user_account_in_owner_doc(uid, acc_id)
        if acc_info:
            assigned_accounts_info.append(f"`{utils.get(acc_info, 'phone_number', f'ID: {acc_id}')}`") # Use Markdown for code block
    assigned_accounts_display = ", ".join(assigned_accounts_info) if assigned_accounts_info else "None"

    total_added_members = utils.get(task, 'added_members_count', 0)

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
    
    if utils.get(task, 'status') == 'active':
        buttons.append([Button.inline("⏸️ Pause Task", f'{{"action":"pause_adding_task","task_id":{task_id}}}')])
    elif utils.get(task, 'status') == 'paused' or utils.get(task, 'status') == 'draft' or utils.get(task, 'status') == 'completed':
        if utils.get(task, 'source_chat_id') and utils.get(task, 'target_chat_ids') and utils.get(task, 'assigned_accounts'):
            buttons.append([Button.inline("▶️ Start Task", f'{{"action":"start_adding_task","task_id":{task_id}}}')])
    
    buttons.append([Button.inline("🗑️ Delete Task", f'{{"action":"confirm_delete_adding_task","task_id":{task_id}}}')])
    buttons.append([Button.inline("« Back", data='{"action":"manage_adding_tasks"}')])
    
    try:
        await e.edit(text, buttons=buttons, parse_mode='Markdown') # Use Markdown
    except Exception:
        await e.respond(text, buttons=buttons, parse_mode='Markdown') # Use Markdown

async def send_assign_accounts_menu(e, uid, task_id):
    owner_data = db.get_user_data(uid)
    all_accounts = utils.get(owner_data, 'user_accounts', [])
    current_task = db.get_task_in_owner_doc(uid, task_id)

    if not current_task: return await e.answer("Task not found.", alert=True)
    if not all_accounts: return await e.answer(strings['NO_ACCOUNTS_FOR_ADDING'], alert=True)

    assigned_account_ids = utils.get(current_task, 'assigned_accounts', [])

    text = strings['CHOOSE_ACCOUNTS_FOR_TASK'].format(task_id=task_id)
    buttons = []

    for account in all_accounts:
        acc_id = utils.get(account, 'account_id')
        phone_number = utils.get(account, 'phone_number')
        prefix = "✅ " if acc_id in assigned_account_ids else ""
        buttons.append([Button.inline(f"{prefix}{phone_number} (ID: {acc_id})", f'm_add_assign_acc|{task_id}|{acc_id}')])
    
    buttons.append([Button.inline("Done ✅", f'{{"action":"m_add_task_menu", "task_id":{task_id}}}')])
    buttons.append([Button.inline("« Back", f'{{"action":"m_add_task_menu", "task_id":{task_id}}}')])

    try:
        await e.edit(text, buttons=buttons, parse_mode='Markdown') # Use Markdown
    except Exception:
        await e.respond(text, buttons=buttons, parse_mode='Markdown') # Use Markdown

async def send_chat_selection_menu(e, uid, selection_type, task_id, page=1):
    owner_data = db.get_user_data(uid)
    
    # Use config.device_info for TelegramClient in menus.py when fetching dialogs
    # This might require passing config from bot.py to menus.py, which is already done.
    
    temp_msg = await e.respond("Fetching your chats, please wait...")
    
    try:
        # Use the main bot client to fetch dialogs from the owner's perspective
        all_dialogs = await bot_client.get_dialogs(limit=None)
        
        dialogs = [d for d in all_dialogs if not (d.is_user and d.entity.is_self)]

        items_per_page = 5
        total_items = len(dialogs)
        total_pages = (total_items + items_per_page - 1) // items_per_page
        
        start_index = (page - 1) * items_per_page
        end_index = start_index + items_per_page
        
        paginated_dialogs = dialogs[start_index:end_index]
        
        buttons = []
        
        current_task_doc = db.get_task_in_owner_doc(uid, task_id)
        selected_to = utils.get(current_task_doc, 'target_chat_ids', [])
        selected_from = utils.get(current_task_doc, 'source_chat_id')
        
        buttons.append([Button.inline("🔄 Refresh List", f'm_add_set|{selection_type}|{task_id}|{page}')])

        for dialog in paginated_dialogs:
            prefix = ""
            if selection_type == 'to' and dialog.id in selected_to:
                prefix = "✅ "
            elif selection_type == 'from' and dialog.id == selected_from:
                prefix = "✅ "

            title = (utils.get(dialog, 'title', '')[:30] + '..') if len(utils.get(dialog, 'title', '')) > 32 else utils.get(dialog, 'title', 'Unknown Chat')
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
        
        await temp_msg.edit(prompt.format(task_id=task_id), buttons=buttons, parse_mode='Markdown') # Use Markdown

    except Exception as ex:
        # LOGGER.error(f"Chat selection error for {uid}: {ex}") # Add logging here
        back_action = f'{{"action":"m_add_task_menu", "task_id":{task_id}}}'
        await temp_msg.edit("Could not fetch chats. Please try again.", buttons=[[Button.inline("« Back", back_action)]], parse_mode='Markdown') # Use Markdown
    finally:
        pass
