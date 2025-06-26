import logging
import asyncio
from telethon import TelegramClient

from config import config
import db
import handlers
import menus
import members_adder

# --- CRITICAL FIX: Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler()
    ]
)
LOGGER = logging.getLogger(__name__)
# --- END Logging Setup ---

BOT_USERNAME = None
bot = TelegramClient('bot_session', config.API_ID, config.API_HASH)

async def main():
    global BOT_USERNAME
    try:
        LOGGER.info("Starting bot initialization...")
        
        db.init_db()
        
        await bot.start(bot_token=config.BOT_TOKEN)
        me = await bot.get_me()
        BOT_USERNAME = me.username
        LOGGER.info(f"Bot started as @{BOT_USERNAME}. Telegram API connection successful.")
        
        handlers.set_bot_client_for_modules(bot)
        members_adder.set_bot_client(bot)
        members_adder.set_config_instance(config)
        
        LOGGER.info("Handlers registered successfully. Initializing member adding clients and tasks...")
        
        all_owners = db.users_db.find({})
        member_account_count = 0
        active_adding_tasks_count = 0
        for owner_doc in all_owners:
            owner_id = owner_doc.get('chat_id')
            for acc in owner_doc.get('user_accounts', []):
                acc_id = acc.get('account_id')
                if acc_id and acc.get('logged_in') and acc.get('session_string'):
                    client = await members_adder.get_user_client(acc_id)
                    if client:
                        LOGGER.info(f"Loaded and connected member adding client {acc_id} for owner {owner_id}.")
                        member_account_count += 1
                else:
                    if not acc.get('logged_in') or not acc.get('session_string'):
                        LOGGER.warning(f"Account {acc_id} for owner {owner_id} is not logged in or has no session. Will be cleaned up on next 'Manage Accounts' visit.")


            for task in owner_doc.get('adding_tasks', []):
                if task.get('is_active'):
                    LOGGER.info(f"Attempting to restart active adding task {task.get('task_id')} for owner {owner_id}")
                    db.update_task_in_owner_doc(
                        owner_id, task.get('task_id'),
                        {"$set": {"adding_tasks.$.is_active": False, "adding_tasks.$.status": "paused"}}
                    )
                    await members_adder.start_adding_task(owner_id, task.get('task_id'))
                    active_adding_tasks_count += 1
        
        LOGGER.info(f"Member Adding Initialization complete. Loaded {member_account_count} accounts and restarted {active_adding_tasks_count} tasks.")
        
        LOGGER.info("Bot is fully operational and listening for events.")
        await bot.run_until_disconnected()

    except Exception as e:
        LOGGER.critical(f"BOT CRITICAL ERROR: {e}", exc_info=True)
    finally:
        LOGGER.info("Stopping bot and performing cleanup...")
        
        for client in list(members_adder.USER_CLIENTS.values()):
            if client.is_connected():
                await client.disconnect()
        
        for task_id in list(members_adder.ACTIVE_ADDING_TASKS.keys()):
            if task_id in members_adder.ACTIVE_ADDING_TASKS:
                task = members_adder.ACTIVE_ADDING_TASKS[task_id]
                if not task.done():
                    task.cancel()
                del members_adder.ACTIVE_ADDING_TASKS[task_id]

        # Clean up any residual login clients that might be active
        for user_id in list(handlers.ONGOING_LOGIN_CLIENTS.keys()):
            client = handlers.ONGOING_LOGIN_CLIENTS.pop(user_id)
            if client.is_connected():
                await client.disconnect()
                LOGGER.info(f"Disconnected leftover login client for user {user_id}")

        db.close_db()
        LOGGER.info("All processes stopped. Bot gracefully shut down.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        LOGGER.info("Bot stopped manually by KeyboardInterrupt.")
