import logging
import json
import asyncio
import time
import datetime
import re

from telethon import TelegramClient, events, functions
from telethon.sessions import StringSession
from telethon.tl.custom.button import Button
from telethon.tl.types import ReplyKeyboardMarkup, KeyboardButtonRequestPhone # Import ReplyKeyboardMarkup and KeyboardButtonRequestPhone
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError, PasswordHashInvalidError,
    FloodWaitError, UserIsBlockedError, InputUserDeactivatedError,
    UserNotParticipantError, MessageNotModifiedError, PeerFloodError
)

from config import config
import db
import utils
import menus
import members_adder
from strings import strings

LOGGER = logging.getLogger(__name__)

_bot_client_instance = None

def set_bot_client_for_modules(client):
    """Sets the bot_client instance for handlers and other modules that need it."""
    global _bot_client_instance
    _bot_client_instance = client
    menus.set_bot_client(client)
    members_adder.set_bot_client(client)


# --- Helper functions for this module (handlers.py) ---

# Helper function for main bot user login flow
async def _handle_main_bot_user_login(contact_obj, event_obj):
    # For requesting phone, use ReplyKeyboardMarkup
    request_phone_button = ReplyKeyboardMarkup(
        [[KeyboardButtonRequestPhone(strings['ask_phone_button'])]], # Use the string for the button text
        resize=True,
        one_time=True
    )
    # Send a new message with the reply keyboard
    m = await event_obj.respond(strings['ask_phone_prompt'], buttons=request_phone_button, parse_mode='html') # New string needed

    # The actual login process will continue in the contact_handler.
    # The current message (m) won't be edited by this function directly.
    
    # We might need to delete this temporary message later or rely on Telethon to remove reply keyboards
    # after the contact is sent. For a cleaner UI, we'll ensure the next step edits or sends new inline menu.

# Helper for main bot sign in (called from callback query)
async def sign_in_main_bot(e):
    owner_data = db.get_user_data(e.chat_id)
    login_data = json.loads(utils.get(owner_data,'login','{}'))
    s = {}
    
    if not owner_data: await e.edit("Error: User data not found. Please /start again."); return False
    
    try:
        if utils.get(owner_data,'logged_in'):
            await e.edit(strings['already_logged_in'], buttons=None, parse_mode='html')
            return True
        
        u = TelegramClient(StringSession(utils.get(login_data,'sess')), config.API_ID, config.API_HASH, **config.device_info)
        await u.connect()
        
        password = utils.get(owner_data, 'password')
        phone_number = utils.get(owner_data, 'ph')
        
        if utils.get(login_data,'code_ok') and utils.get(login_data,'pass_ok'):
            await u.sign_in(password=password)
        elif utils.get(login_data,'code_ok'):
            await u.sign_in(phone=phone_number, code=utils.get(login_data, 'code'), phone_code_hash=utils.get(login_data,'phash'))
        else:
            return False
        
        s.update({'session':u.session.save(),'logged_in':True,'login':'{}'})
        await e.edit(strings['LOGIN_SUCCESS_TEXT'], buttons=None, parse_mode='html')
        await _bot_client_instance.send_message(e.chat_id, "Login successful! You can now use the bot's features.")
    except SessionPasswordNeededError:
        login_data['need_pass'] = True
        await e.edit(strings['ask_pass'],buttons=None, parse_mode='html')
        s['login'] = json.dumps(login_data)
        return False
    except (PhoneCodeInvalidError,PasswordHashInvalidError) as ex:
        numpad=[[Button.inline(str(i),f'{{"press":{i}}}')for i in range(j,j+3)]for j in range(1,10,3)];numpad.append([Button.inline("Clear All",'{"press":"clear_all"}'),Button.inline("0",'{"press":0}'),Button.inline("⌫",'{"press":"clear"}')])
        errk='code' if isinstance(ex,PhoneCodeInvalidError) else 'pass'
        login_data.update({'code':'','code_ok':False} if errk=='code' else {'pass_ok':False})
        
        await e.edit(strings[f'{errk}_invalid'], parse_mode='html')
        await asyncio.sleep(2)
        await e.edit(strings[f'ask_{errk}'],buttons=numpad if errk=='code' else None, parse_mode='html', link_preview=False)
        s['login'] = json.dumps(login_data)
        return False
    except Exception as ex:
        LOGGER.error(f"Unexpected error during main bot login: {ex}")
        await e.edit(f"Unexpected error: {ex}")
        s['login'] = '{}'
        return False
    finally:
        if s: db.update_user_data(owner_data['chat_id'], {'$set':s})
        if u and u.is_connected(): await u.disconnect()
    return utils.get(s,'logged_in',False)

# Handler for "Add Account" menu option
async def handle_add_member_account_flow(e):
    uid = e.sender_id
    db.update_user_data(uid, {"$set": {"state": "awaiting_member_account_phone"}})
    
    # CRITICAL FIX FOR: ValueError: You cannot mix inline with normal buttons
    # Send *only* the request_phone button as a reply keyboard
    request_phone_markup = ReplyKeyboardMarkup(
        [[KeyboardButtonRequestPhone(strings['share_phone_number_button'])]], # New string for button text
        resize=True,
        one_time=True
    )
    await e.respond(strings['ADD_ACCOUNT_PROMPT'], buttons=request_phone_markup, parse_mode='html')
    # We no longer send an inline cancel button with the phone request.
    # User can just ignore or send another command to cancel.

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
    global _bot_client_instance
    _bot_client_instance = bot_client_instance

    # --- Command Handlers ---
    @_bot_client_instance.on(events.NewMessage(pattern=r"/start", func=lambda e: e.is_private))
    async def start_command_handler(e):
        s = await e.get_sender()
        if not db.get_user_data(s.id):
            db.users_db.insert_one({
                "chat_id":s.id, "fn":s.first_name, "un":s.username,
                "start_time":datetime.datetime.now(datetime.timezone.utc),
                "logged_in": False, "state": None,
                "is_banned_from_dl": False,
                "user_accounts": [],
                "adding_tasks": []
            })
        await menus.send_start_menu(e) # This will handle sending a new message or editing appropriately

    @_bot_client_instance.on(events.NewMessage(pattern=r"/help", func=lambda e: e.is_private))
    async def help_command_handler(e):
        await menus.send_help_menu(e)

    @_bot_client_instance.on(events.NewMessage(pattern=r"/commands", func=lambda e: e.is_private))
    async def commands_command_handler(e):
        await menus.send_commands_menu(e)

    @_bot_client_instance.on(events.NewMessage(pattern=r"/settings", func=lambda e: e.is_private))
    async def settings_command_handler(e):
        owner_data = db.get_user_data(e.sender_id)
        if not utils.get(owner_data, 'logged_in'):
            await e.respond(strings['need_login'], parse_mode='html')
        else:
            await menus.send_settings_menu(e)

    @_bot_client_instance.on(events.NewMessage(pattern=r"/login", func=lambda e: e.is_private))
    async def login_command_handler(e):
        owner_data = db.get_user_data(e.sender_id)
        if utils.get(owner_data, 'logged_in'):
            await e.respond(strings['already_logged_in'], parse_mode='html')
        else:
            # We don't delete e.g., /login command, we respond with phone button
            await e.respond(strings['ask_phone'], buttons=ReplyKeyboardMarkup([[KeyboardButtonRequestPhone(strings['ask_phone_button'])]], resize=True, one_time=True), parse_mode='html')


    @_bot_client_instance.on(events.NewMessage(pattern=r"/logout", func=lambda e: e.is_private))
    async def logout_command_handler(e):
        owner_data = db.get_user_data(e.sender_id)
        if not utils.get(owner_data, 'logged_in'):
            await e.respond(strings['need_login'], parse_mode='html')
        else:
            await e.respond(strings['logout_sure'], buttons=menus.yesno('logout'), parse_mode='html')

    @_bot_client_instance.on(events.NewMessage(pattern=r"/addaccount", func=lambda e: e.is_private))
    async def add_member_account_command_handler(e):
        # This sends a *new* message with the phone request reply keyboard
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
        logged_in_bot_users = db.users_db.count_documents({"logged_in": True});
        
        total_member_accounts = sum(len(doc.get('user_accounts', [])) for doc in db.users_db.find({}))
        active_adding_tasks_count = len(members_adder.ACTIVE_ADDING_TASKS)
        
        st = (f"<b>Bot Statistics</b>\n\n"
              f"👤 <b>Total Bot Users:</b> <code>{total_bot_users}</code>\n"
              f"✅ <b>Logged-in Bot Users:</b> <code>{logged_in_bot_users}</code>\n"
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

    # --- Message Type Handlers ---
    @_bot_client_instance.on(events.NewMessage(pattern=r"^(?:https?://t\.me/(c/)?(\w+)/(\d+)|(-?\d+)\.(\d+))$", func=lambda e: e.is_private))
    async def link_handler(e):
        if not await menus.check_fsub(e): return
        owner_data = db.get_user_data(e.sender_id)
        if utils.get(owner_data, 'is_banned_from_dl', False):
            await e.respond(strings['USER_BANNED_MESSAGE'], parse_mode='html')
            return
        await e.respond("This bot is dedicated to member adding. If you need to set up a task, please use the /settings menu.")

    @_bot_client_instance.on(events.NewMessage(func=lambda e: e.is_private and e.contact))
    async def contact_handler(e):
        uid = e.sender_id
        owner_data = db.get_user_data(uid)
        state = utils.get(owner_data, 'state')

        if e.contact.user_id == e.chat_id: # Main bot owner login via contact share
            await _handle_main_bot_user_login_contact_received(e.contact, e) # Call a specific handler for this
        
        elif state and state.startswith(("awaiting_member_account_relogin_phone_", "awaiting_member_account_phone")):
            account_id_match = re.search(r'_(\d+)$', state)
            account_id = int(account_id_match.group(1)) if account_id_match else None
            
            phone_number = e.contact.phone_number
            
            if state == "awaiting_member_account_phone" and any(acc.get('phone_number') == phone_number for acc in utils.get(owner_data, 'user_accounts', [])):
                db.update_user_data(uid, {"$set": {"state": None}})
                return await e.respond("This phone number is already added.", buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='html')

            client = TelegramClient(StringSession(), config.API_ID, config.API_HASH, **config.device_info)
            try:
                await client.connect()
                code_request = await client.send_code_request(phone_number)
                
                # Check if new account or re-login
                if state == "awaiting_member_account_phone": # New account flow
                    existing_account_ids = [acc.get('account_id') for acc in utils.get(owner_data, 'user_accounts', []) if utils.get(acc, 'account_id')]
                    new_account_id = 1
                    if existing_account_ids:
                        new_account_id = max(existing_account_ids) + 1
                    account_id = new_account_id # Set account_id for the new entry

                    new_account_entry = {
                        "account_id": account_id,
                        "phone_number": phone_number,
                        "session_string": client.session.save(), # Temp session initially
                        "logged_in": False,
                        "last_login_time": None,
                        "daily_adds_count": 0,
                        "soft_error_count": 0,
                        "last_add_date": None,
                        "is_active_for_adding": False,
                        "is_banned_for_adding": False,
                        "flood_wait_until": 0,
                        "error_type": None,
                        "temp_login_data": {} # Will be populated next
                    }
                    db.update_user_data(uid, {"$push": {"user_accounts": new_account_entry}}) # Add the new account entry
                
                # Now update the temp_login_data for either new or re-login flow
                db.update_user_account_in_owner_doc(uid, account_id,
                    {"$set": {"user_accounts.$.temp_login_data": {
                        'phash': code_request.phone_code_hash,
                        'sess': client.session.save(),
                        'clen': code_request.type.length,
                        'code_ok': False,
                        'need_pass': False
                    }}}
                )
            
                db.update_user_data(uid, {"$set": {"state": f"awaiting_member_account_code_{account_id}"}})
                
                numpad=[[Button.inline(str(i),f'{{"press":{i}}}')for i in range(j,j+3)]for j in range(1,10,3)];numpad.append([Button.inline("Clear All",'{"press":"clear_all"}'),Button.inline("0",'{"press":0}'),Button.inline("⌫",'{"press":"clear"}')])
                await e.respond(strings['ask_code'], buttons=numpad, parse_mode='html', link_preview=False)

            except Exception as ex:
                LOGGER.error(f"Error during member account phone submission: {ex}")
                # If an error occurs during new account add, remove the incomplete entry
                if state == "awaiting_member_account_phone" and account_id:
                    db.update_user_data(uid, {"$pull": {"user_accounts": {"account_id": account_id}}})
                db.update_user_data(uid, {"$set": {"state": None}})
                await e.respond(f"Failed to process account: {ex}", buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='html')
            finally:
                if client.is_connected(): await client.disconnect()
        else:
            await e.respond(strings['wrong_phone'], parse_mode='html')

    @_bot_client_instance.on(events.NewMessage(func=lambda e: e.is_private and not e.text.startswith('/')))
    async def private_message_handler(e):
        uid = e.sender_id
        owner_data = db.get_user_data(uid)
        if not owner_data: return
        
        state = utils.get(owner_data, 'state')

        if state == "awaiting_broadcast_message":
            db.update_user_data(uid, {"$set": {"state": None}})
            asyncio.create_task(members_adder.run_user_broadcast(uid, e.message))
        elif utils.get(json.loads(utils.get(owner_data, 'login', '{}')), 'need_pass'):
            db.update_user_data(uid, {'$set': {'password': e.text}})
            await e.delete()
            await e.respond(f"{strings['ask_ok']}'<code>********</code>'", buttons=menus.yesno('pass'), parse_mode='html')
        elif state and state.startswith("awaiting_member_account_code_"):
            account_id = int(state.split('_')[-1])
            account_info_doc = db.users_db.find_one({"chat_id": uid, "user_accounts.account_id": account_id})
            if not account_info_doc: return await e.respond("Invalid state for OTP. Please try adding account again.", buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='html')
            account_info = next((acc for acc in utils.get(account_info_doc, 'user_accounts', []) if utils.get(acc, 'account_id') == account_id), None)
            if not account_info or not utils.get(account_info, 'temp_login_data'):
                return await e.respond("Invalid state for OTP. Please try adding account again.", buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='html')
            
            temp_login_data = utils.get(account_info, 'temp_login_data')
            phone_number = utils.get(temp_login_data, 'ph')
            phone_code_hash = utils.get(temp_login_data, 'phash')
            session_string_temp = utils.get(temp_login_data, 'sess')
            otp_code = e.text.strip()

            client = TelegramClient(StringSession(session_string_temp), config.API_ID, config.API_HASH, **config.device_info)
            try:
                await client.connect()
                await client.sign_in(phone=phone_number, code=otp_code, phone_code_hash=phone_code_hash)
                
                db.update_user_account_in_owner_doc(
                    uid, account_id,
                    {"$set": {
                        "user_accounts.$.session_string": client.session.save(),
                        "user_accounts.$.logged_in": True,
                        "user_accounts.$.last_login_time": time.time(),
                        "user_accounts.$.is_active_for_adding": True,
                        "user_accounts.$.temp_login_data": {}
                    }, "$unset": {"state": 1}}
                )
                members_adder.USER_CLIENTS[account_id] = client
                await e.respond(strings['ACCOUNT_ADDED_SUCCESS'].format(phone_number=phone_number, account_id=account_id), parse_mode='html')
                await menus.send_members_adding_menu(e, uid)
            except SessionPasswordNeededError:
                db.update_user_data(uid, {"$set": {"state": f"awaiting_member_account_password_{account_id}", "user_accounts.$.temp_login_data.need_pass": True}})
                await e.respond(strings['ask_pass'], parse_mode='html')
            except PhoneCodeInvalidError:
                await e.respond(strings['code_invalid'], parse_mode='html')
                numpad=[[Button.inline(str(i),f'{{"press":{i}}}')for i in range(j,j+3)]for j in range(1,10,3)];numpad.append([Button.inline("Clear All",'{"press":"clear_all"}'),Button.inline("0",'{"press":0}'),Button.inline("⌫",'{"press":"clear"}')])
                await e.respond(strings['ask_code'], buttons=numpad, parse_mode='html', link_preview=False)
            except Exception as ex:
                LOGGER.error(f"Error during member account OTP submission: {ex}")
                db.update_user_account_in_owner_doc(uid, account_id, {"$set": {"user_accounts.$.logged_in": False, "user_accounts.$.is_active_for_adding": False, "user_accounts.$.temp_login_data": {}}})
                db.update_user_data(uid, {"$unset": {"state": 1}})
                await e.respond(strings['ACCOUNT_LOGIN_FAILED'].format(error_message=ex), buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='html')
            finally:
                if client.is_connected() and not utils.get(account_info, 'logged_in'):
                    await client.disconnect()
        elif state and state.startswith("awaiting_member_account_password_"):
            account_id = int(state.split('_')[-1])
            account_info_doc = db.users_db.find_one({"chat_id": uid, "user_accounts.account_id": account_id})
            if not account_info_doc: return await e.respond("Invalid state for password. Please try adding account again.", buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='html')
            account_info = next((acc for acc in utils.get(account_info_doc, 'user_accounts', []) if utils.get(acc, 'account_id') == account_id), None)
            if not account_info or not utils.get(account_info, 'temp_login_data'):
                return await e.respond("Invalid state for password. Please try adding account again.", buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='html')
            
            temp_login_data = utils.get(account_info, 'temp_login_data')
            session_string_temp = utils.get(temp_login_data, 'sess')
            password = e.text.strip()

            client = TelegramClient(StringSession(session_string_temp), config.API_ID, config.API_HASH, **config.device_info)
            try:
                await client.connect()
                await client.sign_in(password=password)

                db.update_user_account_in_owner_doc(
                    uid, account_id,
                    {"$set": {
                        "user_accounts.$.session_string": client.session.save(),
                        "user_accounts.$.logged_in": True,
                        "user_accounts.$.last_login_time": time.time(),
                        "user_accounts.$.is_active_for_adding": True,
                        "user_accounts.$.temp_login_data": {}
                    }, "$unset": {"state": 1}}
                )
                members_adder.USER_CLIENTS[account_id] = client
                await e.respond(strings['ACCOUNT_ADDED_SUCCESS'].format(phone_number=utils.get(account_info, 'phone_number'), account_id=account_id), parse_mode='html')
                await menus.send_members_adding_menu(e, uid)
            except PasswordHashInvalidError:
                await e.respond(strings['pass_invalid'], parse_mode='html')
                await e.respond(strings['ask_pass'], parse_mode='html')
            except Exception as ex:
                LOGGER.error(f"Error during member account 2FA password submission: {ex}")
                db.update_user_account_in_owner_doc(uid, account_id, {"$set": {"user_accounts.$.logged_in": False, "user_accounts.$.is_active_for_adding": False, "user_accounts.$.temp_login_data": {}}})
                db.update_user_data(uid, {"$unset": {"state": 1}})
                await e.respond(strings['ACCOUNT_LOGIN_FAILED'].format(error_message=ex), buttons=[[Button.inline("« Back", '{"action":"members_adding_menu"}')]], parse_mode='html')
            finally:
                if client.is_connected() and not utils.get(account_info, 'logged_in'):
                    await client.disconnect()

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

        if raw_data.startswith("m_add_sc|"):
            try:
                parts = raw_data.split("|")
                _, chat_id, selection_type, task_id, page = parts
                chat_id = int(chat_id)
                task_id = int(task_id)
                page = int(page)

                if not owner_data: return await e.answer("User data not found. Please /start again.", alert=True)
                
                current_task_doc = db.get_task_in_owner_doc(uid, task_id)
                if not current_task_doc: return await e.answer("Adding task not found.", alert=True)

                if selection_type == 'from':
                    db.update_task_in_owner_doc(uid, task_id, {"$set": {"adding_tasks.$.source_chat_id": chat_id}})
                    popup_text = strings['TASK_SOURCE_SET'].format(task_id=task_id, chat_title=await members_adder.get_chat_title(_bot_client_instance, chat_id))
                    await e.answer(utils.strip_html(popup_text), alert=True)
                    await menus.send_adding_task_details_menu(e, uid, task_id)
                elif selection_type == 'to':
                    target_chats = utils.get(current_task_doc, 'target_chat_ids', [])
                    if chat_id in target_chats:
                        target_chats.remove(chat_id)
                        popup_text = strings['TASK_TARGET_UNSET'].format(task_id=task_id, chat_title=await members_adder.get_chat_title(_bot_client_instance, chat_id))
                    elif len(target_chats) < 2:
                        target_chats.append(chat_id)
                        popup_text = strings['TASK_TARGET_SET_MULTI'].format(task_id=task_id, chat_title=await members_adder.get_chat_title(_bot_client_instance, chat_id))
                    else:
                        popup_text = strings['AF_ERROR_TO_FULL']
                        return await e.answer(utils.strip_html(popup_text), alert=True)
                    
                    db.update_task_in_owner_doc(uid, task_id, {"$set": {"adding_tasks.$.target_chat_ids": target_chats}})
                    await e.answer(utils.strip_html(popup_text), alert=True)
                    await menus.send_chat_selection_menu(e, uid, 'to', task_id, page)
            except Exception as ex:
                LOGGER.error(f"Error processing compact callback 'm_add_sc': {ex}")
                await e.answer("An error occurred during chat selection.", alert=True)
            return

        elif raw_data.startswith("m_add_set|"):
            try:
                _, selection_type, task_id, page = raw_data.split("|")
                task_id = int(task_id)
                page = int(page)
                await menus.send_chat_selection_menu(e, uid, selection_type, task_id, page)
            except Exception as ex:
                LOGGER.error(f"Error processing compact callback 'm_add_set': {ex}")
                await e.answer("An error occurred.", alert=True)
            return
        
        elif raw_data.startswith("m_add_assign_acc|"):
            try:
                _, task_id_str, account_id_str = raw_data.split("|")
                task_id = int(task_id_str)
                account_id = int(account_id_str)

                current_task = db.get_task_in_owner_doc(uid, task_id)
                if not current_task: return await e.answer("Task not found.", alert=True)

                assigned_accounts = utils.get(current_task, 'assigned_accounts', [])
                
                if account_id in assigned_accounts:
                    assigned_accounts.remove(account_id)
                    popup_text = strings['ACCOUNT_UNASSIGNED_FROM_TASK'].format(account_id=account_id, task_id=task_id)
                else:
                    assigned_accounts.append(account_id)
                    popup_text = strings['ACCOUNT_ASSIGNED_TO_TASK'].format(account_id=account_id, task_id=task_id)

                db.update_task_in_owner_doc(uid, task_id, {"$set": {"adding_tasks.$.assigned_accounts": assigned_accounts}})
                await e.answer(utils.strip_html(popup_text), alert=True)
                await menus.send_assign_accounts_menu(e, uid, task_id)
            except Exception as ex:
                LOGGER.error(f"Error processing 'm_add_assign_acc' callback: {ex}")
                await e.answer("An error occurred.", alert=True)
            return

        if raw_data == 'noop' or raw_data == '{"action":"noop"}':
            return await e.answer()

        try:
            j = json.loads(raw_data)
            action = utils.get(j, 'action')
        except (json.JSONDecodeError, AttributeError):
            j = {}
            action = None

        if action:
            if not owner_data and action not in ["main_menu", "help", "commands", "retry_fsub", "show_tutorial", "ban_dl"]:
                await e.answer("User data not found. Please /start again.", alert=True)
                return

            if action == "ban_dl":
                if e.sender_id != config.OWNER_ID:
                    return await e.answer("You are not authorized to do this.", alert=True)
                user_id_to_ban = utils.get(j, 'user_id')
                if not user_id_to_ban:
                    return await e.answer("Error: User ID not found.", alert=True)
                
                db.users_db.update_one(
                    {"chat_id": user_id_to_ban},
                    {"$set": {"is_banned_from_dl": True}},
                    upsert=True
                )
                try:
                    ban_message = strings['BAN_NOTIFICATION_MESSAGE']
                    await _bot_client_instance.send_message(user_id_to_ban, ban_message, parse_mode='html')
                except Exception as ex:
                    LOGGER.error(f"Failed to send ban notification to {user_id_to_ban}: {ex}")

                await e.edit("✅ User has been **banned** and notified.")
                return

            if action == "main_menu":
                await menus.send_start_menu(e)
            elif action=="help":
                await menus.send_help_menu(e)
            elif action=="commands":
                await menus.send_commands_menu(e)
            elif action == "show_tutorial":
                if uid == config.OWNER_ID:
                    db.update_user_data(uid, {"$set": {"state": "awaiting_tutorial_video"}})
                    await e.edit(strings['TUTORIAL_PROMPT_OWNER'], parse_mode='html')
                else:
                    tutorial_doc = db.bot_settings_db.find_one({'setting': 'tutorial'})
                    if tutorial_doc and 'message_id' in tutorial_doc:
                        msg_id = utils.get(tutorial_doc, 'message_id')
                        try:
                            sent_video = await _bot_client_instance.forward_messages(e.chat_id, from_peer=config.OWNER_ID, message_ids=msg_id)
                            if sent_video:
                                asyncio.create_task(utils.delete_after(sent_video[0], 600))
                            await e.answer()
                        except Exception as ex:
                            LOGGER.error(f"Tutorial forward failed: {ex}")
                            await e.answer(utils.strip_html("Could not fetch the tutorial video."), alert=True)
                    else:
                        popup_text = strings['TUTORIAL_NOT_SET_MSG']
                        await e.answer(utils.strip_html(popup_text), alert=True)
            elif action == "settings":
                await menus.send_settings_menu(e)
            elif action == "user_broadcast":
                db.update_user_data(uid, {"$set": {"state": "awaiting_broadcast_message"}})
                await e.edit(strings['BROADCAST_MENU_TEXT'], buttons=[[Button.inline("🛑 Cancel Broadcast", '{"action":"cancel_broadcast"}')]], parse_mode='html')
            elif action == "cancel_broadcast":
                db.update_user_data(uid, {"$set": {"state": None}})
                await e.edit(strings['BROADCAST_CANCELLED'], buttons=[[Button.inline("« Back to Settings", '{"action":"settings"}')]], parse_mode='html')
            elif action=="retry_fsub":
                await e.delete()
                if await menus.check_fsub(e):await e.respond("Thanks for joining! Please use /settings to manage member adding.")
            elif action.startswith("yes_")or action.startswith("no_"):
                ctx=action.split('_',1)[1]
                if ctx=="logout":
                    if action.startswith("yes_"):
                        db.update_user_data(uid, {'$set':{'logged_in':False,'session':None,'login':'{}'}})
                        await e.edit(strings['logged_out'], parse_mode='html')
                    else:await e.edit(strings['not_logged_out'], parse_mode='html')
                elif ctx in["code","pass"]:
                    l=json.loads(utils.get(owner_data,'login','{}'))
                    ip=ctx=="pass";ko="pass_ok"if ip else"code_ok"
                    if action.startswith("yes_"):
                        l[ko]=True;db.update_user_data(uid, {'$set':{'login':json.dumps(l)}});
                        await sign_in_main_bot(e)
                    else:
                        l[ko]=False
                        if ip:db.update_user_data(uid, {'$set':{'login':json.dumps(l),'password':''}});await e.edit(strings['ask_pass'], parse_mode='html')
                        else:l['code']='';db.update_user_data(uid, {'$set':{'login':json.dumps(l)}});
                        numpad=[[Button.inline(str(i),f'{{"press":{i}}}')for i in range(j,j+3)]for j in range(1,10,3)];numpad.append([Button.inline("Clear All",'{"press":"clear_all"}'),Button.inline("0",'{"press":0}'),Button.inline("⌫",'{"press":"clear"}')])
                        await e.edit(strings['ask_code']+'\n<code>'+l.get('code','')+'</code>',buttons=numpad, parse_mode='html', link_preview=False)
            
            elif action == "members_adding_menu":
                await menus.send_members_adding_menu(e, uid)
            elif action == "add_member_account":
                await handle_add_member_account_flow(e)
            elif action == "manage_member_accounts":
                await menus.display_member_accounts(e, uid)
            elif action == "member_account_details":
                account_id = utils.get(j, 'account_id')
                await menus.send_member_account_details(e, uid, account_id)
            elif action == "confirm_delete_member_account":
                account_id = utils.get(j, 'account_id')
                await e.edit(f"Are you sure you want to delete account <code>{account_id}</code>? All tasks associated with it will be affected.", buttons=menus.yesno(f"delete_member_account_{account_id}"), parse_mode='html')
            elif action.startswith("yes_delete_member_account_"):
                account_id = int(action.split('_')[-1])
                await _handle_delete_member_account(e, uid, account_id)
            elif action.startswith("no_delete_member_account_"):
                account_id = int(action.split('_')[-1])
                await menus.send_member_account_details(e, uid, account_id)
            elif action == "relogin_member_account":
                account_id = utils.get(j, 'account_id')
                db.update_user_data(uid, {"$set": {"state": f"awaiting_member_account_relogin_phone_{account_id}"}})
                await e.edit(f"Please forward your phone number associated with account <code>{account_id}</code> to re-login.", buttons=[[Button.request_phone("Share Phone Number", resize=True, single_use=True)], [Button.inline("Cancel", f'{{"action":"member_account_details","account_id":{account_id}}}')]], parse_mode='html')
            elif action == "toggle_member_account_ban":
                account_id = utils.get(j, 'account_id')
                account_info = db.find_user_account_in_owner_doc(uid, account_id)
                if account_info:
                    new_ban_status = not utils.get(account_info, 'is_banned_for_adding', False)
                    db.update_user_account_in_owner_doc(uid, account_id, {"$set": {"user_accounts.$.is_banned_for_adding": new_ban_status}})
                    await e.answer(f"Account <code>{account_id}</code> ban status toggled to {'Banned' if new_ban_status else 'Unbanned'}.", alert=True)
                    await menus.send_member_account_details(e, uid, account_id)
                else:
                    await e.answer("Account not found.", alert=True)
            elif action == "create_adding_task":
                await menus.send_create_adding_task_menu(e, uid)
            elif action == "manage_adding_tasks":
                await menus.send_manage_adding_tasks_menu(e, uid)
            elif action == "m_add_task_menu":
                task_id = utils.get(j, 'task_id')
                await menus.send_adding_task_details_menu(e, uid, task_id)
            elif action == "start_adding_task":
                task_id = utils.get(j, 'task_id')
                task_to_start = db.get_task_in_owner_doc(uid, task_id)
                
                if not utils.get(task_to_start, 'source_chat_id'):
                    return await e.answer(strings['TASK_NO_SOURCE_SELECTED'], alert=True)
                if not utils.get(task_to_start, 'target_chat_ids'):
                    return await e.answer(strings['TASK_NO_TARGET_SELECTED'], alert=True)
                if not utils.get(task_to_start, 'assigned_accounts'):
                    return await e.answer(strings['TASK_NO_ACCOUNTS_ASSIGNED'], alert=True)

                started = await members_adder.start_adding_task(uid, task_id)
                if started:
                    await e.answer(strings['TASK_STARTING'].format(task_id=task_id), alert=True)
                    await menus.send_adding_task_details_menu(e, uid, task_id)
                else:
                    await e.answer("Failed to start task. Check account statuses.", alert=True)
            elif action == "pause_adding_task":
                task_id = utils.get(j, 'task_id')
                paused = await members_adder.pause_adding_task(task_id)
                if paused:
                    await e.answer(strings['TASK_PAUSING'].format(task_id=task_id), alert=True)
                    await menus.send_adding_task_details_menu(e, uid, task_id)
                else:
                    await e.answer("Failed to pause task. It might not be running.", alert=True)
            elif action == "confirm_delete_adding_task":
                task_id = utils.get(j, 'task_id')
                await e.edit(strings['TASK_DELETE_CONFIRM'].format(task_id=task_id), buttons=menus.yesno(f"delete_adding_task_{task_id}"), parse_mode='html')
            elif action.startswith("yes_delete_adding_task_"):
                task_id = int(action.split('_')[-1])
                await members_adder.pause_adding_task(task_id)
                db.update_user_data(uid, {"$pull": {"adding_tasks": {"task_id": task_id}}})
                await e.edit(strings['TASK_DELETED'].format(task_id=task_id), buttons=[[Button.inline("« Back", '{"action":"manage_adding_tasks"}')]], parse_mode='html')
            elif action.startswith("no_delete_adding_task_"):
                task_id = int(action.split('_')[-1])
                await menus.send_adding_task_details_menu(e, uid, task_id)
            
            return await e.answer()
            
        try:
            j = json.loads(raw_data)
            pr=utils.get(j,'press')
            if not owner_data: return await e.answer("User data not found. Please /start again.", alert=True)
            
            state = utils.get(owner_data, 'state')
            
            if state and (state.startswith("awaiting_member_account_code_") or state.startswith("awaiting_member_account_relogin_code_")):
                account_id = int(state.split('_')[-1])
                account_info = db.find_user_account_in_owner_doc(uid, account_id)
                if not account_info or not utils.get(account_info, 'temp_login_data'):
                    return await e.answer("Invalid state for OTP. Please try again.", alert=True)
                
                temp_login_data = utils.get(account_info, 'temp_login_data')
                code = utils.get(temp_login_data, 'code', '')
                if isinstance(pr,int): code += str(pr)
                elif pr=="clear": code = code[:-1]
                elif pr=="clear_all": code = ''
                
                db.update_user_account_in_owner_doc(uid, account_id, {"$set": {"user_accounts.$.temp_login_data.code": code}})
                
                clen, code_ok = utils.get(temp_login_data, 'clen'), utils.get(temp_login_data, 'code_ok', False)
                numpad=[[Button.inline(str(i),f'{{"press":{i}}}')for i in range(j,j+3)]for j in range(1,10,3)];numpad.append([Button.inline("Clear All",'{"press":"clear_all"}'),Button.inline("0",'{"press":0}'),Button.inline("⌫",'{"press":"clear"}')])
                try:
                    if clen and len(code)==clen and not code_ok:
                        db.update_user_account_in_owner_doc(uid, account_id, {"$set": {"user_accounts.$.temp_login_data.code_ok": True}})
                        await e.edit(f"{strings['ask_ok']}<code>{code}</code>",buttons=menus.yesno('code'), parse_mode='html')
                    else: await e.edit(strings['ask_code']+'\n<code>'+code+'</code>',buttons=numpad, parse_mode='html', link_preview=False)
                except MessageNotModifiedError: pass
                finally: await e.answer()

            else: # Default behavior for main bot login (if any)
                l=json.loads(utils.get(owner_data,'login','{}'))
                if isinstance(pr,int):l['code']=utils.get(l,'code','')+str(pr)
                elif pr=="clear":l['code']=utils.get(l,'code','')[:-1]
                elif pr=="clear_all":l['code']=''
                db.update_user_data(uid, {'$set':{'login':json.dumps(l)}})
                code,clen,code_ok=utils.get(l,'code',''),utils.get(l,'clen'),utils.get(l,'code_ok',False)
                numpad=[[Button.inline(str(i),f'{{"press":{i}}}')for i in range(j,j+3)]for j in range(1,10,3)];numpad.append([Button.inline("Clear All",'{"press":"clear_all"}'),Button.inline("0",'{"press":0}'),Button.inline("⌫",'{"press":"clear"}')])
                try:
                    if clen and len(code)==clen and not code_ok: await e.edit(f"{strings['ask_ok']}<code>{code}</code>",buttons=menus.yesno('code'), parse_mode='html')
                    elif'code'in l: await e.edit(strings['ask_code']+'\n<code>'+code+'</code>',buttons=numpad, parse_mode='html', link_preview=False)
                except MessageNotModifiedError: pass
                finally: await e.answer()

        except (json.JSONDecodeError, KeyError):
            await e.answer()
