import asyncio
import random
import time
import datetime
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

import config
# Import necessary items from bot.py
# We will explicitly pass 'users_db' from bot.py's main scope
# Or, make users_db global if you prefer to avoid passing it everywhere.
# For simplicity, let's assume it's imported globally for now if you put it in a separate db.py.
# Or, we can pass it as an argument where needed.
# For now, let's keep it simple and directly import what we need from `bot`.

# Assuming users_db is directly imported or accessible via a global scope set up in bot.py
from bot import users_db, LOGGER, device_info, bot # `bot` client is needed for sending messages back to owner
from strings import strings

# Global dictionaries for managing active adding tasks and user clients
ACTIVE_ADDING_TASKS = {} # Stores asyncio tasks for each adding task
USER_CLIENTS = {} # Stores active TelegramClient instances for user accounts

async def get_user_client(user_account_id):
    """
    Retrieves or creates a TelegramClient for a given user account (member-adding account).
    Ensures the client is connected and authorized.
    """
    if user_account_id in USER_CLIENTS and USER_CLIENTS[user_account_id].is_connected():
        return USER_CLIENTS[user_account_id]

    # Find the owner document containing this user account
    owner_data = users_db.find_one({"user_accounts.account_id": user_account_id})
    if not owner_data:
        LOGGER.warning(f"Owner data not found for member-adding account {user_account_id}.")
        return None

    # Find the specific user account within the user_accounts array
    account_info = next((acc for acc in owner_data.get('user_accounts', []) if acc.get('account_id') == user_account_id), None)
    if not account_info or not account_info.get('session_string'):
        LOGGER.warning(f"No session string found for user account {user_account_id}.")
        # Update status in DB
        users_db.update_one(
            {"user_accounts.account_id": user_account_id},
            {"$set": {"user_accounts.$.logged_in": False, "user_accounts.$.is_active_for_adding": False, "user_accounts.$.is_banned_for_adding": False}}
        )
        return None

    client = TelegramClient(StringSession(account_info['session_string']), config.API_ID, config.API_HASH, **device_info)

    try:
        if not client.is_connected():
            await client.connect()
        if not await client.is_user_authorized():
            LOGGER.warning(f"Session invalid for user account {user_account_id}. Marking as logged_in=False.")
            users_db.update_one(
                {"user_accounts.account_id": user_account_id},
                {"$set": {"user_accounts.$.logged_in": False, "user_accounts.$.is_active_for_adding": False, "user_accounts.$.is_banned_for_adding": False}}
            )
            await client.disconnect()
            return None
        
        # Cache the client
        USER_CLIENTS[user_account_id] = client
        return client
    except Exception as e:
        LOGGER.error(f"Failed to connect or authorize client for {user_account_id}: {e}")
        users_db.update_one(
            {"user_accounts.account_id": user_account_id},
            {"$set": {"user_accounts.$.logged_in": False, "user_accounts.$.is_active_for_adding": False, "user_accounts.$.is_banned_for_adding": False}}
        )
        if client.is_connected():
            await client.disconnect()
        return None

async def scrape_members(client, source_chat_id, limit=config.MEMBER_SCRAPE_LIMIT):
    """
    Scrapes members from a given source chat using a user client.
    Filters out bots and potentially inactive members.
    """
    members = []
    offset = 0
    while True:
        try:
            participants = await client(GetParticipantsRequest(
                channel=source_chat_id,
                filter=ChannelParticipantsRecent(), # Get recent participants for activity
                offset=offset,
                limit=100, # Fetch 100 participants at a time
                hash=0
            ))
            if not participants.users:
                break
            
            for user in participants.users:
                # Filter out bots, self, and blocked/deactivated users
                if not user.bot and not user.is_self and user.status and \
                   not isinstance(user.status, (type(None), functions.contacts.Blocked, functions.contacts.BlockedWait)):
                    members.append(user)
                    if len(members) >= limit:
                        break
            
            offset += len(participants.users)
            if len(members) >= limit or not participants.users:
                break
            await asyncio.sleep(random.uniform(1, 3)) # Small delay between scraping requests to be safe

        except FloodWaitError as e:
            LOGGER.warning(f"Scraping FloodWait for {client.session.dc_id}: {e.seconds}s. Waiting...")
            await asyncio.sleep(e.seconds + random.uniform(5, 10)) # Add extra buffer
        except Exception as e:
            LOGGER.error(f"Error scraping members from {source_chat_id}: {e}")
            break
    
    LOGGER.info(f"Scraped {len(members)} members from {source_chat_id}")
    return members

async def add_member_to_group(user_client, target_chat_id, member_user, task_id, account_id, owner_id):
    """
    Attempts to add a single member to a target group using a specific user client.
    Handles various errors and updates account status.
    Returns True on success, False on failure.
    """
    try:
        await user_client(functions.channels.InviteToChannelRequest(
            channel=target_chat_id,
            users=[member_user]
        ))
        LOGGER.info(f"Account {account_id}: Successfully added {member_user.id} to {target_chat_id} for Task {task_id}")
        
        # Update daily adds count for the successful account
        users_db.update_one(
            {"chat_id": owner_id, "user_accounts.account_id": account_id},
            {"$inc": {"user_accounts.$.daily_adds_count": 1}, "$set": {"user_accounts.$.last_add_date": time.time()}}
        )
        return True
    except UserAlreadyParticipantError:
        LOGGER.info(f"Account {account_id}: Member {member_user.id} already in {target_chat_id}. Skipping.")
        # This is not an error that should penalize the account much
    except UserPrivacyRestrictedError:
        LOGGER.warning(f"Account {account_id}: Member {member_user.id} has privacy restrictions for adding. Skipping.")
        # This is a soft error, increments the count but doesn't immediately suspend
        users_db.update_one(
            {"chat_id": owner_id, "user_accounts.account_id": account_id},
            {"$inc": {"user_accounts.$.soft_error_count": 1}}
        )
    except PeerFloodError as e:
        LOGGER.error(f"Account {account_id}: PeerFloodError detected! Message: {e}. Suspending account.")
        users_db.update_one(
            {"chat_id": owner_id, "user_accounts.account_id": account_id},
            {"$set": {"user_accounts.$.is_banned_for_adding": True, "user_accounts.$.last_error_time": time.time(), "user_accounts.$.error_type": "PeerFlood"}}
        )
        # Notify owner immediately via the main bot
        await bot.send_message(owner_id, strings['PEER_FLOOD_DETECTED'].format(account_id=account_id), parse_mode='html')
    except FloodWaitError as e:
        LOGGER.warning(f"Account {account_id}: FloodWaitError for {e.seconds}s. Pausing adding for this account.")
        users_db.update_one(
            {"chat_id": owner_id, "user_accounts.account_id": account_id},
            {"$set": {"user_accounts.$.flood_wait_until": time.time() + e.seconds, "user_accounts.$.last_error_time": time.time(), "user_accounts.$.error_type": "FloodWait"}}
        )
        await asyncio.sleep(e.seconds + random.uniform(5, 10)) # Add extra random delay to be safe
    except (UserBlockedError, InputUserDeactivatedError):
        LOGGER.warning(f"Account {account_id}: Target user blocked or deactivated. Skipping.")
    except Exception as e:
        LOGGER.error(f"Account {account_id}: Failed to add {member_user.id} to {target_chat_id}: {e}")
        # Increment soft error count for other general errors
        users_db.update_one(
            {"chat_id": owner_id, "user_accounts.account_id": account_id},
            {"$inc": {"user_accounts.$.soft_error_count": 1}}
        )
    return False

async def manage_adding_task(owner_id, task_id):
    """
    Manages the overall member adding process for a single task.
    Distributes adding among assigned accounts, handles limits and errors.
    """
    LOGGER.info(f"Starting to manage adding task {task_id} for owner {owner_id}")
    
    # Get the owner's main client to send progress updates
    owner_main_client = bot 

    # Fetch initial task info
    owner_doc = users_db.find_one({"chat_id": owner_id})
    if not owner_doc:
        LOGGER.error(f"Owner {owner_id} data not found for task {task_id}. Stopping task.")
        return
    task_info = next((t for t in owner_doc.get('adding_tasks', []) if t.get('task_id') == task_id), None)
    if not task_info or not task_info.get('is_active'):
        LOGGER.info(f"Task {task_id} is not active or not found. Stopping management.")
        return

    source_chat_id = task_info.get('source_chat_id')
    target_chat_ids = task_info.get('target_chat_ids', [])
    assigned_account_ids = task_info.get('assigned_accounts', [])

    if not source_chat_id or not target_chat_ids or not assigned_account_ids:
        LOGGER.warning(f"Task {task_id} missing source, target, or assigned accounts. Pausing task.")
        users_db.update_one({"chat_id": owner_id, "adding_tasks.task_id": task_id}, {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}})
        await owner_main_client.send_message(owner_id, strings['TASK_NO_SOURCE_SELECTED'] if not source_chat_id else (strings['TASK_NO_TARGET_SELECTED'] if not target_chat_ids else strings['TASK_NO_ACCOUNTS_ASSIGNED']))
        return

    # --- Step 1: Scrape Members ---
    members_to_add = []
    # Find an active, healthy client for scraping.
    # Prioritize clients that are not banned and not in flood wait.
    scrape_client_info = None
    for acc_id in assigned_account_ids:
        acc_doc = users_db.find_one({"chat_id": owner_id, "user_accounts.account_id": acc_id})
        if acc_doc:
            account_data = next((ua for ua in acc_doc['user_accounts'] if ua['account_id'] == acc_id), None)
            if account_data and account_data.get('logged_in') and \
               not account_data.get('is_banned_for_adding') and \
               account_data.get('flood_wait_until', 0) < time.time():
                scrape_client = await get_user_client(acc_id)
                if scrape_client:
                    scrape_client_info = (acc_id, scrape_client)
                    break # Found a suitable client for scraping
    
    if not scrape_client_info:
        LOGGER.warning(f"No active accounts available for scraping for task {task_id}. Pausing task.")
        users_db.update_one({"chat_id": owner_id, "adding_tasks.task_id": task_id}, {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}})
        await owner_main_client.send_message(owner_id, strings['NO_ACTIVE_ACCOUNTS_FOR_TASK'].format(task_id=task_id))
        return

    try:
        await owner_main_client.send_message(owner_id, strings['SCRAPING_MEMBERS'])
        members_to_add = await scrape_members(scrape_client_info[1], source_chat_id)
        if not members_to_add:
            await owner_main_client.send_message(owner_id, "No members found to scrape or an error occurred during scraping. Pausing task.")
            users_db.update_one({"chat_id": owner_id, "adding_tasks.task_id": task_id}, {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}})
            return
        await owner_main_client.send_message(owner_id, strings['SCRAPING_COMPLETE'].format(count=len(members_to_add)))
    except Exception as e:
        LOGGER.error(f"Failed to scrape members for task {task_id}: {e}")
        await owner_main_client.send_message(owner_id, f"Error scraping members for task {task_id}: {e}. Pausing task.")
        users_db.update_one({"chat_id": owner_id, "adding_tasks.task_id": task_id}, {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}})
        return

    # --- Step 2: Add Members ---
    current_member_index = task_info.get('current_member_index', 0)
    added_count_for_task = task_info.get('added_members_count', 0)
    total_members_to_process = len(members_to_add)

    # Shuffle members to avoid adding the same members repeatedly if restarting
    random.shuffle(members_to_add) 
    
    # Infinite loop to keep task running until paused/completed/error
    while True:
        try:
            # Re-fetch task info to check for pauses/stops
            owner_doc = users_db.find_one({"chat_id": owner_id})
            task_info = next((t for t in owner_doc.get('adding_tasks', []) if t.get('task_id') == task_id), None)
            if not task_info or not task_info.get('is_active'):
                LOGGER.info(f"Task {task_id} paused or stopped by owner. Exiting adding loop.")
                break # Exit the loop if task is no longer active

            # If all scraped members are processed, mark task as complete and break
            if current_member_index >= total_members_to_process:
                await owner_main_client.send_message(owner_id, strings['TASK_COMPLETED'].format(task_id=task_id))
                users_db.update_one({"chat_id": owner_id, "adding_tasks.task_id": task_id}, {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "completed"}})
                break

            # Find available accounts for adding
            available_accounts = []
            for acc_id in assigned_account_ids:
                acc_doc = users_db.find_one({"chat_id": owner_id, "user_accounts.account_id": acc_id})
                if acc_doc:
                    account_data = next((ua for ua in acc_doc['user_accounts'] if ua['account_id'] == acc_id), None)
                    if account_data and account_data.get('logged_in') and \
                       not account_data.get('is_banned_for_adding') and \
                       account_data.get('flood_wait_until', 0) < time.time(): # Check if not in flood wait
                        
                        # Reset daily counts if it's a new day
                        today = datetime.date.today()
                        last_add_date = account_data.get('last_add_date')
                        if last_add_date:
                            last_add_date_dt = datetime.datetime.fromtimestamp(last_add_date).date()
                            if last_add_date_dt < today:
                                users_db.update_one({"chat_id": owner_id, "user_accounts.account_id": acc_id}, {"$set": {"user_accounts.$.daily_adds_count": 0, "user_accounts.$.soft_error_count": 0, "user_accounts.$.last_add_date": time.time()}})
                                account_data['daily_adds_count'] = 0
                                account_data['soft_error_count'] = 0

                        # Check if account has not reached its daily limit or soft error limit
                        if account_data.get('daily_adds_count', 0) < config.MAX_DAILY_ADDS_PER_ACCOUNT and \
                           account_data.get('soft_error_count', 0) < config.SOFT_ADD_LIMIT_ERRORS:
                            client = await get_user_client(acc_id)
                            if client:
                                available_accounts.append((acc_id, client, account_data))
            
            if not available_accounts:
                await owner_main_client.send_message(owner_id, strings['NO_ACTIVE_ACCOUNTS_FOR_TASK'].format(task_id=task_id))
                users_db.update_one({"chat_id": owner_id, "adding_tasks.task_id": task_id}, {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}})
                break # No accounts available, pause task and exit

            # Pick an account (simple round-robin for now, can implement more complex logic)
            # Find the account with the least daily adds or lowest soft errors.
            # For simplicity, let's just pick one.
            account_id, current_user_client, account_data = random.choice(available_accounts) 

            member_to_add = members_to_add[current_member_index]
            
            # Iterate through target chats for the current member
            added_to_any_target = False
            for target_chat_id in target_chat_ids:
                success = await add_member_to_group(current_user_client, target_chat_id, member_to_add, task_id, account_id, owner_id)
                if success:
                    added_to_any_target = True
                    # Update task's total added count
                    users_db.update_one(
                        {"chat_id": owner_id, "adding_tasks.task_id": task_id},
                        {"$inc": {"adding_tasks.$.added_members_count": 1}}
                    )
                    added_count_for_task += 1
                    break # Member added to at least one target, move to next scraped member

            # If the account is now at its limit or too many errors, it won't be in available_accounts next loop
            if account_data.get('daily_adds_count', 0) >= config.MAX_DAILY_ADDS_PER_ACCOUNT or \
               account_data.get('soft_error_count', 0) >= config.SOFT_ADD_LIMIT_ERRORS:
                await owner_main_client.send_message(owner_id, strings['ADDING_LIMIT_REACHED'].format(account_id=account_id, limit=config.MAX_DAILY_ADDS_PER_ACCOUNT), parse_mode='html')

            if added_to_any_target:
                current_member_index += 1
                users_db.update_one(
                    {"chat_id": owner_id, "adding_tasks.task_id": task_id},
                    {"$set": {"adding_tasks.$.current_member_index": current_member_index}}
                )
            
            # Update task progress for owner
            progress_percent = (added_count_for_task / total_members_to_process) * 100
            
            # Find the last progress message to edit it
            # This requires storing the message_id in the task_info
            # For simplicity, let's send a new one every few adds or after a fixed interval
            
            # Send progress update every 10 adds, or immediately if starting.
            if added_count_for_task % 5 == 0 or added_count_for_task == 1:
                progress_msg_text = strings['TASK_PROGRESS'].format(
                    task_id=task_id, added_count=added_count_for_task, total_members=total_members_to_process,
                    progress=progress_percent, account_id=account_id
                )
                last_progress_message_id = task_info.get('last_progress_message_id')
                try:
                    if last_progress_message_id:
                        await owner_main_client.edit_message(owner_id, last_progress_message_id, progress_msg_text, parse_mode='html')
                    else:
                        msg = await owner_main_client.send_message(owner_id, progress_msg_text, parse_mode='html')
                        users_db.update_one(
                            {"chat_id": owner_id, "adding_tasks.task_id": task_id},
                            {"$set": {"adding_tasks.$.last_progress_message_id": msg.id}}
                        )
                except Exception as update_error:
                    LOGGER.warning(f"Could not update progress message for task {task_id}: {update_error}")
                    # If edit fails, send a new one and update ID
                    msg = await owner_main_client.send_message(owner_id, progress_msg_text, parse_mode='html')
                    users_db.update_one(
                        {"chat_id": owner_id, "adding_tasks.task_id": task_id},
                        {"$set": {"adding_tasks.$.last_progress_message_id": msg.id}}
                    )

            await asyncio.sleep(random.uniform(config.MIN_ADD_DELAY, config.MAX_ADD_DELAY)) # Delay between adding attempts

        except asyncio.CancelledError:
            LOGGER.info(f"Adding task {task_id} cancelled by owner.")
            await owner_main_client.send_message(owner_id, f"Adding task {task_id} cancelled.")
            break
        except Exception as e:
            LOGGER.error(f"Unhandled error in adding task {task_id}: {e}")
            await owner_main_client.send_message(owner_id, f"An unexpected error occurred in Task {task_id}: {e}. Pausing task.")
            users_db.update_one({"chat_id": owner_id, "adding_tasks.task_id": task_id}, {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}})
            break
    
    LOGGER.info(f"Adding task {task_id} management finished.")

async def start_adding_task(owner_id, task_id):
    """Starts an adding task by creating an asyncio task."""
    if task_id in ACTIVE_ADDING_TASKS:
        LOGGER.warning(f"Task {task_id} is already running.")
        return False
    
    owner_doc = users_db.find_one({"chat_id": owner_id})
    if not owner_doc: return False
    task_info = next((t for t in owner_doc.get('adding_tasks', []) if t.get('task_id') == task_id), None)
    if not task_info: return False

    assigned_account_ids = task_info.get('assigned_accounts', [])
    if not assigned_account_ids:
        await bot.send_message(owner_id, strings['TASK_NO_ACCOUNTS_ASSIGNED'])
        return False

    # Ensure all assigned clients are connected before starting the task
    for acc_id in assigned_account_ids:
        client = await get_user_client(acc_id)
        if not client:
            await bot.send_message(owner_id, f"Account <code>{acc_id}</code> could not be connected. Task {task_id} cannot start.", parse_mode='html')
            return False
        
    task = asyncio.create_task(manage_adding_task(owner_id, task_id))
    ACTIVE_ADDING_TASKS[task_id] = task
    users_db.update_one({"chat_id": owner_id, "adding_tasks.task_id": task_id}, {"$set": {"adding_tasks.$.is_active": True, "adding_tasks.$.status": "active"}})
    LOGGER.info(f"Adding task {task_id} started for owner {owner_id}.")
    return True

async def pause_adding_task(task_id):
    """Pauses an active adding task."""
    if task_id in ACTIVE_ADDING_TASKS:
        ACTIVE_ADDING_TASKS[task_id].cancel()
        del ACTIVE_ADDING_TASKS[task_id]
        # Update database status to paused
        owner_doc = users_db.find_one({"adding_tasks.task_id": task_id}) # Find owner by task_id
        if owner_doc:
            users_db.update_one({"chat_id": owner_doc['chat_id'], "adding_tasks.task_id": task_id}, {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}})
        LOGGER.info(f"Adding task {task_id} paused.")
        return True
    return False

async def stop_user_adding_clients(owner_id):
    """Stops all TelegramClient instances for a specific owner's member-adding accounts."""
    owner_data = users_db.find_one({"chat_id": owner_id})
    if owner_data:
        for account in owner_data.get('user_accounts', []):
            acc_id = account.get('account_id')
            if acc_id and acc_id in USER_CLIENTS:
                client = USER_CLIENTS.pop(acc_id)
                if client.is_connected():
                    await client.disconnect()
                LOGGER.info(f"Disconnected user client {acc_id} for owner {owner_id}.")

async def get_chat_title(client, chat_id):
    """Helper to get chat title from ID."""
    try:
        entity = await client.get_entity(chat_id)
        return entity.title if hasattr(entity, 'title') else entity.username or f"User {entity.id}"
    except Exception as e:
        LOGGER.warning(f"Could not get title for chat {chat_id}: {e}")
        return f"ID: <code>{chat_id}</code>"
