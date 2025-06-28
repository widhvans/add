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
    level=logging.INFO, # Set to INFO to see general bot activity, DEBUG for more detailed messages
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

        # --- FIX: Inject bot client instance into other modules ---
        menus.set_bot_client(bot)
        members_adder.set_bot_client(bot)
        LOGGER.info("Bot client instances injected into modules.")
        # --- END FIX ---
        
        # Pass the bot instance to the handlers module so it can register events
        handlers.register_all_handlers(bot) 
        LOGGER.info("All event handlers registered successfully.")
        
        # Pass the config instance to members_adder module (no direct import there)
        members_adder.set_config_instance(config)
        
        LOGGER.info("Initializing member adding clients and tasks from database...")
        
        # Re-initialize any active member adding clients and tasks from DB
        all_owners = await db.users_db.find({}).to_list(length=None) # Use async find and convert cursor
        member_account_count = 0
        paused_on_restart_count = 0 # New counter for paused tasks
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

            # --- MODIFICATION: Pause active tasks on restart instead of auto-starting ---
            for task in owner_doc.get('adding_tasks', []):
                if task.get('is_active'):
                    LOGGER.info(f"Pausing previously active task {task.get('task_id')} for owner {owner_id} on bot restart.")
                    await db.update_task_in_owner_doc(
                        owner_id, task.get('task_id'),
                        {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}}
                    )
                    paused_on_restart_count += 1
        
        LOGGER.info(f"Member Adding Initialization complete. Loaded {member_account_count} accounts and paused {paused_on_restart_count} previously active tasks.")
        
        LOGGER.info("Bot is fully operational and listening for events. Press Ctrl+C to stop.")
        # Run the bot until disconnected. This keeps the event loop alive.
        await bot.run_until_disconnected()

    except Exception as e:
        LOGGER.critical(f"BOT CRITICAL ERROR: An unhandled exception occurred during startup: {e}", exc_info=True)
    finally:
        LOGGER.info("Stopping bot and performing cleanup...")
        
        # Stop all member adding clients
        for client in list(members_adder.USER_CLIENTS.values()):
            if client.is_connected():
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
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        LOGGER.info(f"Task {task_id} successfully cancelled during shutdown.")
                del members_adder.ACTIVE_ADDING_TASKS[task_id]

        # Clean up any residual login clients
        if hasattr(handlers, 'ONGOING_LOGIN_CLIENTS'):
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

        db.close_db()
        LOGGER.info("All processes stopped. Bot gracefully shut down.")

if __name__ == "__main__":
    try:
        # For PyMongo with asyncio, you need an async-compatible driver like motor
        # Since the original code uses pymongo, I'll assume it's running in a separate thread
        # or the DB operations are fast enough not to block the loop significantly.
        # A small change to db.py might be needed if you face event loop blocked warnings.
        asyncio.run(main())
    except KeyboardInterrupt:
        LOGGER.info("Bot stopped manually by KeyboardInterrupt.")
    except Exception as final_e:
        logging.critical(f"Unhandled exception during final shutdown: {final_e}", exc_info=True)
