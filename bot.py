import logging
import asyncio
from telethon import TelegramClient

import config
import db # Import our new db module
import handlers # Import our new handlers module
import menus # Import our new menus module
import members_adder # Import core adding logic

# --- Basic Setup ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
LOGGER = logging.getLogger(__name__)

# --- BOT & DEVICE SIMULATION SETUP ---
BOT_USERNAME = None
# device_info is now in config.py
bot = TelegramClient('bot_session', config.API_ID, config.API_HASH)

async def main():
    global BOT_USERNAME
    try:
        db.init_db() # Initialize MongoDB connection
        
        await bot.start(bot_token=config.BOT_TOKEN)
        me = await bot.get_me()
        BOT_USERNAME = me.username
        LOGGER.info(f"Bot started as @{BOT_USERNAME}.")
        
        # Pass the main bot client to other modules that need it for sending messages
        handlers.set_bot_client_for_handlers(bot)
        members_adder.set_bot_client(bot) # Pass the main bot client to members_adder
        
        LOGGER.info("Initializing member adding clients and tasks...")
        
        all_owners = db.users_db.find({})
        member_account_count = 0
        active_adding_tasks_count = 0
        for owner_doc in all_owners:
            owner_id = owner_doc.get('chat_id')
            # Connect all user clients for adding
            for acc in owner_doc.get('user_accounts', []):
                acc_id = acc.get('account_id')
                if acc_id and acc.get('logged_in'):
                    client = await members_adder.get_user_client(acc_id)
                    if client:
                        LOGGER.info(f"Loaded and connected member adding client {acc_id} for owner {owner_id}.")
                        member_account_count += 1
            
            # Restart active adding tasks
            for task in owner_doc.get('adding_tasks', []):
                if task.get('is_active'):
                    LOGGER.info(f"Restarting active adding task {task.get('task_id')} for owner {owner_id}")
                    db.update_task_in_owner_doc(
                        owner_id, task.get('task_id'),
                        {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}}
                    )
                    await members_adder.start_adding_task(owner_id, task.get('task_id'))
                    active_adding_tasks_count += 1
        
        LOGGER.info(f"Member Adding Initialization complete. Loaded {member_account_count} accounts and restarted {active_adding_tasks_count} tasks.")

        await bot.run_until_disconnected()
    except Exception as e:
        LOGGER.critical(f"BOT CRITICAL ERROR: {e}")
    finally:
        LOGGER.info("Stopping bot and all user workers...")
        # Stop all member adding clients
        for client in list(members_adder.USER_CLIENTS.values()):
            if client.is_connected():
                await client.disconnect()
        # Cancel all active adding tasks
        for task_id in list(members_adder.ACTIVE_ADDING_TASKS.keys()):
            if task_id in members_adder.ACTIVE_ADDING_TASKS:
                members_adder.ACTIVE_ADDING_TASKS[task_id].cancel()
                del members_adder.ACTIVE_ADDING_TASKS[task_id]
        
        db.close_db() # Close MongoDB connection
        LOGGER.info("All processes stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        LOGGER.info("Bot stopped manually.")
