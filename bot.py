import logging
import asyncio
from telethon import TelegramClient

from config import config
import db
import handlers
import menus
import members_adder

# --- CRITICAL FIX: Logging Setup ---
# Configure logging to output to console
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO, # Set to INFO to see general bot activity, DEBUG for more detailed messages
    handlers=[
        logging.StreamHandler() # This ensures logs go to the console/stdout
    ]
)
LOGGER = logging.getLogger(__name__)
# --- END Logging Setup ---

BOT_USERNAME = None
# Initialize the TelegramClient instance
bot = TelegramClient('bot_session', config.API_ID, config.API_HASH)

async def main():
    global BOT_USERNAME
    try:
        LOGGER.info("Starting bot initialization...")
        
        # Initialize MongoDB connection
        db.init_db() 
        
        # Start the bot client connection
        await bot.start(bot_token=config.BOT_TOKEN)
        me = await bot.get_me()
        BOT_USERNAME = me.username
        LOGGER.info(f"Bot started as @{BOT_USERNAME}. Telegram API connection successful.")
        
        # --- CRITICAL FIX: Register handlers *after* the bot client is fully initialized and started ---
        # Pass the bot instance to the handlers module so it can register events
        handlers.register_all_handlers(bot) 
        
        # Pass the config instance to members_adder module (no direct import there)
        members_adder.set_config_instance(config)
        
        LOGGER.info("Handlers registered successfully. Initializing member adding clients and tasks...")
        
        # Re-initialize any active member adding clients and tasks from DB
        all_owners = db.users_db.find({})
        member_account_count = 0
        active_adding_tasks_count = 0
        for owner_doc in all_owners:
            owner_id = owner_doc.get('chat_id')
            # Connect all user clients for adding
            for acc in owner_doc.get('user_accounts', []):
                acc_id = acc.get('account_id')
                # Only try to connect if it's marked as logged_in and has a session string
                if acc_id and acc.get('logged_in') and acc.get('session_string'):
                    client = await members_adder.get_user_client(acc_id)
                    if client:
                        LOGGER.info(f"Loaded and connected member adding client {acc_id} for owner {owner_id}.")
                        member_account_count += 1
                else:
                    # Clean up accounts that are not logged in or have no session string
                    # This also prevents trying to start tasks with invalid accounts
                    if not acc.get('logged_in') or not acc.get('session_string'):
                        LOGGER.warning(f"Account {acc_id} for owner {owner_id} is not logged in or has no session. Will be cleaned up on next 'Manage Accounts' visit.")


            # Restart active adding tasks
            for task in owner_doc.get('adding_tasks', []):
                if task.get('is_active'):
                    LOGGER.info(f"Attempting to restart active adding task {task.get('task_id')} for owner {owner_id}")
                    # Temporarily set to paused to ensure clean restart process
                    db.update_task_in_owner_doc(
                        owner_id, task.get('task_id'),
                        {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}}
                    )
                    # This function internally checks for active/valid accounts before actually starting
                    await members_adder.start_adding_task(owner_id, task.get('task_id'))
                    active_adding_tasks_count += 1
        
        LOGGER.info(f"Member Adding Initialization complete. Loaded {member_account_count} accounts and restarted {active_adding_tasks_count} tasks.")
        
        LOGGER.info("Bot is fully operational and listening for events.")
        # Run the bot until disconnected. This keeps the event loop alive.
        await bot.run_until_disconnected()

    except Exception as e:
        LOGGER.critical(f"BOT CRITICAL ERROR: {e}", exc_info=True) # Use exc_info=True to print full traceback
    finally:
        LOGGER.info("Stopping bot and performing cleanup...")
        
        # Stop all member adding clients
        for client in list(members_adder.USER_CLIENTS.values()):
            if client.is_connected():
                await client.disconnect()
        
        # Cancel all active adding tasks
        for task_id in list(members_adder.ACTIVE_ADDING_TASKS.keys()):
            if task_id in members_adder.ACTIVE_ADDING_TASKS:
                task = members_adder.ACTIVE_ADDING_TASKS[task_id]
                if not task.done():
                    task.cancel()
                del members_adder.ACTIVE_ADDING_TASKS[task_id]

        db.close_db() # Close MongoDB connection
        LOGGER.info("All processes stopped. Bot gracefully shut down.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        LOGGER.info("Bot stopped manually by KeyboardInterrupt.")
