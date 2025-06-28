import logging
import json
import asyncio
import time
import datetime
import re

from telethon import TelegramClient, events, functions
from telethon.sessions import StringSession
from telethon.tl.custom.button import Button # For inline buttons
from telethon.tl.types import ReplyKeyboardMarkup, KeyboardButton, KeyboardButtonRequestPhone, ReplyKeyboardHide # Import these
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError, PasswordHashInvalidError,
    FloodWaitError, UserIsBlockedError, InputUserDeactivatedError,
    UserNotParticipantError, MessageNotModifiedError, PhoneCodeExpiredError,
    InviteHashExpiredError, InviteHashInvalidError, UserAlreadyParticipantError
)
from telethon.tl.functions.messages import ImportChatInviteRequest


from config import config
import db
import utils
import menus
import members_adder
from strings import strings

LOGGER = logging.getLogger(__name__)

_bot_client_instance = None

# CRITICAL FIX: Global dictionary to hold TelegramClient instances for ongoing login attempts
# Key: owner_uid, Value: TelegramClient instance
ONGOING_LOGIN_CLIENTS = {}

def set_bot_client_for_modules(client):
    """Sets the bot_client instance for handlers and other modules that need it."""
    global _bot_client_instance
    _bot_client_instance = client
    menus.set_bot_client(client)
    members_adder.set_bot_client(client)


# --- Helper functions for this module (handlers.py) ---

async def _resolve_chat_entity(owner_id, chat_input):
    """
    Tries to resolve a chat entity (ID, username, link) using the user's logged-in
    accounts first, and then the bot's own client as a fallback.
    It will also attempt to join the chat if a private invite link is provided.
    """
    owner_data = db.get_user_data(owner_id)
    if not owner_data:
        return None, "Owner data not found."

    clients_to_try = []
    # Prioritize user accounts as they can join private chats
    user_accounts = owner_data.get('user_accounts', [])
    for acc in user_accounts:
        if acc.get('logged_in') and acc.get('session_string'):
            client = await members_adder.get_user_client(acc.get('account_id'))
            if client:
                clients_to_try.append(client)
    
    # Fallback to the bot's own client
    clients_to_try.append(_bot_client_instance)

    for i, client in enumerate(clients_to_try):
        is_user_client = client != _bot_client_instance
        client_name = f"User Account Client {i+1}" if is_user_client else "Bot Client"
        
        try:
            # Handle private invite links (t.me/joinchat/... or t.me/+)
            if 'joinchat' in chat_input or '+' in chat_input:
                invite_hash = chat_input.split('/')[-1].replace('+', '')
                # Only user accounts can accept invites, not bots
                if is_user_client:
                    try:
                        updates = await client(ImportChatInviteRequest(invite_hash))
                        return updates.chats[0], None
                    except UserAlreadyParticipantError:
                        # If already in the chat, just get the entity
                        return await client.get_entity(chat_input), None
                    except (InviteHashExpiredError, InviteHashInvalidError):
                        LOGGER.warning(f"Invite link '{chat_input}' is invalid or expired when using {client_name}. Trying next client.")
                        continue
                else:
                    # Bots can't join private chats via invite links this way. Skip.
                    LOGGER.warning(f"Bot Client cannot process invite link '{chat_input}'. Skipping.")
                    continue

            # Handle public chats/channels or IDs
            entity = await client.get_entity(chat_input)
            return entity, None
        except Exception as ex:
            LOGGER.warning(f"Could not resolve '{chat_input}' with {client_name}. Error: {ex}")
            continue
    
    return None, f"Could not resolve or access the chat '{chat_input}' with any available account. Please ensure the link/username is correct and that at least one of your logged-in accounts can access it."

# Helper function for "Add Account" menu option (for member adding accounts)
async def handle_add_member_account_flow(e):
    uid = e.sender_id
    
    if uid in ONGOING_LOGIN_CLIENTS:
        client_to_clean = ONGOING_LOGIN_CLIENTS.pop(uid)
        if client_to_clean.is_connected():
            await client_to_clean.disconnect()
            LOGGER.info(f"Cleaned up stale login client for user {uid}")
    
    db.update_user_data(uid, {"$set": {"state": "awaiting_member_account_number"}})
    
    await e.respond(strings['ADD_ACCOUNT_NUMBER_PROMPT'], parse_mode='Markdown')


# Centralized helper for handling OTP/Password input and login attempt for member accounts
async def _handle_member_account_login_step(e, uid, account_id, input_text):
    owner_data = db.get_user_data(uid)
    
    client = ONGOING_LOGIN_CLIENTS.get(uid)
    if not client or not client.is_connected():
        LOGGER.warning(f"No active client found for user {uid} during login step. Resetting state.")
        db.update_user_data(uid, {"$set": {"state": None}})
        if account_id:
            db.update_user_data(uid, {"$pull": {"user_accounts": {"account_id": account_id}}})
        if uid in ONGOING_LOGIN_CLIENTS:
            del ONGOING_LOGIN_CLIENTS[uid]
        return await e.respond("Your login session expired or was interrupted. Please restart the account addition process.", buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='html')


    account_info_doc = db.users_db.find_one({"chat_id": uid, "user_accounts.account_id": account_id})
    account_info = next((acc for acc in utils.get(account_info_doc, 'user_accounts', []) if utils.get(acc, 'account_id') == account_id), None)
    
    if not account_info or not utils.get(account_info, 'temp_login_data'):
        LOGGER.warning(f"Account info or temp_login_data missing for account_id {account_id} for user {uid}. Resetting state.")
        db.update_user_data(uid, {"$set": {"state": None}})
        if uid in ONGOING_LOGIN_CLIENTS:
            del ONGOING_LOGIN_CLIENTS[uid]
        if client.is_connected(): await client.disconnect()
        if account_id: db.update_user_data(uid, {"$pull": {"user_accounts": {"account_id": account_id}}})
        return await e.respond("Your login data is invalid. Please restart account addition.", buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='html')
    
    temp_login_data = utils.get(account_info, 'temp_login_data')
    phone_number = utils.get(temp_login_data, 'ph')
    phone_code_hash = utils.get(temp_login_data, 'phash')
    
    processing_msg = await e.respond("Please wait... Validating your input.", parse_mode='html')
    await asyncio.sleep(0.5)

    try:
        if utils.get(owner_data, 'state').startswith("awaiting_member_account_code_"):
            otp_code = input_text.strip().replace(" ", "")
            
            if utils.get(temp_login_data, 'clen') and utils.get(temp_login_data, 'clen') != 0 and len(otp_code) != utils.get(temp_login_data, 'clen'):
                await processing_msg.delete()
                return await e.respond(strings['code_invalid'] + "\n" + strings['ASK_OTP_PROMPT'], parse_mode='html', link_preview=False)

            await client.sign_in(phone=phone_number, code=otp_code, phone_code_hash=phone_code_hash)
            
        elif utils.get(owner_data, 'state').startswith("awaiting_member_account_password_"):
            password = input_text.strip()
            await client.sign_in(password=password)

        db.update_user_account_in_owner_doc(
            uid, account_id,
            {
                "session_string": client.session.save(), 
                "logged_in": True,
                "last_login_time": time.time(),
                "is_active_for_adding": True,
                "temp_login_data": {},
            }
        )
        db.update_user_data(uid, {"$unset": {"state": 1}})
        
        members_adder.USER_CLIENTS[account_id] = client
        if uid in ONGOING_LOGIN_CLIENTS:
            del ONGOING_LOGIN_CLIENTS[uid]

        await processing_msg.delete()
        await e.respond(strings['ACCOUNT_ADDED_SUCCESS'].format(phone_number=phone_number, account_id=account_id), parse_mode='html')
        await e.respond(strings['ADD_ANOTHER_ACCOUNT_PROMPT'], buttons=menus.yesno(f"add_another_account_{account_id}"), parse_mode='html')

    except SessionPasswordNeededError:
        LOGGER.info(f"2FA required for account {account_id}.")
        await processing_msg.delete()
        db.update_user_data(uid, {"$set": {"state": f"awaiting_member_account_password_{account_id}"}})
        await e.respond(strings['ASK_PASSWORD_PROMPT'], parse_mode='html')
    except (PhoneCodeInvalidError, PasswordHashInvalidError, PhoneCodeExpiredError) as ex:
        LOGGER.warning(f"Login failed for account {account_id} with invalid credentials: {ex}")
        await processing_msg.delete()
        
        if isinstance(ex, PhoneCodeExpiredError):
            error_message = f"{strings['code_invalid']} (Code Expired)"
        elif isinstance(ex, PhoneCodeInvalidError):
            error_message = strings['code_invalid']
        else:
            error_message = strings['pass_invalid']

        db.update_user_data(uid, {"$pull": {"user_accounts": {"account_id": account_id}}})
        db.update_user_data(uid, {"$unset": {"state": 1}})

        if uid in ONGOING_LOGIN_CLIENTS:
            if client.is_connected():
                await client.disconnect()
            del ONGOING_LOGIN_CLIENTS[uid]

        await e.respond(f"{error_message}\n\nPlease restart the account login process from the /settings menu.", buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='html')
    except Exception as ex:
        LOGGER.error(f"Critical error during member account login for {account_id}: {ex}", exc_info=True)
        await processing_msg.delete()

        db.update_user_data(uid, {"$pull": {"user_accounts": {"account_id": account_id}}})
        db.update_user_data(uid, {"$unset": {"state": 1}})
        
        if uid in ONGOING_LOGIN_CLIENTS:
            if client.is_connected():
                await client.disconnect()
            del ONGOING_LOGIN_CLIENTS[uid]
        
        await e.respond(strings['ACCOUNT_LOGIN_FAILED'].format(error_message=ex), buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='html')
    finally:
        pass


# Helper for initial phone request or re-login phone request
async def _initiate_member_account_login_flow(e, uid, existing_account_id, phone_number, current_state):
    processing_msg = await e.respond(f"Attempting to log in account: **`{phone_number}`**\n\nPlease wait...", buttons=ReplyKeyboardHide(), parse_mode='Markdown')
    
    if uid in ONGOING_LOGIN_CLIENTS:
        client_to_clean = ONGOING_LOGIN_CLIENTS.pop(uid)
        if client_to_clean.is_connected():
            await client_to_clean.disconnect()
            LOGGER.info(f"Cleaned up stale client before new code request for user {uid}")

    client = TelegramClient(StringSession(), config.API_ID, config.API_HASH, **config.device_info)
    account_id_for_db_update = None
    try:
        await client.connect()
        code_request = await client.send_code_request(phone_number)

        account_id_for_db_update = existing_account_id

        if current_state == "awaiting_member_account_number":
            existing_account_ids = [utils.get(acc, 'account_id') for acc in utils.get(db.get_user_data(uid), 'user_accounts', []) if utils.get(acc, 'account_id')]
            new_account_id = 1
            if existing_account_ids:
                new_account_id = max(existing_account_ids) + 1
            account_id_for_db_update = new_account_id

            new_account_entry = {
                "account_id": account_id_for_db_update,
                "phone_number": phone_number,
                "session_string": None,
                "logged_in": False,
                "last_login_time": None,
                "daily_adds_count": 0,
                "soft_error_count": 0,
                "last_add_date": None,
                "is_active_for_adding": False,
                "is_banned_for_adding": False,
                "flood_wait_until": 0,
                "error_type": None,
                "temp_login_data": {
                    'phash': code_request.phone_code_hash,
                    'sess': client.session.save(),
                    'clen': code_request.type.length,
                    'code_ok': False,
                    'need_pass': False
                }
            }
            db.update_user_data(uid, {"$push": {"user_accounts": new_account_entry}})

        elif current_state.startswith("awaiting_member_account_relogin_phone_"):
            db.update_user_account_in_owner_doc(uid, account_id_for_db_update,
                {
                    "temp_login_data": {
                        'phash': code_request.phone_code_hash,
                        'sess': client.session.save(),
                        'clen': code_request.type.length,
                        'code_ok': False,
                        'need_pass': False
                    }
                }
            )
        
        db.update_user_data(uid, {"$set": {"state": f"awaiting_member_account_code_{account_id_for_db_update}"}})
        
        ONGOING_LOGIN_CLIENTS[uid] = client
        
        await processing_msg.delete()
        await e.respond(strings['ASK_OTP_PROMPT'], parse_mode='html', link_preview=False)

    except Exception as ex:
        LOGGER.error(f"Error during phone number input processing (code request failed): {ex}", exc_info=True)
        await processing_msg.delete()

        if current_state == "awaiting_member_account_number" and account_id_for_db_update:
            db.update_user_data(uid, {"$pull": {"user_accounts": {"account_id": account_id_for_db_update}}})
        
        db.update_user_data(uid, {"$set": {"state": None}})
        
        if uid in ONGOING_LOGIN_CLIENTS:
            if client.is_connected():
                await client.disconnect()
            del ONGOING_LOGIN_CLIENTS[uid]
        
        await e.respond(f"Failed to process account: {ex}", buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='html')
    finally:
        pass


# Helper for deleting member account
async def _handle_delete_member_account(e, uid, account_id):
    owner_data = db.get_user_data(uid)
    tasks_using_account = [t for t in utils.get(owner_data, 'adding_tasks', []) if account_id in utils.get(t, 'assigned_accounts', []) and utils.get(t, 'is_active')]
    for task in tasks_using_account:
        await members_adder.pause_adding_task(utils.get(task, 'task_id'))
        await _bot_client_instance.send_message(uid, f"Task {utils.get(task, 'task_id')} was using account <code>{account_id}</code> and has been paused.", parse_mode='html')

    db.users_db.update_many(
        {"chat_id": uid, "adding_tasks.assigned_accounts": account_id},
        {"$pull": {"adding_tasks.$.assigned_accounts": account_id}}
    )
    db.users_db.update_one(
        {"chat_id": uid},
        {"$pull": {"user_accounts": {"account_id": account_id}}}
    )

    if account_id in members_adder.USER_CLIENTS:
        client = members_adder.USER_CLIENTS.pop(account_id)
        if client.is_connected():
            await client.disconnect()

    await e.edit(f"✅ Account <code>{account_id}</code> deleted successfully.", buttons=[[Button.inline("« Back", '{"action":"manage_member_accounts"}')]], parse_mode='html')


# --- Registration Function ---
def register_all_handlers(bot_client_instance):
    """Registers all message and callback query handlers with the bot_client instance."""
    set_bot_client_for_modules(bot_client_instance)

    # --- Command Handlers ---
    @_bot_client_instance.on(events.NewMessage(pattern=r"/start", func=lambda e: e.is_private))
    async def start_command_handler(e):
        s = await e.get_sender()
        if not db.get_user_data(s.id):
            db.users_db.insert_one({
                "chat_id":s.id, "fn":s.first_name, "un":s.username,
                "start_time":datetime.datetime.now(datetime.timezone.utc),
                "is_banned_from_dl": False,
                "user_accounts": [],
                "adding_tasks": []
            })
        await menus.send_main_menu(e)

    @_bot_client_instance.on(events.NewMessage(pattern=r"/help", func=lambda e: e.is_private))
    async def help_command_handler(e):
        await menus.send_help_menu(e)

    @_bot_client_instance.on(events.NewMessage(pattern=r"/commands", func=lambda e: e.is_private))
    async def commands_command_handler(e):
        await menus.send_commands_menu(e)

    @_bot_client_instance.on(events.NewMessage(pattern=r"/settings", func=lambda e: e.is_private))
    async def settings_command_handler(e):
        await menus.send_settings_menu(e)

    @_bot_client_instance.on(events.NewMessage(pattern=r"/addaccount", func=lambda e: e.is_private))
    async def add_member_account_command_handler(e):
        await handle_add_member_account_flow(e)

    @_bot_client_instance.on(events.NewMessage(pattern=r"/myaccounts", func=lambda e: e.is_private))
    async def my_member_accounts_command_handler(e):
        await menus.display_member_accounts(e, e.sender_id)

    @_bot_client_instance.on(events.NewMessage(pattern=r"/createtask", func=lambda e: e.is_private))
    async def create_adding_task_command_handler(e):
        await menus.send_create_adding_task_menu(e, e.sender_id)

    @_bot_client_instance.on(events.NewMessage(pattern=r"/managetasks", func=lambda e: e.is_private))
    async def manage_adding_tasks_command_handler(e):
        await menus.send_manage_adding_tasks_menu(e, e.sender_id)

    @_bot_client_instance.on(events.NewMessage(pattern="/stats", from_users=config.OWNER_ID))
    async def stats_handler(e):
        total_bot_users = db.users_db.count_documents({});
        
        total_member_accounts = sum(len(doc.get('user_accounts', [])) for doc in db.users_db.find({}))
        active_adding_tasks_count = len(members_adder.ACTIVE_ADDING_TASKS)
        
        st = (f"<b>Bot Statistics</b>\n\n"
              f"👤 <b>Total Bot Users:</b> <code>{total_bot_users}</code>\n"
              f"👥 <b>Total Member Adding Accounts:</b> <code>{total_member_accounts}</code>\n"
              f"⚙️ <b>Active Adding Tasks:</b> <code>{active_adding_tasks_count}</code>"
        )
        await e.respond(st, parse_mode='html')

    @_bot_client_instance.on(events.NewMessage(pattern="/broadcast", from_users=config.OWNER_ID))
    async def owner_broadcast_handler(e):
        r = await e.get_reply_message()
        if not r:
            await e.respond("Please reply to a message to broadcast."); return
        
        db.update_user_data(e.sender_id, {"$set": {"state": "awaiting_broadcast_message"}})
        await e.respond(strings['BROADCAST_MENU_TEXT'], buttons=[[Button.inline("🛑 Cancel Broadcast", '{"action":"cancel_broadcast"}')]], parse_mode='html')

    @_bot_client_instance.on(events.NewMessage(pattern=r"^(?:https?://t\.me/(c/)?(\w+)/(\d+)|(-?\d+)\.(\d+))$", func=lambda e: e.is_private))
    async def link_handler(e):
        if not await menus.check_fsub(e): return
        owner_data = db.get_user_data(e.sender_id)
        if utils.get(owner_data, 'is_banned_from_dl', False):
            await e.respond(strings['USER_BANNED_MESSAGE'], parse_mode='html')
            return
        await e.respond("This bot is dedicated to member adding. Please use the menu or commands like /addaccount to manage accounts.")

    @_bot_client_instance.on(events.NewMessage(func=lambda e: e.is_private and e.contact))
    async def contact_handler(e):
        uid = e.sender_id
        owner_data = db.get_user_data(uid)
        state = utils.get(owner_data, 'state')

        if state and (state.startswith("awaiting_member_account_relogin_phone_") or state.startswith("awaiting_member_account_number")):
            account_id_match = re.search(r'_(\d+)$', state)
            account_id = int(account_id_match.group(1)) if account_id_match else None
            
            phone_number = e.contact.phone_number.replace(" ", "")

            await e.respond("Processing your request...", buttons=ReplyKeyboardHide(), parse_mode='html')

            if state == "awaiting_member_account_number" and any(acc.get('phone_number') == phone_number for acc in utils.get(owner_data, 'user_accounts', [])):
                db.update_user_data(uid, {"$set": {"state": None}})
                return await e.respond(strings['ACCOUNT_ALREADY_ADDED'].format(phone_number=phone_number), buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='html')

            await _initiate_member_account_login_flow(e, uid, account_id, phone_number, state)
            
        else:
            await e.respond(strings['wrong_phone'], parse_mode='html')

    @_bot_client_instance.on(events.NewMessage(func=lambda e: e.is_private and e.text and not e.text.startswith('/')))
    async def private_message_handler(e):
        uid = e.sender_id
        owner_data = db.get_user_data(uid)
        if not owner_data: return
        
        state = utils.get(owner_data, 'state')

        if state == "awaiting_member_account_number":
            phone_numbers_raw = e.text.strip().split('\n')
            
            for phone_number_raw in phone_numbers_raw:
                phone_number = phone_number_raw.replace(" ", "")
                if not phone_number.strip() or not re.match(r"^\+\d{10,15}$", phone_number):
                    await e.respond(f"Skipping invalid phone number: `{phone_number_raw}`. Please use international format `+<countrycode><number>`.", parse_mode='html')
                    continue

                if any(acc.get('phone_number') == phone_number for acc in utils.get(owner_data, 'user_accounts', [])):
                    await e.respond(strings['ACCOUNT_ALREADY_ADDED'].format(phone_number=phone_number), parse_mode='html')
                    continue
                
                await _initiate_member_account_login_flow(e, uid, None, phone_number, state)
                
                break 
            
            if not any(re.match(r"^\+\d{10,15}$", p.replace(" ", "")) for p in phone_numbers_raw if p.strip()):
                await e.respond("No valid phone numbers provided. Please send numbers in international format, one per line.", parse_mode='html')
            
            return

        elif state == "awaiting_broadcast_message":
            db.update_user_data(uid, {"$set": {"state": None}})
            asyncio.create_task(members_adder.run_user_broadcast(uid, e.message))
        elif state and state.startswith("awaiting_member_account_code_"):
            account_id = int(state.split('_')[-1])
            await _handle_member_account_login_step(e, uid, account_id, e.text)
        elif state and state.startswith("awaiting_member_account_password_"):
            account_id = int(state.split('_')[-1])
            await _handle_member_account_login_step(e, uid, account_id, e.text)
        
        elif state and state.startswith("awaiting_add_source_chat_"):
            task_id = int(state.split('_')[-1])
            chat_inputs_raw = e.text.strip().split('\n')
            
            task = db.get_task_in_owner_doc(uid, task_id)
            if not task:
                db.update_user_data(uid, {"$unset": {"state": 1}})
                return await e.respond("Task not found. It might have been deleted.")

            source_chat_ids = task.get('source_chat_ids', [])
            
            processing_msg = await e.respond("Validating and adding source chat(s)... Please wait.", parse_mode='Markdown')
            
            added_count = 0
            failed_chats = []
            for chat_input_raw in chat_inputs_raw:
                chat_input = chat_input_raw.strip()
                if not chat_input: continue

                entity, error_msg = await _resolve_chat_entity(uid, chat_input)
                
                if entity:
                    if entity.id not in source_chat_ids:
                        source_chat_ids.append(entity.id)
                        added_count += 1
                else:
                    failed_chats.append(chat_input)

            # Trim the list to the latest 5 if it exceeds the limit
            if len(source_chat_ids) > 5:
                source_chat_ids = source_chat_ids[-5:]
            
            db.update_task_in_owner_doc(uid, task_id, {"$set": {"adding_tasks.$.source_chat_ids": source_chat_ids}})
            
            db.update_user_data(uid, {"$unset": {"state": 1}})
            await processing_msg.delete()
            
            response_msg = f"Successfully added {added_count} new source chat(s) to Task {task_id}."
            if failed_chats:
                response_msg += f"\n\nCould not add the following:\n- `{'`\n- `'.join(failed_chats)}`"
                response_msg += "\nPlease check the links/IDs and ensure your accounts can access them."

            await e.respond(response_msg, parse_mode='Markdown')
            await menus.send_adding_task_details_menu(e, uid, task_id)

        elif state and state.startswith("awaiting_chat_input_target_"):
            task_id = int(state.split('_')[-1])
            chat_input = e.text.strip()
            
            if not chat_input:
                return await e.respond("Please provide a single target chat ID, username, or invite link.", parse_mode='Markdown')
            
            processing_msg = await e.respond("Validating target chat... Please wait.", parse_mode='Markdown')

            entity, error_msg = await _resolve_chat_entity(uid, chat_input)

            if entity:
                target_chat_id = entity.id
                db.update_task_in_owner_doc(uid, task_id, {"$set": {"adding_tasks.$.target_chat_id": target_chat_id}})
                db.update_user_data(uid, {"$unset": {"state": 1}})
                await processing_msg.delete()
                await e.respond(strings['TASK_TARGET_SET'].format(task_id=task_id), parse_mode='Markdown')
                await menus.send_adding_task_details_menu(e, uid, task_id)
            else:
                await processing_msg.delete()
                await e.respond(error_msg, parse_mode='Markdown')
                return

        else:
            await e.respond("I'm a dedicated member adding bot! Please use the commands or buttons to interact with me. Use /help for more info.")


    @_bot_client_instance.on(events.NewMessage(func=lambda e: e.is_private and e.media))
    async def private_media_handler(e):
        uid = e.sender_id
        owner_data = db.get_user_data(uid)
        if not owner_data: return

        state = utils.get(owner_data, 'state')
        if uid == config.OWNER_ID and state == "awaiting_tutorial_video" and e.video:
            db.update_user_data(uid, {"$set": {"state": None}})
            db.bot_settings_db.update_one(
                {'setting': 'tutorial'},
                {'$set': {'message_id': e.message.id}},
                upsert=True
            )
            await e.respond(strings['TUTORIAL_SET_SUCCESS'], parse_mode='html')
        elif state == "awaiting_broadcast_message":
            db.update_user_data(uid, {"$set": {"state": None}})
            asyncio.create_task(members_adder.run_user_broadcast(uid, e.message))
        else:
            await e.respond("I'm a dedicated member adding bot. I don't handle media messages outside of specific flows.")


    @_bot_client_instance.on(events.NewMessage(pattern=r"/empty", func=lambda e: e.is_private))
    async def empty_handler(e):
        uid = e.sender_id
        owner_data = db.get_user_data(uid)
        if not owner_data: return
        state = utils.get(owner_data, 'state')
        if uid == config.OWNER_ID and state == "awaiting_tutorial_video":
            db.update_user_data(uid, {"$set": {"state": None}})
            db.bot_settings_db.delete_one({'setting': 'tutorial'})
            await e.respond(strings['TUTORIAL_REMOVED_SUCCESS'], parse_mode='html')
        else:
            await e.respond("This command is not available in the current context.")

    @_bot_client_instance.on(events.CallbackQuery)
    async def main_callback_handler(e):
        uid = e.sender_id
        raw_data = e.data.decode()
        owner_data = db.get_user_data(uid)

        # BUG FIX: Robust callback handling. Try JSON first, then simple strings.
        try:
            # Handle JSON-based callbacks
            j = json.loads(raw_data)
            action = j.get('action')

            if not action:
                return await e.answer("Invalid callback (no action).")

            if not owner_data and action not in ["main_menu", "help", "commands", "retry_fsub"]:
                return await e.answer("User data not found. Please /start again.", alert=True)
            
            # --- All JSON actions are handled below ---

            if action == "m_add_addsource":
                task_id = j.get('task_id')
                prompt_key = 'ASK_ADD_SOURCE_CHAT_ID'
                db.update_user_data(uid, {"$set": {"state": f"awaiting_add_source_chat_{task_id}"}})
                await e.edit(strings[prompt_key], buttons=[[Button.inline("« Back", f'{{"action":"m_add_task_menu","task_id":{task_id}}}')]], parse_mode='Markdown')

            elif action == "m_add_clearsource":
                task_id = j.get('task_id')
                db.update_task_in_owner_doc(uid, task_id, {"$set": {"adding_tasks.$.source_chat_ids": []}})
                await e.answer("Source chats cleared successfully!", alert=True)
                await menus.send_adding_task_details_menu(e, uid, task_id)

            elif action == "m_add_settarget":
                task_id = j.get('task_id')
                prompt_key = 'ASK_TARGET_CHAT_ID'
                db.update_user_data(uid, {"$set": {"state": f"awaiting_chat_input_target_{task_id}"}})
                await e.edit(strings[prompt_key], buttons=[[Button.inline("« Back", f'{{"action":"m_add_task_menu","task_id":{task_id}}}')]], parse_mode='Markdown')
            
            elif action == "ban_dl":
                if e.sender_id != config.OWNER_ID:
                    return await e.answer("You are not authorized to do this.", alert=True)
                user_id_to_ban = j.get('user_id')
                db.users_db.update_one({"chat_id": user_id_to_ban}, {"$set": {"is_banned_from_dl": True}}, upsert=True)
                await e.edit("✅ User has been **banned**.")
            
            elif action == "main_menu": await menus.send_main_menu(e)
            elif action == "help": await menus.send_help_menu(e)
            elif action == "commands": await menus.send_commands_menu(e)
            elif action == "settings": await menus.send_settings_menu(e)
            elif action == "members_adding_menu": await menus.send_members_adding_menu(e, uid)
            elif action == "add_member_account": await handle_add_member_account_flow(e)
            elif action == "manage_member_accounts": await menus.display_member_accounts(e, uid)
            elif action == "create_adding_task": await menus.send_create_adding_task_menu(e, uid)
            elif action == "manage_adding_tasks": await menus.send_manage_adding_tasks_menu(e, uid)
            elif action == "user_broadcast":
                db.update_user_data(uid, {"$set": {"state": "awaiting_broadcast_message"}})
                await e.edit(strings['BROADCAST_MENU_TEXT'], buttons=[[Button.inline("🛑 Cancel Broadcast", '{"action":"cancel_broadcast"}')]], parse_mode='html')
            elif action == "cancel_broadcast":
                db.update_user_data(uid, {"$set": {"state": None}})
                await e.edit(strings['BROADCAST_CANCELLED'], buttons=[[Button.inline("« Back to Settings", '{"action":"settings"}')]], parse_mode='html')
            
            elif action == "member_account_details":
                await menus.send_member_account_details(e, uid, j.get('account_id'))
            elif action == "confirm_delete_member_account":
                await e.edit(f"Are you sure you want to delete account <code>{j.get('account_id')}</code>?", buttons=menus.yesno(f"delete_member_account_{j.get('account_id')}"), parse_mode='html')
            elif action == "relogin_member_account":
                db.update_user_data(uid, {"$set": {"state": f"awaiting_member_account_relogin_phone_{j.get('account_id')}"}})
                await e.edit(strings['ADD_ACCOUNT_NUMBER_PROMPT'], parse_mode='Markdown')
            elif action == "toggle_member_account_ban":
                account_id = j.get('account_id')
                account_info = db.find_user_account_in_owner_doc(uid, account_id)
                if account_info:
                    new_ban_status = not utils.get(account_info, 'is_banned_for_adding', False)
                    db.update_user_account_in_owner_doc(uid, account_id, {"is_banned_for_adding": new_ban_status})
                    await e.answer(f"Account ban status set to {new_ban_status}.", alert=True)
                    await menus.send_member_account_details(e, uid, account_id)
            
            elif action == "m_add_task_menu":
                await menus.send_adding_task_details_menu(e, uid, j.get('task_id'))
            elif action == "start_adding_task":
                task_id = j.get('task_id')
                if await members_adder.start_adding_task(uid, task_id):
                    await e.answer(strings['TASK_STARTING'].format(task_id=task_id), alert=True)
                    await menus.send_adding_task_details_menu(e, uid, task_id)
                else:
                    await e.answer("Failed to start task. Ensure it's fully configured.", alert=True)
            elif action == "pause_adding_task":
                task_id = j.get('task_id')
                if await members_adder.pause_adding_task(task_id):
                    await e.answer(strings['TASK_PAUSING'].format(task_id=task_id), alert=True)
                    await menus.send_adding_task_details_menu(e, uid, task_id)
            elif action == "confirm_delete_adding_task":
                task_id = j.get('task_id')
                await e.edit(strings['TASK_DELETE_CONFIRM'].format(task_id=task_id), buttons=menus.yesno(f"delete_adding_task_{task_id}"), parse_mode='html')
            
            elif action == "noop": return await e.answer()
            else: return await e.answer("Action not implemented yet.")

        except json.JSONDecodeError:
            # Handle simple string-based callbacks (legacy, e.g., yesno)
            action = raw_data

            if action.startswith("yes_add_another_account_"):
                db.update_user_data(uid, {"$set": {"state": None}})
                await e.answer("Initiating another account addition...", alert=True)
                await handle_add_member_account_flow(e)
            elif action.startswith("no_add_another_account_"):
                db.update_user_data(uid, {"$set": {"state": None}})
                await e.edit("Okay, no more accounts for now.", buttons=None)
                await menus.send_members_adding_menu(e, uid)
            elif action.startswith("yes_delete_member_account_"):
                account_id = int(action.split('_')[-1])
                await _handle_delete_member_account(e, uid, account_id)
            elif action.startswith("no_delete_member_account_"):
                account_id = int(action.split('_')[-1])
                await menus.send_member_account_details(e, uid, account_id)
            elif action.startswith("yes_delete_adding_task_"):
                task_id = int(action.split('_')[-1])
                await members_adder.pause_adding_task(task_id) # Ensure it's stopped first
                db.update_user_data(uid, {"$pull": {"adding_tasks": {"task_id": task_id}}})
                await e.edit(strings['TASK_DELETED'].format(task_id=task_id), buttons=[[Button.inline("« Back", '{"action":"manage_adding_tasks"}')]], parse_mode='html')
            elif action.startswith("no_delete_adding_task_"):
                task_id = int(action.split('_')[-1])
                await menus.send_adding_task_details_menu(e, uid, task_id)
            else:
                await e.answer("Unknown or outdated button clicked.")
