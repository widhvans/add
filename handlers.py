import logging
import json
import asyncio
import time
import datetime
import re
import random

from telethon import TelegramClient, events, functions
from telethon.sessions import StringSession
from telethon.tl.custom.button import Button
from telethon.tl.types import ReplyKeyboardMarkup, KeyboardButton, KeyboardButtonRequestPhone, ReplyKeyboardHide
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError, PasswordHashInvalidError,
    FloodWaitError, UserIsBlockedError, InputUserDeactivatedError,
    UserNotParticipantError, MessageNotModifiedError, PhoneCodeExpiredError,
    ChannelPrivateError, UserAlreadyParticipantError, ValueError as TelethonValueError
)

from config import config
import db
import utils
import menus
import members_adder
from strings import strings

LOGGER = logging.getLogger(__name__)

_bot_client_instance = None
ONGOING_LOGIN_CLIENTS = {}

def set_bot_client_for_modules(client):
    """Sets the bot_client instance for handlers and other modules that need it."""
    global _bot_client_instance
    _bot_client_instance = client
    menus.set_bot_client(client)
    members_adder.set_bot_client(client)

# --- Helper Functions ---

async def _get_an_active_user_client(uid):
    """Gets a single, active, logged-in user client instance for a given owner."""
    owner_data = db.get_user_data(uid)
    if not owner_data:
        return None
    
    active_accounts = [acc for acc in utils.get(owner_data, 'user_accounts', []) if utils.get(acc, 'logged_in')]
    if not active_accounts:
        return None

    # Shuffle to distribute the load if this is called frequently
    random.shuffle(active_accounts)

    for acc in active_accounts:
        acc_id = utils.get(acc, 'account_id')
        client = await members_adder.get_user_client(acc_id)
        if client and client.is_connected():
            LOGGER.info(f"Using account {acc_id} as resolver client for user {uid}.")
            return client
    return None

async def handle_add_member_account_flow(e):
    uid = e.sender_id
    if uid in ONGOING_LOGIN_CLIENTS:
        client_to_clean = ONGOING_LOGIN_CLIENTS.pop(uid)
        if client_to_clean.is_connected():
            await client_to_clean.disconnect()
            LOGGER.info(f"Cleaned up stale login client for user {uid}")
    db.update_user_data(uid, {"$set": {"state": "awaiting_member_account_number"}})
    await e.respond(strings['ADD_ACCOUNT_NUMBER_PROMPT'], parse_mode='Markdown')

async def _join_chat_with_all_accounts(e, uid, entity):
    """Attempts to join a given chat/channel with all of the user's active accounts."""
    await e.respond(strings['ACCOUNTS_JOINING_CHAT'], parse_mode='Markdown')
    
    owner_doc = db.get_user_data(uid)
    accounts_to_join = [acc for acc in utils.get(owner_doc, 'user_accounts', []) if acc.get('logged_in')]
    join_success = 0
    join_fail = 0

    for acc in accounts_to_join:
        acc_id = acc.get('account_id')
        client = await members_adder.get_user_client(acc_id)
        if client:
            try:
                await client(functions.channels.JoinChannelRequest(channel=entity))
                join_success += 1
                LOGGER.info(f"Account {acc_id} successfully joined {entity.id}")
                await asyncio.sleep(random.uniform(1, 2))
            except UserAlreadyParticipantError:
                join_success += 1
                LOGGER.info(f"Account {acc_id} was already in {entity.id}")
            except Exception as join_ex:
                join_fail += 1
                LOGGER.warning(f"Account {acc_id} failed to join {entity.id}: {join_ex}")
        else:
            join_fail += 1
            LOGGER.warning(f"Could not get a valid client for account {acc_id} to join chat.")
    await e.respond(strings['ACCOUNTS_JOIN_COMPLETE'].format(success_count=join_success, fail_count=join_fail), parse_mode='Markdown')

async def _handle_member_account_login_step(e, uid, account_id, input_text):
    owner_data = db.get_user_data(uid)
    client = ONGOING_LOGIN_CLIENTS.get(uid)
    if not client or not client.is_connected():
        LOGGER.warning(f"No active client found for user {uid} during login step. Resetting state.")
        db.update_user_data(uid, {"$set": {"state": None}})
        if account_id: db.update_user_data(uid, {"$pull": {"user_accounts": {"account_id": account_id}}})
        if uid in ONGOING_LOGIN_CLIENTS: del ONGOING_LOGIN_CLIENTS[uid]
        return await e.respond("Your login session expired. Please restart.", buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]])

    account_info_doc = db.users_db.find_one({"chat_id": uid, "user_accounts.account_id": account_id})
    account_info = next((acc for acc in utils.get(account_info_doc, 'user_accounts', []) if utils.get(acc, 'account_id') == account_id), None)
    
    if not account_info or not utils.get(account_info, 'temp_login_data'):
        LOGGER.warning(f"Login data missing for account_id {account_id}. Resetting.")
        db.update_user_data(uid, {"$set": {"state": None}})
        if uid in ONGOING_LOGIN_CLIENTS: del ONGOING_LOGIN_CLIENTS[uid]
        if client.is_connected(): await client.disconnect()
        if account_id: db.update_user_data(uid, {"$pull": {"user_accounts": {"account_id": account_id}}})
        return await e.respond("Invalid login data. Please restart.", buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]])
    
    temp_login_data = utils.get(account_info, 'temp_login_data')
    phone_number = utils.get(temp_login_data, 'ph')
    phone_code_hash = utils.get(temp_login_data, 'phash')
    
    processing_msg = await e.respond("Please wait... Validating your input.")
    await asyncio.sleep(0.5)

    try:
        if utils.get(owner_data, 'state').startswith("awaiting_member_account_code_"):
            otp_code = input_text.strip().replace(" ", "")
            await client.sign_in(phone=phone_number, code=otp_code, phone_code_hash=phone_code_hash)
        elif utils.get(owner_data, 'state').startswith("awaiting_member_account_password_"):
            password = input_text.strip()
            await client.sign_in(password=password)
        
        db.update_user_account_in_owner_doc(uid, account_id, {
            "session_string": client.session.save(), "logged_in": True, "last_login_time": time.time(),
            "is_active_for_adding": True, "temp_login_data": {}
        })
        db.update_user_data(uid, {"$unset": {"state": 1}})
        members_adder.USER_CLIENTS[account_id] = client
        if uid in ONGOING_LOGIN_CLIENTS: del ONGOING_LOGIN_CLIENTS[uid]
        
        await processing_msg.delete()
        await e.respond(strings['ACCOUNT_ADDED_SUCCESS'].format(phone_number=phone_number, account_id=account_id), parse_mode='html')
        await e.respond(strings['ADD_ANOTHER_ACCOUNT_PROMPT'], buttons=menus.yesno(f"add_another_account_{account_id}"), parse_mode='html')
    
    except SessionPasswordNeededError:
        await processing_msg.delete()
        db.update_user_data(uid, {"$set": {"state": f"awaiting_member_account_password_{account_id}"}})
        await e.respond(strings['ASK_PASSWORD_PROMPT'])
    except (PhoneCodeInvalidError, PasswordHashInvalidError, PhoneCodeExpiredError) as ex:
        await processing_msg.delete()
        error_message = strings['pass_invalid'] if isinstance(ex, PasswordHashInvalidError) else strings['code_invalid']
        db.update_user_data(uid, {"$pull": {"user_accounts": {"account_id": account_id}}, "$unset": {"state": 1}})
        if uid in ONGOING_LOGIN_CLIENTS:
            if client.is_connected(): await client.disconnect()
            del ONGOING_LOGIN_CLIENTS[uid]
        await e.respond(f"{error_message}\nPlease restart the login process.", buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]])
    except Exception as ex:
        LOGGER.error(f"Critical login error for {account_id}: {ex}", exc_info=True)
        await processing_msg.delete()
        db.update_user_data(uid, {"$pull": {"user_accounts": {"account_id": account_id}}, "$unset": {"state": 1}})
        if uid in ONGOING_LOGIN_CLIENTS:
            if client.is_connected(): await client.disconnect()
            del ONGOING_LOGIN_CLIENTS[uid]
        await e.respond(strings['ACCOUNT_LOGIN_FAILED'].format(error_message=ex), buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]])

async def _initiate_member_account_login_flow(e, uid, existing_account_id, phone_number, current_state):
    processing_msg = await e.respond(f"Attempting to log in: `{phone_number}`...", parse_mode='Markdown')
    if uid in ONGOING_LOGIN_CLIENTS:
        client_to_clean = ONGOING_LOGIN_CLIENTS.pop(uid)
        if client_to_clean.is_connected(): await client_to_clean.disconnect()
    client = TelegramClient(StringSession(), config.API_ID, config.API_HASH, **config.device_info)
    try:
        await client.connect()
        code_request = await client.send_code_request(phone_number)
        account_id_for_db_update = existing_account_id
        if current_state == "awaiting_member_account_number":
            existing_ids = [acc.get('account_id', 0) for acc in utils.get(db.get_user_data(uid), 'user_accounts', [])]
            account_id_for_db_update = max(existing_ids) + 1 if existing_ids else 1
            new_account_entry = {
                "account_id": account_id_for_db_update, "phone_number": phone_number,
                "session_string": None, "logged_in": False, "last_login_time": None,
                "daily_adds_count": 0, "soft_error_count": 0, "last_add_date": None,
                "is_active_for_adding": False, "is_banned_for_adding": False,
                "flood_wait_until": 0, "error_type": None,
                "temp_login_data": {'phash': code_request.phone_code_hash, 'sess': client.session.save(), 'clen': code_request.type.length}
            }
            db.update_user_data(uid, {"$push": {"user_accounts": new_account_entry}})
        else: # Re-login
            db.update_user_account_in_owner_doc(uid, account_id_for_db_update, {
                "temp_login_data": {'phash': code_request.phone_code_hash, 'sess': client.session.save(), 'clen': code_request.type.length}
            })
        db.update_user_data(uid, {"$set": {"state": f"awaiting_member_account_code_{account_id_for_db_update}"}})
        ONGOING_LOGIN_CLIENTS[uid] = client
        await processing_msg.delete()
        await e.respond(strings['ASK_OTP_PROMPT'], parse_mode='html')
    except Exception as ex:
        LOGGER.error(f"Code request failed for {phone_number}: {ex}", exc_info=True)
        await processing_msg.delete()
        if current_state == "awaiting_member_account_number" and 'account_id_for_db_update' in locals():
            db.update_user_data(uid, {"$pull": {"user_accounts": {"account_id": account_id_for_db_update}}})
        db.update_user_data(uid, {"$unset": {"state": 1}})
        if uid in ONGOING_LOGIN_CLIENTS:
            if client.is_connected(): await client.disconnect()
            del ONGOING_LOGIN_CLIENTS[uid]
        await e.respond(f"Failed to process number: {ex}", buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]])

async def _handle_delete_member_account(e, uid, account_id):
    owner_data = db.get_user_data(uid)
    tasks_using_account = [t for t in utils.get(owner_data, 'adding_tasks', []) if account_id in utils.get(t, 'assigned_accounts', []) and utils.get(t, 'is_active')]
    for task in tasks_using_account:
        await members_adder.pause_adding_task(utils.get(task, 'task_id'))
        await _bot_client_instance.send_message(uid, f"Task {utils.get(task, 'task_id')} paused as it used the deleted account `{account_id}`.", parse_mode='Markdown')
    db.users_db.update_many({"chat_id": uid}, {"$pull": {"adding_tasks.$.assigned_accounts": account_id}})
    db.update_user_data(uid, {"$pull": {"user_accounts": {"account_id": account_id}}})
    if account_id in members_adder.USER_CLIENTS:
        client = members_adder.USER_CLIENTS.pop(account_id)
        if client.is_connected(): await client.disconnect()
    await e.edit(f"✅ Account `{account_id}` deleted.", buttons=[[Button.inline("« Back", '{"action":"manage_member_accounts"}')]], parse_mode='Markdown')

# --- Registration Function ---
def register_all_handlers(bot_client_instance):
    global _bot_client_instance
    _bot_client_instance = bot_client_instance

    @_bot_client_instance.on(events.NewMessage(pattern=r"/start", func=lambda e: e.is_private))
    async def start_command_handler(e):
        s = await e.get_sender()
        if not db.get_user_data(s.id):
            db.users_db.insert_one({"chat_id": s.id, "fn": s.first_name, "un": s.username, "start_time": datetime.datetime.now(datetime.timezone.utc), "user_accounts": [], "adding_tasks": []})
        await menus.send_main_menu(e)

    # Command handlers: /help, /commands, /settings, etc.
    @_bot_client_instance.on(events.NewMessage(pattern=r"/help", func=lambda e: e.is_private))
    async def help_command_handler(e): await menus.send_help_menu(e)
    @_bot_client_instance.on(events.NewMessage(pattern=r"/commands", func=lambda e: e.is_private))
    async def commands_command_handler(e): await menus.send_commands_menu(e)
    @_bot_client_instance.on(events.NewMessage(pattern=r"/settings", func=lambda e: e.is_private))
    async def settings_command_handler(e): await menus.send_settings_menu(e)
    @_bot_client_instance.on(events.NewMessage(pattern=r"/addaccount", func=lambda e: e.is_private))
    async def add_member_account_command_handler(e): await handle_add_member_account_flow(e)
    @_bot_client_instance.on(events.NewMessage(pattern=r"/myaccounts", func=lambda e: e.is_private))
    async def my_member_accounts_command_handler(e): await menus.display_member_accounts(e, e.sender_id)
    @_bot_client_instance.on(events.NewMessage(pattern=r"/createtask", func=lambda e: e.is_private))
    async def create_adding_task_command_handler(e): await menus.send_create_adding_task_menu(e, e.sender_id)
    @_bot_client_instance.on(events.NewMessage(pattern=r"/managetasks", func=lambda e: e.is_private))
    async def manage_adding_tasks_command_handler(e): await menus.send_manage_adding_tasks_menu(e, e.sender_id)

    # Message handlers
    @_bot_client_instance.on(events.NewMessage(func=lambda e: e.is_private and e.contact))
    async def contact_handler(e):
        uid = e.sender_id
        owner_data = db.get_user_data(uid)
        state = utils.get(owner_data, 'state')
        if state and (state.startswith("awaiting_member_account_")):
            account_id_match = re.search(r'_(\d+)$', state)
            account_id = int(account_id_match.group(1)) if account_id_match else None
            phone_number = e.contact.phone_number.replace(" ", "")
            await _initiate_member_account_login_flow(e, uid, account_id, phone_number, state)
        else:
            await e.respond(strings['wrong_phone'])

    @_bot_client_instance.on(events.NewMessage(func=lambda e: e.is_private and e.text and not e.text.startswith('/')))
    async def private_message_handler(e):
        uid = e.sender_id
        owner_data = db.get_user_data(uid)
        if not owner_data: return
        state = utils.get(owner_data, 'state')

        if state == "awaiting_member_account_number":
            phone_numbers_raw = e.text.strip().split('\n')
            for phone_number_raw in phone_numbers_raw:
                phone_number = phone_number_raw.strip().replace(" ", "")
                if re.match(r"^\+?\d+$", phone_number):
                    await _initiate_member_account_login_flow(e, uid, None, phone_number, state)
                    break 
            return
        
        elif state and state.startswith("awaiting_member_account_code_"):
            await _handle_member_account_login_step(e, uid, int(state.split('_')[-1]), e.text)
        elif state and state.startswith("awaiting_member_account_password_"):
            await _handle_member_account_login_step(e, uid, int(state.split('_')[-1]), e.text)

        elif state and (state.startswith("awaiting_chat_input_source_") or state.startswith("awaiting_chat_input_target_")):
            is_source = "source" in state
            task_id = int(state.split('_')[-1])
            chat_inputs_raw = e.text.strip().split('\n')
            
            resolver_client = await _get_an_active_user_client(uid)
            if not resolver_client:
                await e.respond("You have no active member accounts to resolve chat links. Please add one first.")
                return

            if is_source and len(chat_inputs_raw) > 5: return await e.respond(strings['TOO_MANY_SOURCE_CHATS'])

            resolved_chat_ids = []
            all_chats_valid = True
            for chat_input in chat_inputs_raw:
                chat_input = chat_input.strip()
                if not chat_input: continue
                try:
                    peer_to_check = int(chat_input) if chat_input.lstrip('-').isdigit() else chat_input
                    entity = await resolver_client.get_entity(peer_to_check)
                    resolved_chat_ids.append(entity.id)
                    await _join_chat_with_all_accounts(e, uid, entity)
                except Exception as ex:
                    LOGGER.warning(f"User client could not resolve chat '{chat_input}': {ex}", exc_info=True)
                    await e.respond(strings['CHAT_NOT_FOUND_OR_ACCESSIBLE'].format(chat_input=chat_input))
                    all_chats_valid = False
                    break
            
            if all_chats_valid and resolved_chat_ids:
                if is_source:
                    db.update_task_in_owner_doc(uid, task_id, {"$set": {"adding_tasks.$.source_chat_ids": resolved_chat_ids}})
                    await e.respond(strings['TASK_SOURCE_SET'].format(task_id=task_id))
                else: # Target
                    db.update_task_in_owner_doc(uid, task_id, {"$set": {"adding_tasks.$.target_chat_id": resolved_chat_ids[0]}})
                    await e.respond(strings['TASK_TARGET_SET'].format(task_id=task_id))
                db.update_user_data(uid, {"$unset": {"state": 1}})
                await menus.send_adding_task_details_menu(e, uid, task_id)
        else:
            await e.respond("Please use the buttons or commands to interact with me.")

    @_bot_client_instance.on(events.CallbackQuery)
    async def main_callback_handler(e):
        uid = e.sender_id
        try:
            raw_data = e.data.decode()
        except (UnicodeDecodeError, AttributeError):
            raw_data = e.data

        if raw_data.startswith("yes_add_another_account_") or raw_data.startswith("no_add_another_account_"):
            if raw_data.startswith("yes_"):
                await e.answer("Initiating...")
                await handle_add_member_account_flow(e)
            else:
                await e.answer("Okay!")
                await menus.send_members_adding_menu(e, uid)
            return

        if raw_data.startswith("yes_delete_member_account_") or raw_data.startswith("no_delete_member_account_"):
            account_id = int(raw_data.split('_')[-1])
            if raw_data.startswith("yes_"):
                await _handle_delete_member_account(e, uid, account_id)
            else:
                await menus.send_member_account_details(e, uid, account_id)
            return

        if raw_data.startswith("yes_delete_adding_task_") or raw_data.startswith("no_delete_adding_task_"):
            task_id = int(raw_data.split('_')[-1])
            if raw_data.startswith("yes_"):
                await members_adder.pause_adding_task(task_id)
                db.update_user_data(uid, {"$pull": {"adding_tasks": {"task_id": task_id}}})
                await e.edit(strings['TASK_DELETED'].format(task_id=task_id), buttons=[[Button.inline("« Back", '{"action":"manage_adding_tasks"}')]])
            else:
                await menus.send_adding_task_details_menu(e, uid, task_id)
            return
            
        try:
            j = json.loads(raw_data)
            action = j.get('action')
        except (json.JSONDecodeError, AttributeError):
            await e.answer("Invalid action.")
            return

        if not action: return await e.answer()

        owner_data = db.get_user_data(uid)
        if not owner_data and action not in ["main_menu", "help", "commands", "retry_fsub"]:
            return await e.answer("Please /start the bot first.", alert=True)

        # Main Menu Navigation
        if action == "main_menu": await menus.send_main_menu(e)
        elif action == "help": await menus.send_help_menu(e)
        elif action == "commands": await menus.send_commands_menu(e)
        elif action == "settings": await menus.send_settings_menu(e)
        elif action == "retry_fsub":
            await e.delete()
            if await menus.check_fsub(e): await e.respond("Thanks for joining!")
        
        # Members Adding Menu
        elif action == "members_adding_menu": await menus.send_members_adding_menu(e, uid)
        elif action == "add_member_account": await handle_add_member_account_flow(e)
        elif action == "manage_member_accounts": await menus.display_member_accounts(e, uid)
        
        # Account Details Menu
        elif action == "member_account_details": await menus.send_member_account_details(e, uid, j.get('account_id'))
        elif action == "confirm_delete_member_account":
            account_id = j.get('account_id')
            await e.edit(f"Are you sure you want to delete account `{account_id}`?", buttons=menus.yesno(f"delete_member_account_{account_id}"), parse_mode='Markdown')
        elif action == "relogin_member_account":
            account_id = j.get('account_id')
            db.update_user_data(uid, {"$set": {"state": f"awaiting_member_account_relogin_phone_{account_id}"}})
            await e.edit(strings['ADD_ACCOUNT_NUMBER_PROMPT'], parse_mode='Markdown')
        elif action == "toggle_member_account_ban":
            account_id = j.get('account_id')
            acc_info = db.find_user_account_in_owner_doc(uid, account_id)
            if acc_info:
                new_status = not acc_info.get('is_banned_for_adding', False)
                db.update_user_account_in_owner_doc(uid, account_id, {"is_banned_for_adding": new_status})
                await e.answer(f"Ban status set to {new_status}", alert=True)
                await menus.send_member_account_details(e, uid, account_id)

        # Task Management Menu
        elif action == "create_adding_task": await menus.send_create_adding_task_menu(e, uid)
        elif action == "manage_adding_tasks": await menus.send_manage_adding_tasks_menu(e, uid)
        elif action == "m_add_task_menu": await menus.send_adding_task_details_menu(e, uid, j.get('task_id'))
        elif action == "set_task_source_chat":
            task_id = j.get('task_id')
            db.update_user_data(uid, {"$set": {"state": f"awaiting_chat_input_source_{task_id}"}})
            await e.edit(strings['ASK_SOURCE_CHAT_ID'], buttons=[[Button.inline("« Back", f'{{"action":"m_add_task_menu", "task_id":{task_id}}}')]], parse_mode='Markdown')
        elif action == "set_task_target_chat":
            task_id = j.get('task_id')
            db.update_user_data(uid, {"$set": {"state": f"awaiting_chat_input_target_{task_id}"}})
            await e.edit(strings['ASK_TARGET_CHAT_ID'], buttons=[[Button.inline("« Back", f'{{"action":"m_add_task_menu", "task_id":{task_id}}}')]], parse_mode='Markdown')
        
        # Task Actions
        elif action == "start_adding_task":
            task_id = j.get('task_id')
            task_info = db.get_task_in_owner_doc(uid, task_id)
            if not task_info.get('source_chat_ids') or not task_info.get('target_chat_id') or not task_info.get('assigned_accounts'):
                return await e.answer("Task is not fully configured (source/target/accounts).", alert=True)
            if await members_adder.start_adding_task(uid, task_id):
                await e.answer("Task started!", alert=True)
                await menus.send_adding_task_details_menu(e, uid, task_id)
            else: await e.answer("Failed to start task.", alert=True)
        elif action == "pause_adding_task":
            task_id = j.get('task_id')
            if await members_adder.pause_adding_task(task_id):
                await e.answer("Task paused!", alert=True)
                await menus.send_adding_task_details_menu(e, uid, task_id)
            else: await e.answer("Task was not running.", alert=True)
        elif action == "confirm_delete_adding_task":
            task_id = j.get('task_id')
            await e.edit(strings['TASK_DELETE_CONFIRM'].format(task_id=task_id), buttons=menus.yesno(f"delete_adding_task_{task_id}"), parse_mode='html')

        await e.answer()
