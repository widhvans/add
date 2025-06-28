import asyncio
import random
import time
import datetime
import logging
from telethon import TelegramClient, functions, types # Import types for isinstance checks
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
    InviteHashInvalidError,
    InviteHashExpiredError,
)
from telethon.tl.types import ChannelParticipantsRecent
from telethon.tl.functions.channels import GetParticipantsRequest, JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest # For joining via link

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
    
    task_info = db.get_task_in_owner_doc(owner_id, task_id)
    if not task_info or not utils.get(task_info, 'is_active'):
        LOGGER.info(f"Task {task_id} is not active or not found. Stopping management.")
        return

    source_chat_ids = utils.get(task_info, 'source_chat_ids', [])
    target_chat_id = utils.get(task_info, 'target_chat_id')
    assigned_account_ids = utils.get(task_info, 'assigned_accounts', [])

    if not source_chat_ids or not target_chat_id or not assigned_account_ids:
        await bot_client.send_message(owner_id, "Task is not fully configured. Please set source, target, and ensure you have active accounts.")
        db.update_task_in_owner_doc(owner_id, task_id, {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}})
        return

    active_clients_for_task = [client for acc_id in assigned_account_ids if (client := await get_user_client(acc_id))]
    if not active_clients_for_task:
        await bot_client.send_message(owner_id, strings['NO_ACTIVE_ACCOUNTS_FOR_TASK'].format(task_id=task_id), parse_mode='html')
        db.update_task_in_owner_doc(owner_id, task_id, {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}})
        return

    # --- FEATURE: Auto-Join and Validate Chats ---
    all_chats_to_setup = source_chat_ids + [target_chat_id]
    for chat in all_chats_to_setup:
        is_source = chat in source_chat_ids
        setup_successful = False
        
        # Try to join/validate with at least one client
        for client in active_clients_for_task:
            me = await client.get_me()
            try:
                # Handle invite links
                if isinstance(chat, str) and ('joinchat' in chat or '+' in chat):
                    invite_hash = chat.split('/')[-1].replace('+', '')
                    try:
                        await client(ImportChatInviteRequest(invite_hash))
                        LOGGER.info(f"Account {me.id} successfully joined {chat}")
                    except UserAlreadyParticipantError:
                        pass # Already a member, which is fine
                
                # Get entity to validate type and access
                entity = await client.get_entity(chat)

                # If it's a source chat, it must be a group or channel
                if is_source and not isinstance(entity, (types.Channel, types.Chat)):
                    error_msg = f"**Task {task_id} Paused!**\n\nSource chat (`{chat}`) is a **private user chat**. You can only scrape members from **groups or channels**."
                    LOGGER.error(f"Invalid source type for task {task_id}: {chat} is a user.")
                    await bot_client.send_message(owner_id, error_msg, parse_mode='Markdown')
                    db.update_task_in_owner_doc(owner_id, task_id, {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}})
                    return

                # Ensure membership for public groups/channels
                if isinstance(entity, (types.Channel, types.Chat)):
                    try:
                        await client(JoinChannelRequest(entity))
                    except UserAlreadyParticipantError:
                        pass # Good, already a member
                
                setup_successful = True
                LOGGER.info(f"Account {me.id} confirmed access to chat {chat}")
                break # One client is enough to confirm access and join

            except (InviteHashInvalidError, InviteHashExpiredError, ValueError, TypeError) as e:
                LOGGER.warning(f"Account {me.id} failed to process chat {chat}: {e}")
                continue # Try the next account
        
        if not setup_successful:
            error_msg = f"**Task {task_id} Paused!**\n\nNone of your accounts could join or access the chat: `{chat}`. Please ensure the link/ID is correct and your accounts are not banned from it."
            LOGGER.error(f"All accounts failed to setup chat {chat} for task {task_id}")
            await bot_client.send_message(owner_id, error_msg, parse_mode='Markdown')
            db.update_task_in_owner_doc(owner_id, task_id, {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}})
            return
    # --- End of Auto-Join and Validation ---

    scrape_client = active_clients_for_task[0]
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
            await bot_client.send_message(owner_id, "No new members found to scrape. Pausing task.")
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
                if acc_info and acc_info.get('logged_in') and not acc_info.get('is_banned_for_adding') and (acc_info.get('flood_wait_until') or 0) < time.time():
                    
                    today_start_ts = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
                    last_add_timestamp = acc_info.get('last_add_date') or 0
                    if last_add_timestamp < today_start_ts:
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
            await bot_client.send_message(owner_id, f"Adding task {task_id} has been cancelled.")
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

async def get_chat_title(client, chat_id):
    """
    More robustly gets a chat title. Handles cases where the entity is not in cache.
    """
    try:
        entity = await client.get_entity(chat_id)
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
        LOGGER.warning(f"Could not resolve entity for chat_id {chat_id}: {e}")
        return f"ID: `{chat_id}` (Unresolved)"
    except Exception as e:
        LOGGER.error(f"Unexpected error getting title for chat {chat_id}: {e}")
        return f"ID: `{chat_id}` (Error)"
