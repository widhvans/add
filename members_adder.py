
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
    InputUserDeactivatedError,
    ChannelPrivateError,
    MessageNotModifiedError,
)
from telethon.tl.types import ChannelParticipantsRecent
from telethon.tl.functions.channels import GetParticipantsRequest, JoinChannelRequest

import db
import utils
from strings import strings

LOGGER = logging.getLogger(__name__)

bot_client = None
current_config_instance = None

def set_bot_client(client):
    global bot_client
    bot_client = client

def set_config_instance(cfg):
    global current_config_instance
    current_config_instance = cfg

ACTIVE_ADDING_TASKS = {}
USER_CLIENTS = {}

async def run_user_broadcast(uid, message_to_send):
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
            {"logged_in": False, "is_active_for_adding": False, "is_banned_for_adding": False}
        )
        return None

    client = TelegramClient(StringSession(account_info['session_string']), current_config_instance.API_ID, current_config_instance.API_HASH, **current_config_instance.device_info)

    try:
        if not client.is_connected():
            await client.connect()
        if not await client.is_user_authorized():
            LOGGER.warning(f"Session invalid for user account {user_account_id}. Marking as logged_in=False.")
            db.update_user_account_in_owner_doc(owner_data['chat_id'], user_account_id,
                {"logged_in": False, "is_active_for_adding": False, "is_banned_for_adding": False}
            )
            await client.disconnect()
            return None
        
        USER_CLIENTS[user_account_id] = client
        return client
    except Exception as e:
        LOGGER.error(f"Failed to connect or authorize client for {user_account_id}: {e}")
        db.update_user_account_in_owner_doc(owner_data['chat_id'], user_account_id,
            {"logged_in": False, "is_active_for_adding": False, "is_banned_for_adding": False}
        )
        if client.is_connected():
            await client.disconnect()
        return None

async def scrape_members(client, source_chat_id, limit=None):
    actual_limit = limit if limit is not None else current_config_instance.MEMBER_SCRAPE_LIMIT

    members = []
    offset = 0
    try:
        while True:
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
                # BUG FIX: Corrected the filtering logic to be robust and avoid crashes.
                # We filter out bots, deleted accounts, and the scraper's own account.
                if not user.bot and not user.deleted and not user.is_self:
                    members.append(user)
                    if len(members) >= actual_limit:
                        break
            
            if len(members) >= actual_limit or len(participants.users) < 100:
                break
                
            offset += len(participants.users)
            await asyncio.sleep(random.uniform(1, 3))

    except FloodWaitError as e:
        LOGGER.warning(f"Scraping FloodWait on account: {e.seconds}s. Waiting...")
        await asyncio.sleep(e.seconds + random.uniform(5, 10))
    except Exception as e:
        LOGGER.error(f"Error scraping members from {source_chat_id}: {e}")
    
    LOGGER.info(f"Scraped {len(members)} members from {source_chat_id}")
    return members


async def add_member_to_group(user_client, target_chat_id, member_user, task_id, account_id, owner_id):
    try:
        await user_client(functions.channels.InviteToChannelRequest(
            channel=target_chat_id,
            users=[member_user]
        ))
        LOGGER.info(f"Account {account_id}: Successfully added {member_user.id} to {target_chat_id} for Task {task_id}")
        
        db.update_user_account_in_owner_doc(owner_id, account_id,
            {"$inc": {"daily_adds_count": 1}, "$set": {"last_add_date": time.time()}}
        )
        return True, "Success"
    except UserAlreadyParticipantError:
        LOGGER.info(f"Account {account_id}: Member {member_user.id} already in {target_chat_id}. Skipping.")
        return False, "AlreadyParticipant"
    except UserPrivacyRestrictedError:
        LOGGER.warning(f"Account {account_id}: Member {member_user.id} has privacy restrictions. Skipping.")
        db.update_user_account_in_owner_doc(owner_id, account_id,
            {"$inc": {"soft_error_count": 1}}
        )
        return False, "PrivacyRestricted"
    except PeerFloodError as e:
        LOGGER.error(f"Account {account_id}: PeerFloodError detected! Message: {e}. Suspending account.")
        db.update_user_account_in_owner_doc(owner_id, account_id,
            {"is_banned_for_adding": True, "last_error_time": time.time(), "error_type": "PeerFlood"}
        )
        await bot_client.send_message(owner_id, strings['PEER_FLOOD_DETECTED'].format(account_id=account_id), parse_mode='html')
        return False, "PeerFlood"
    except FloodWaitError as e:
        LOGGER.warning(f"Account {account_id}: FloodWaitError for {e.seconds}s. Pausing adding.")
        db.update_user_account_in_owner_doc(owner_id, account_id,
            {"flood_wait_until": time.time() + e.seconds, "last_error_time": time.time(), "error_type": "FloodWait"}
        )
        await asyncio.sleep(e.seconds + random.uniform(5, 10))
        return False, "FloodWait"
    except (UserBlockedError, InputUserDeactivatedError):
        LOGGER.warning(f"Account {account_id}: Target user blocked or deactivated. Skipping.")
        return False, "UserBlocked"
    except Exception as e:
        LOGGER.error(f"Account {account_id}: Failed to add {member_user.id} to {target_chat_id}: {e}")
        db.update_user_account_in_owner_doc(owner_id, account_id,
            {"$inc": {"soft_error_count": 1}}
        )
        return False, str(e)

async def manage_adding_task(owner_id, task_id):
    LOGGER.info(f"Starting to manage adding task {task_id} for owner {owner_id}")
    
    owner_doc = db.get_user_data(owner_id)
    if not owner_doc:
        LOGGER.error(f"Owner {owner_id} data not found for task {task_id}. Stopping task.")
        return
    task_info = db.get_task_in_owner_doc(owner_id, task_id)
    if not task_info or not utils.get(task_info, 'is_active'):
        LOGGER.info(f"Task {task_id} is not active or not found. Stopping management.")
        return

    source_chat_ids = utils.get(task_info, 'source_chat_ids', [])
    target_chat_id = utils.get(task_info, 'target_chat_id')
    assigned_account_ids = utils.get(task_info, 'assigned_accounts', [])

    if not source_chat_ids or not target_chat_id or not assigned_account_ids:
        LOGGER.warning(f"Task {task_id} missing source, target, or assigned accounts. Pausing task.")
        db.update_task_in_owner_doc(owner_id, task_id, {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}})
        await bot_client.send_message(owner_id, strings['TASK_NO_SOURCE_SELECTED'] if not source_chat_ids else (strings['TASK_NO_TARGET_SELECTED'] if not target_chat_id else strings['TASK_NO_ACCOUNTS_ASSIGNED']), parse_mode='html')
        return

    active_clients_for_task = []
    for acc_id in assigned_account_ids:
        client = await get_user_client(acc_id)
        if client:
            active_clients_for_task.append((acc_id, client))

    if not active_clients_for_task:
        LOGGER.warning(f"No active accounts could be connected for task {task_id}. Pausing.")
        db.update_task_in_owner_doc(owner_id, task_id, {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}})
        await bot_client.send_message(owner_id, strings['NO_ACTIVE_ACCOUNTS_FOR_TASK'].format(task_id=task_id), parse_mode='html')
        return

    all_chats_to_check = source_chat_ids + [target_chat_id]
    for chat_to_check in all_chats_to_check:
        access_verified = False
        for acc_id, client in active_clients_for_task:
            try:
                await client.get_entity(chat_to_check)
                access_verified = True
                LOGGER.info(f"Access to chat {chat_to_check} verified with account {acc_id}.")
                break
            except Exception:
                continue
        
        if not access_verified:
            LOGGER.error(f"No assigned account for task {task_id} can access chat {chat_to_check}. Pausing task.")
            db.update_task_in_owner_doc(owner_id, task_id, {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}})
            await bot_client.send_message(owner_id, f"Could not access chat `{chat_to_check}` with any assigned account for Task `{task_id}`. Pausing task.", parse_mode='Markdown')
            return

    scrape_acc_id, scrape_client = active_clients_for_task[0]
    members_to_add = []
    try:
        await bot_client.send_message(owner_id, strings['SCRAPING_MEMBERS'])
        all_members = {}
        for source_chat in source_chat_ids:
            scraped = await scrape_members(scrape_client, source_chat, limit=current_config_instance.MEMBER_SCRAPE_LIMIT)
            for member in scraped:
                if member.id not in all_members:
                    all_members[member.id] = member
        members_to_add = list(all_members.values())

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
            owner_doc_for_accounts = db.get_user_data(owner_id) # Re-fetch to get latest account status
            for acc_id in assigned_account_ids:
                acc_info = db.find_user_account_in_owner_doc(owner_id, acc_id) # This is better
                if acc_info and acc_info.get('logged_in') and not acc_info.get('is_banned_for_adding') and acc_info.get('flood_wait_until', 0) < time.time():
                    
                    today_start_ts = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
                    if acc_info.get('last_add_date', 0) < today_start_ts:
                        db.update_user_account_in_owner_doc(owner_id, acc_id, {"daily_adds_count": 0, "soft_error_count": 0})
                        acc_info['daily_adds_count'] = 0
                        acc_info['soft_error_count'] = 0

                    if acc_info.get('daily_adds_count', 0) < current_config_instance.MAX_DAILY_ADDS_PER_ACCOUNT and \
                       acc_info.get('soft_error_count', 0) < current_config_instance.SOFT_ADD_LIMIT_ERRORS:
                        client = await get_user_client(acc_id)
                        if client:
                            available_accounts.append((acc_id, client, acc_info))
            
            if not available_accounts:
                await bot_client.send_message(owner_id, strings['NO_ACTIVE_ACCOUNTS_FOR_TASK'].format(task_id=task_id), parse_mode='html')
                db.update_task_in_owner_doc(owner_id, task_id, {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}})
                break

            account_id, current_user_client, account_data = random.choice(available_accounts) 

            member_to_add = members_to_add[current_member_index]
            
            success, reason = await add_member_to_group(current_user_client, target_chat_id, member_to_add, task_id, account_id, owner_id)
            
            current_member_index += 1
            db.update_task_in_owner_doc(owner_id, task_id, {"$set": {"adding_tasks.$.current_member_index": current_member_index}})

            if success:
                added_count_for_task += 1
                db.update_task_in_owner_doc(owner_id, task_id, {"$inc": {"adding_tasks.$.added_members_count": 1}})

            if utils.get(account_data, 'daily_adds_count', 0) + 1 >= current_config_instance.MAX_DAILY_ADDS_PER_ACCOUNT or \
               utils.get(account_data, 'soft_error_count', 0) + (1 if not success and reason not in ["AlreadyParticipant", "UserBlocked"] else 0) >= current_config_instance.SOFT_ADD_LIMIT_ERRORS:
                await bot_client.send_message(owner_id, strings['ADDING_LIMIT_REACHED'].format(account_id=account_id, limit=current_config_instance.MAX_DAILY_ADDS_PER_ACCOUNT), parse_mode='html')
            
            if total_members_to_process > 0 and (added_count_for_task % 5 == 0 or added_count_for_task == 1):
                progress_percent = (added_count_for_task / total_members_to_process) * 100
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
            LOGGER.error(f"Unhandled error in adding task {task_id}: {e}", exc_info=True)
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
        owner_doc = db.users_db.find_one({"adding_tasks.task_id": task_id})
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
    """
    More robustly gets a chat title. Handles cases where the entity is not in cache.
    """
    try:
        entity = await client.get_entity(chat_id)
        # Return title for chats/channels, or username/full_name for users
        if hasattr(entity, 'title'):
            return entity.title
        else:
            name = ""
            if hasattr(entity, 'first_name'):
                name += entity.first_name + " "
            if hasattr(entity, 'last_name') and entity.last_name:
                name += entity.last_name
            return name.strip() or f"User ID: {entity.id}"
    except (ValueError, TypeError) as e:
        # This specifically catches "Could not find the input entity" errors
        LOGGER.warning(f"Could not resolve entity for chat_id {chat_id}: {e}")
        return f"ID: `{chat_id}` (Unresolved)"
    except Exception as e:
        LOGGER.error(f"Unexpected error getting title for chat {chat_id}: {e}")
        return f"ID: `{chat_id}` (Error)"
