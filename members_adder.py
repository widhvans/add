import asyncio
import random
import time
import datetime
import logging
from telethon import TelegramClient, functions
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError,
    PeerFloodError,
    UserPrivacyRestrictedError,
    UserAlreadyParticipantError,
    UserBlockedError,
    InputUserDeactivatedError
)
from telethon.tl.types import ChannelParticipantsRecent
from telethon.tl.functions.channels import GetParticipantsRequest

# REMOVED: import config  <-- This line is GONE
import db
import utils
from strings import strings

LOGGER = logging.getLogger(__name__)

# GLOBAL VARIABLES TO BE SET EXTERNALLY
bot_client = None
current_config_instance = None # NEW: This will hold the config instance

def set_bot_client(client):
    global bot_client
    bot_client = client

# NEW FUNCTION: To set the config instance
def set_config_instance(cfg):
    global current_config_instance
    current_config_instance = cfg

# Global dictionaries for managing active adding tasks and user clients
ACTIVE_ADDING_TASKS = {}
USER_CLIENTS = {}

async def run_user_broadcast(uid, message_to_send):
    # Use current_config_instance instead of config
    status_msg = await bot_client.send_message(uid, strings['BROADCAST_STARTED'], parse_mode='html')
    owner_data = db.get_user_data(uid)
    
    session_string = utils.get(owner_data, 'session')
    if not session_string:
        return await status_msg.edit(strings['session_invalid'], parse_mode='html')

    u_client = TelegramClient(StringSession(session_string), current_config_instance.API_ID, current_config_instance.API_HASH, **current_config_instance.device_info)
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

async def get_user_client(user_account_id):
    # Use current_config_instance instead of config
    if user_account_id in USER_CLIENTS and USER_CLIENTS[user_account_id].is_connected():
        return USER_CLIENTS[user_account_id]

    owner_data = db.users_db.find_one({"user_accounts.account_id": user_account_id})
    if not owner_data:
        LOGGER.warning(f"Owner data not found for member-adding account {user_account_id}.")
        return None

    account_info = next((acc for acc in owner_data.get('user_accounts', []) if acc.get('account_id') == user_account_id), None)
    if not account_info or not utils.get(account_info, 'session_string'):
        LOGGER.warning(f"No session string found for user account {user_account_id}.")
        db.update_user_account_in_owner_doc(owner_data['chat_id'], user_account_id,
            {"$set": {"user_accounts.$.logged_in": False, "user_accounts.$.is_active_for_adding": False, "user_accounts.$.is_banned_for_adding": False}}
        )
        return None

    client = TelegramClient(StringSession(account_info['session_string']), current_config_instance.API_ID, current_config_instance.API_HASH, **current_config_instance.device_info)

    try:
        if not client.is_connected():
            await client.connect()
        if not await client.is_user_authorized():
            LOGGER.warning(f"Session invalid for user account {user_account_id}. Marking as logged_in=False.")
            db.update_user_account_in_owner_doc(owner_data['chat_id'], user_account_id,
                {"$set": {"user_accounts.$.logged_in": False, "user_accounts.$.is_active_for_adding": False, "user_accounts.$.is_banned_for_adding": False}}
            )
            await client.disconnect()
            return None
        
        USER_CLIENTS[user_account_id] = client
        return client
    except Exception as e:
        LOGGER.error(f"Failed to connect or authorize client for {user_account_id}: {e}")
        db.update_user_account_in_owner_doc(owner_data['chat_id'], user_account_id,
            {"$set": {"user_accounts.$.logged_in": False, "user_accounts.$.is_active_for_adding": False, "user_accounts.$.is_banned_for_adding": False}}
        )
        if client.is_connected():
            await client.disconnect()
        return None

async def scrape_members(client, source_chat_id, limit=None): # REMOVED: limit=config.MEMBER_SCRAPE_LIMIT
    # Use current_config_instance.MEMBER_SCRAPE_LIMIT if limit is not provided
    actual_limit = limit if limit is not None else current_config_instance.MEMBER_SCRAPE_LIMIT

    members = []
    offset = 0
    while True:
        try:
            participants = await client(GetParticipantsRequest(
                channel=source_chat_id,
                filter=ChannelParticipantsRecent(),
                offset=offset,
                limit=100,
                hash=0
            ))
            if not participants.users:
                break
            
            for user in participants.users:
                if not user.bot and not user.is_self and user.status and \
                   not isinstance(user.status, (type(None), functions.contacts.Blocked, functions.contacts.BlockedWait)):
                    members.append(user)
                    if len(members) >= actual_limit: # Use actual_limit here
                        break
            
            offset += len(participants.users)
            if len(members) >= actual_limit or not participants.users: # Use actual_limit here
                break
            await asyncio.sleep(random.uniform(1, 3))

        except FloodWaitError as e:
            LOGGER.warning(f"Scraping FloodWait for {client.session.dc_id}: {e.seconds}s. Waiting...")
            await asyncio.sleep(e.seconds + random.uniform(5, 10))
        except Exception as e:
            LOGGER.error(f"Error scraping members from {source_chat_id}: {e}")
            break
    
    LOGGER.info(f"Scraped {len(members)} members from {source_chat_id}")
    return members

async def add_member_to_group(user_client, target_chat_id, member_user, task_id, account_id, owner_id):
    # Use current_config_instance for limits
    try:
        await user_client(functions.channels.InviteToChannelRequest(
            channel=target_chat_id,
            users=[member_user]
        ))
        LOGGER.info(f"Account {account_id}: Successfully added {member_user.id} to {target_chat_id} for Task {task_id}")
        
        db.update_user_account_in_owner_doc(owner_id, account_id,
            {"$inc": {"user_accounts.$.daily_adds_count": 1}, "$set": {"user_accounts.$.last_add_date": time.time()}}
        )
        return True
    except UserAlreadyParticipantError:
        LOGGER.info(f"Account {account_id}: Member {member_user.id} already in {target_chat_id}. Skipping.")
    except UserPrivacyRestrictedError:
        LOGGER.warning(f"Account {account_id}: Member {member_user.id} has privacy restrictions for adding. Skipping.")
        db.update_user_account_in_owner_doc(owner_id, account_id,
            {"$inc": {"user_accounts.$.soft_error_count": 1}}
        )
    except PeerFloodError as e:
        LOGGER.error(f"Account {account_id}: PeerFloodError detected! Message: {e}. Suspending account.")
        db.update_user_account_in_owner_doc(owner_id, account_id,
            {"$set": {"user_accounts.$.is_banned_for_adding": True, "user_accounts.$.last_error_time": time.time(), "user_accounts.$.error_type": "PeerFlood"}}
        )
        await bot_client.send_message(owner_id, strings['PEER_FLOOD_DETECTED'].format(account_id=account_id), parse_mode='html')
    except FloodWaitError as e:
        LOGGER.warning(f"Account {account_id}: FloodWaitError for {e.seconds}s. Pausing adding for this account.")
        db.update_user_account_in_owner_doc(owner_id, account_id,
            {"$set": {"user_accounts.$.flood_wait_until": time.time() + e.seconds, "user_accounts.$.last_error_time": time.time(), "user_accounts.$.error_type": "FloodWait"}}
        )
        await asyncio.sleep(e.seconds + random.uniform(5, 10))
    except (UserBlockedError, InputUserDeactivatedError):
        LOGGER.warning(f"Account {account_id}: Target user blocked or deactivated. Skipping.")
    except Exception as e:
        LOGGER.error(f"Account {account_id}: Failed to add {member_user.id} to {target_chat_id}: {e}")
        db.update_user_account_in_owner_doc(owner_id, account_id,
            {"$inc": {"user_accounts.$.soft_error_count": 1}}
        )
    return False

async def manage_adding_task(owner_id, task_id):
    # Use current_config_instance for limits and delays
    LOGGER.info(f"Starting to manage adding task {task_id} for owner {owner_id}")
    
    owner_doc = db.get_user_data(owner_id)
    if not owner_doc:
        LOGGER.error(f"Owner {owner_id} data not found for task {task_id}. Stopping task.")
        return
    task_info = db.get_task_in_owner_doc(owner_id, task_id)
    if not task_info or not utils.get(task_info, 'is_active'):
        LOGGER.info(f"Task {task_id} is not active or not found. Stopping management.")
        return

    source_chat_id = utils.get(task_info, 'source_chat_id')
    target_chat_ids = utils.get(task_info, 'target_chat_ids', [])
    assigned_account_ids = utils.get(task_info, 'assigned_accounts', [])

    if not source_chat_id or not target_chat_ids or not assigned_account_ids:
        LOGGER.warning(f"Task {task_id} missing source, target, or assigned accounts. Pausing task.")
        db.update_task_in_owner_doc(owner_id, task_id, {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}})
        await bot_client.send_message(owner_id, strings['TASK_NO_SOURCE_SELECTED'] if not source_chat_id else (strings['TASK_NO_TARGET_SELECTED'] if not target_chat_ids else strings['TASK_NO_ACCOUNTS_ASSIGNED']), parse_mode='html')
        return

    members_to_add = []
    scrape_client_info = None
    for acc_id in assigned_account_ids:
        acc_info = db.find_user_account_in_owner_doc(owner_id, acc_id)
        if acc_info and utils.get(acc_info, 'logged_in') and \
           not utils.get(acc_info, 'is_banned_for_adding') and \
           utils.get(acc_info, 'flood_wait_until', 0) < time.time():
            scrape_client = await get_user_client(acc_id)
            if scrape_client:
                scrape_client_info = (acc_id, scrape_client)
                break
    
    if not scrape_client_info:
        LOGGER.warning(f"No active accounts available for scraping for task {task_id}. Pausing task.")
        db.update_task_in_owner_doc(owner_id, task_id, {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}})
        await bot_client.send_message(owner_id, strings['NO_ACTIVE_ACCOUNTS_FOR_TASK'].format(task_id=task_id), parse_mode='html')
        return

    try:
        await bot_client.send_message(owner_id, strings['SCRAPING_MEMBERS'])
        # Pass the limit to scrape_members
        members_to_add = await scrape_members(scrape_client_info[1], source_chat_id, limit=current_config_instance.MEMBER_SCRAPE_LIMIT)
        if not members_to_add:
            await bot_client.send_message(owner_id, "No members found to scrape or an error occurred during scraping. Pausing task.")
            db.update_task_in_owner_doc(owner_id, task_id, {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}})
            return
        await bot_client.send_message(owner_id, strings['SCRAPING_COMPLETE'].format(count=len(members_to_add)))
    except Exception as e:
        LOGGER.error(f"Failed to scrape members for task {task_id}: {e}")
        await bot_client.send_message(owner_id, f"Error scraping members for task {task_id}: {e}. Pausing task.", parse_mode='html')
        db.update_task_in_owner_doc(owner_id, task_id, {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}})
        return

    current_member_index = utils.get(task_info, 'current_member_index', 0)
    added_count_for_task = utils.get(task_info, 'added_members_count', 0)
    total_members_to_process = len(members_to_add)

    random.shuffle(members_to_add) 
    
    while True:
        try:
            task_info = db.get_task_in_owner_doc(owner_id, task_id)
            if not task_info or not utils.get(task_info, 'is_active'):
                LOGGER.info(f"Task {task_id} paused or stopped by owner. Exiting adding loop.")
                break

            if current_member_index >= total_members_to_process:
                await bot_client.send_message(owner_id, strings['TASK_COMPLETED'].format(task_id=task_id))
                db.update_task_in_owner_doc(owner_id, task_id, {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "completed"}})
                break

            available_accounts = []
            for acc_id in assigned_account_ids:
                acc_info = db.find_user_account_in_owner_doc(owner_id, acc_id)
                if acc_info and utils.get(acc_info, 'logged_in') and \
                   not utils.get(acc_info, 'is_banned_for_adding') and \
                   utils.get(acc_info, 'flood_wait_until', 0) < time.time():
                    
                    today = datetime.date.today()
                    last_add_date = utils.get(acc_info, 'last_add_date')
                    if last_add_date:
                        last_add_date_dt = datetime.datetime.fromtimestamp(last_add_date).date()
                        if last_add_date_dt < today:
                            db.update_user_account_in_owner_doc(owner_id, acc_id, {"$set": {"user_accounts.$.daily_adds_count": 0, "user_accounts.$.soft_error_count": 0, "user_accounts.$.last_add_date": time.time()}})
                            acc_info['daily_adds_count'] = 0
                            acc_info['soft_error_count'] = 0

                    if utils.get(acc_info, 'daily_adds_count', 0) < current_config_instance.MAX_DAILY_ADDS_PER_ACCOUNT and \
                       utils.get(acc_info, 'soft_error_count', 0) < current_config_instance.SOFT_ADD_LIMIT_ERRORS:
                        client = await get_user_client(acc_id)
                        if client:
                            available_accounts.append((acc_id, client, acc_info))
            
            if not available_accounts:
                await bot_client.send_message(owner_id, strings['NO_ACTIVE_ACCOUNTS_FOR_TASK'].format(task_id=task_id), parse_mode='html')
                db.update_task_in_owner_doc(owner_id, task_id, {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}})
                break

            account_id, current_user_client, account_data = random.choice(available_accounts) 

            member_to_add = members_to_add[current_member_index]
            
            added_to_any_target = False
            for target_chat_id in target_chat_ids:
                success = await add_member_to_group(current_user_client, target_chat_id, member_to_add, task_id, account_id, owner_id)
                if success:
                    db.update_task_in_owner_doc(owner_id, task_id, {"$inc": {"adding_tasks.$.added_members_count": 1}})
                    added_count_for_task += 1
                    break

            if utils.get(account_data, 'daily_adds_count', 0) >= current_config_instance.MAX_DAILY_ADDS_PER_ACCOUNT or \
               utils.get(account_data, 'soft_error_count', 0) >= current_config_instance.SOFT_ADD_LIMIT_ERRORS:
                await bot_client.send_message(owner_id, strings['ADDING_LIMIT_REACHED'].format(account_id=account_id, limit=current_config_instance.MAX_DAILY_ADDS_PER_ACCOUNT), parse_mode='html')

            if added_to_any_target:
                current_member_index += 1
                db.update_task_in_owner_doc(owner_id, task_id, {"$set": {"adding_tasks.$.current_member_index": current_member_index}})
            
            progress_percent = (added_count_for_task / total_members_to_process) * 100
            
            if added_count_for_task % 5 == 0 or added_count_for_task == 1:
                progress_msg_text = strings['TASK_PROGRESS'].format(
                    task_id=task_id, added_count=added_count_for_task, total_members=total_members_to_process,
                    progress=progress_percent, account_id=account_id
                )
                last_progress_message_id = utils.get(task_info, 'last_progress_message_id')
                try:
                    if last_progress_message_id:
                        await bot_client.edit_message(owner_id, last_progress_message_id, progress_msg_text, parse_mode='html')
                    else:
                        msg = await bot_client.send_message(owner_id, progress_msg_text, parse_mode='html')
                        db.update_task_in_owner_doc(owner_id, task_id, {"$set": {"adding_tasks.$.last_progress_message_id": msg.id}})
                except Exception as update_error:
                    LOGGER.warning(f"Could not update progress message for task {task_id}: {update_error}")
                    msg = await bot_client.send_message(owner_id, progress_msg_text, parse_mode='html')
                    db.update_task_in_owner_doc(owner_id, task_id, {"$set": {"adding_tasks.$.last_progress_message_id": msg.id}})

            await asyncio.sleep(random.uniform(current_config_instance.MIN_ADD_DELAY, current_config_instance.MAX_ADD_DELAY))

        except asyncio.CancelledError:
            LOGGER.info(f"Adding task {task_id} cancelled by owner.")
            await bot_client.send_message(owner_id, f"Adding task {task_id} cancelled.")
            break
        except Exception as e:
            LOGGER.error(f"Unhandled error in adding task {task_id}: {e}")
            await bot_client.send_message(owner_id, f"An unexpected error occurred in Task {task_id}: {e}. Pausing task.", parse_mode='html')
            db.update_task_in_owner_doc(owner_id, task_id, {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}})
            break
    
    LOGGER.info(f"Adding task {task_id} management finished.")

async def start_adding_task(owner_id, task_id):
    if task_id in ACTIVE_ADDING_TASKS:
        LOGGER.warning(f"Task {task_id} is already running.")
        return False
    
    task_info = db.get_task_in_owner_doc(owner_id, task_id)
    if not task_info: return False

    assigned_account_ids = utils.get(task_info, 'assigned_accounts', [])
    if not assigned_account_ids:
        await bot_client.send_message(owner_id, strings['TASK_NO_ACCOUNTS_ASSIGNED'], parse_mode='html')
        return False

    for acc_id in assigned_account_ids:
        client = await get_user_client(acc_id)
        if not client:
            await bot_client.send_message(owner_id, f"Account <code>{acc_id}</code> could not be connected. Task {task_id} cannot start.", parse_mode='html')
            return False
        
    task = asyncio.create_task(manage_adding_task(owner_id, task_id))
    ACTIVE_ADDING_TASKS[task_id] = task
    db.update_task_in_owner_doc(owner_id, task_id, {"$set": {"adding_tasks.$.is_active": True, "adding_tasks.$.status": "active"}})
    LOGGER.info(f"Adding task {task_id} started for owner {owner_id}.")
    return True

async def pause_adding_task(task_id):
    if task_id in ACTIVE_ADDING_TASKS:
        ACTIVE_ADDING_TASKS[task_id].cancel()
        del ACTIVE_ADDING_TASKS[task_id]
        owner_doc = db.users_db.find_one({"adding_tasks.task_id": task_id}) # Find owner by task_id
        if owner_doc:
            db.update_task_in_owner_doc(owner_doc['chat_id'], task_id, {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}})
        LOGGER.info(f"Adding task {task_id} paused.")
        return True
    return False

async def stop_user_adding_clients(owner_id):
    owner_data = db.get_user_data(owner_id)
    if owner_data:
        for account in utils.get(owner_data, 'user_accounts', []):
            acc_id = utils.get(account, 'account_id')
            if acc_id and acc_id in USER_CLIENTS:
                client = USER_CLIENTS.pop(acc_id)
                if client.is_connected():
                    await client.disconnect()
                LOGGER.info(f"Disconnected user client {acc_id} for owner {owner_id}.")

async def get_chat_title(client, chat_id):
    try:
        entity = await client.get_entity(chat_id)
        return entity.title if hasattr(entity, 'title') else entity.username or f"User {entity.id}"
    except Exception as e:
        LOGGER.warning(f"Could not get title for chat {chat_id}: {e}")
        return f"ID: <code>{chat_id}</code>"
