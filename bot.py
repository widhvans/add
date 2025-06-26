import logging
import asyncio
from telethon import TelegramClient

from config import config
import db
import handlers
import menus
import members_adder

# --- CRITICAL FIX: Logging Setup ---
# Configure logging to output to console and file for robust tracking
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO, # Set to INFO for general activity, DEBUG for more detailed messages
    handlers=[
        logging.StreamHandler(), # Output to console
        logging.FileHandler('bot.log', mode='a') # Output to a file named bot.log
    ]
)
LOGGER = logging.getLogger(__name__)
# --- END Logging Setup ---

BOT_USERNAME = None
# Initialize the TelegramClient instance
bot = TelegramClient('bot_session', config.API_ID, config.API_HASH, **config.device_info)

async def main():
    global BOT_USERNAME
    try:
        LOGGER.info("Starting bot initialization...")
        
        # Initialize MongoDB connection
        db.init_db() 
        LOGGER.info("MongoDB connection initialized.")
        
        # Connect the bot client to Telegram
        LOGGER.info("Connecting bot to Telegram servers...")
        await bot.start(bot_token=config.BOT_TOKEN)
        me = await bot.get_me()
        BOT_USERNAME = me.username
        LOGGER.info(f"Bot started as @{BOT_USERNAME}. Telegram API connection successful.")
        
        # --- CRITICAL FIX: Register handlers *after* the bot client is fully initialized and connected ---
        # Pass the bot instance to the handlers module so it can register events
        handlers.register_all_handlers(bot) 
        LOGGER.info("All event handlers registered successfully.")
        
        # Pass the config instance to members_adder module (no direct import there)
        members_adder.set_config_instance(config)
        
        LOGGER.info("Initializing member adding clients and tasks from database...")
        
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
                    # Log if an account will be cleaned up to provide feedback
                    if not acc.get('logged_in') or not acc.get('session_string'):
                        LOGGER.warning(f"Account {acc_id} for owner {owner_id} is not logged in or has no session. It will be removed from 'Manage Accounts' display.")


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
        
        LOGGER.info("Bot is fully operational and listening for events. Press Ctrl+C to stop.")
        # Run the bot until disconnected. This keeps the event loop alive.
        await bot.run_until_disconnected()

    except Exception as e:
        LOGGER.critical(f"BOT CRITICAL ERROR: An unhandled exception occurred during startup: {e}", exc_info=True) # Use exc_info=True to print full traceback
    finally:
        LOGGER.info("Stopping bot and performing cleanup...")
        
        # Stop all member adding clients
        for client in list(members_adder.USER_CLIENTS.values()):
            if client.is_connected():
                # CRITICAL FIX: Simply disconnect and log the client's ID, not session.get_update_info()
                try:
                    me_info = await client.get_me()
                    log_id = me_info.id if me_info else "Unknown"
                    LOGGER.info(f"Disconnecting member adding client ID: {log_id}")
                except Exception as ex:
                    LOGGER.warning(f"Could not get info for client during disconnect logging: {ex}")
                    LOGGER.info(f"Disconnecting member adding client.")
                await client.disconnect()
        
        # Cancel all active adding tasks
        for task_id in list(members_adder.ACTIVE_ADDING_TASKS.keys()):
            if task_id in members_adder.ACTIVE_ADDING_TASKS:
                task = members_adder.ACTIVE_ADDING_TASKS[task_id]
                if not task.done():
                    task.cancel() # Request cancellation
                    try:
                        await task # Await cancellation to complete
                    except asyncio.CancelledError:
                        LOGGER.info(f"Task {task_id} successfully cancelled during shutdown.")
                del members_adder.ACTIVE_ADDING_TASKS[task_id]

        # Clean up any residual login clients that might be active but not fully processed
        if hasattr(handlers, 'ONGOING_LOGIN_CLIENTS'): # Check if attribute exists
            for user_id in list(handlers.ONGOING_LOGIN_CLIENTS.keys()):
                client = handlers.ONGOING_LOGIN_CLIENTS.pop(user_id)
                if client.is_connected():
                    try:
                        me_info = await client.get_me()
                        log_id = me_info.id if me_info else "Unknown"
                        LOGGER.info(f"Disconnecting leftover temporary login client for user {user_id}, client ID: {log_id}")
                    except Exception as ex:
                        LOGGER.warning(f"Could not get info for leftover client during disconnect logging: {ex}")
                        LOGGER.info(f"Disconnecting leftover temporary login client for user {user_id}")
                    await client.disconnect()

        db.close_db() # Close MongoDB connection
        LOGGER.info("All processes stopped. Bot gracefully shut down.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        LOGGER.info("Bot stopped manually by KeyboardInterrupt.")
    except Exception as final_e:
        logging.critical(f"Unhandled exception during final shutdown: {final_e}", exc_info=True)
