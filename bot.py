import logging
import asyncio
from telethon import TelegramClient

from config import config
import db
import handlers
import menus
import members_adder

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', mode='a')
    ]
)
LOGGER = logging.getLogger(__name__)
# --- END Logging Setup ---

BOT_USERNAME = None
bot = TelegramClient('bot_session', config.API_ID, config.API_HASH, **config.device_info)


async def initialize_from_db():
    """
    This function runs in the background to initialize all user clients and
    active tasks from the database without blocking the main bot.
    """
    LOGGER.info("Starting background initialization of clients and tasks from DB...")
    await asyncio.sleep(2) # Brief pause to ensure bot is fully listening

    try:
        all_owners = db.users_db.find({})
        member_account_count = 0
        active_adding_tasks_count = 0
        
        for owner_doc in all_owners:
            owner_id = owner_doc.get('chat_id')
            
            # Connect all user clients for adding
            if 'user_accounts' in owner_doc:
                for acc in owner_doc.get('user_accounts', []):
                    acc_id = acc.get('account_id')
                    if acc_id and acc.get('logged_in') and acc.get('session_string'):
                        client = await members_adder.get_user_client(acc_id)
                        if client:
                            LOGGER.info(f"Loaded and connected member adding client {acc_id} for owner {owner_id}.")
                            member_account_count += 1
                    else:
                        if not acc.get('logged_in') or not acc.get('session_string'):
                            LOGGER.warning(f"Account {acc_id} for owner {owner_id} is not logged in or has no session.")

            # Restart active adding tasks
            if 'adding_tasks' in owner_doc:
                for task in owner_doc.get('adding_tasks', []):
                    if task.get('is_active'):
                        LOGGER.info(f"Attempting to restart active adding task {task.get('task_id')} for owner {owner_id}")
                        db.update_task_in_owner_doc(
                            owner_id, task.get('task_id'),
                            {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}}
                        )
                        await members_adder.start_adding_task(owner_id, task.get('task_id'))
                        active_adding_tasks_count += 1
        
        LOGGER.info(f"Background initialization complete. Loaded {member_account_count} accounts and restarted {active_adding_tasks_count} tasks.")

    except Exception as e:
        LOGGER.error(f"Error during background DB initialization: {e}", exc_info=True)


async def main():
    global BOT_USERNAME
    try:
        LOGGER.info("Starting bot initialization...")
        
        db.init_db() 
        LOGGER.info("MongoDB connection initialized.")
        
        await bot.start(bot_token=config.BOT_TOKEN)
        me = await bot.get_me()
        BOT_USERNAME = me.username
        LOGGER.info(f"Bot started as @{BOT_USERNAME}. Telegram API connection successful.")
        
        handlers.set_bot_client_for_modules(bot)
        menus.set_bot_client(bot)
        LOGGER.info("All event handlers registered successfully.")
        
        members_adder.set_config_instance(config)
        
        # --- FIX: Run the DB initialization in the background ---
        LOGGER.info("Scheduling user accounts initialization to run in the background.")
        asyncio.create_task(initialize_from_db())
        
        LOGGER.info("Bot is fully operational and listening for events. Press Ctrl+C to stop.")
        await bot.run_until_disconnected()

    except Exception as e:
        LOGGER.critical(f"BOT CRITICAL ERROR: An unhandled exception occurred during startup: {e}", exc_info=True)
    finally:
        LOGGER.info("Stopping bot and performing cleanup...")
        
        for client in list(members_adder.USER_CLIENTS.values()):
            if client.is_connected():
                try:
                    me_info = await client.get_me()
                    log_id = me_info.id if me_info else "Unknown"
                    LOGGER.info(f"Disconnecting member adding client ID: {log_id}")
                except Exception as ex:
                    LOGGER.warning(f"Could not get info for client during disconnect logging: {ex}")
                await client.disconnect()
        
        for task_id in list(members_adder.ACTIVE_ADDING_TASKS.keys()):
            if task_id in members_adder.ACTIVE_ADDING_TASKS:
                task = members_adder.ACTIVE_ADDING_TASKS[task_id]
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        LOGGER.info(f"Task {task_id} successfully cancelled.")
                del members_adder.ACTIVE_ADDING_TASKS[task_id]

        if hasattr(handlers, 'ONGOING_LOGIN_CLIENTS'):
            for user_id in list(handlers.ONGOING_LOGIN_CLIENTS.keys()):
                client = handlers.ONGOING_LOGIN_CLIENTS.pop(user_id)
                if client.is_connected():
                    await client.disconnect()

        db.close_db()
        LOGGER.info("All processes stopped. Bot gracefully shut down.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        LOGGER.info("Bot stopped manually by KeyboardInterrupt.")
    except Exception as final_e:
        logging.critical(f"Unhandled exception during final shutdown: {final_e}", exc_info=True)
