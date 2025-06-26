import logging
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from config import config

LOGGER = logging.getLogger(__name__)

mongo_client = None
users_db = None
bot_settings_db = None

def init_db():
    global mongo_client, users_db, bot_settings_db
    try:
        mongo_client = MongoClient(config.MONGODB_URL, server_api=ServerApi('1'))

        # Derive a unique database name based on the bot token for isolation
        unique_db_identifier = config.BOT_TOKEN.split(':')[0]
        db_name = f"member_adding_bot_db_{unique_db_identifier}"

        users_db = mongo_client[db_name].users # This collection stores all user/owner data
        bot_settings_db = mongo_client[db_name].bot_settings
        mongo_client.admin.command('ping')
        LOGGER.info(f"Successfully connected to MongoDB. Using database: {db_name}")
    except Exception as e:
        LOGGER.critical(f"CRITICAL ERROR: Failed to connect to MongoDB: {e}. Exiting.")
        exit(1)

def close_db():
    if mongo_client:
        mongo_client.close()
        LOGGER.info("MongoDB connection closed.")

def get_user_data(user_id):
    return users_db.find_one({"chat_id": user_id})

def update_user_data(user_id, update_query):
    return users_db.update_one({"chat_id": user_id}, update_query)

def find_user_account_in_owner_doc(owner_id, account_id):
    owner_data = users_db.find_one({"chat_id": owner_id})
    if owner_data:
        return next((acc for acc in owner_data.get('user_accounts', []) if acc.get('account_id') == account_id), None)
    return None

def update_user_account_in_owner_doc(owner_id, account_id, update_query):
    return users_db.update_one({"chat_id": owner_id, "user_accounts.account_id": account_id}, update_query)

def get_task_in_owner_doc(owner_id, task_id):
    owner_data = users_db.find_one({"chat_id": owner_id})
    if owner_data:
        return next((t for t in owner_data.get('adding_tasks', []) if t.get('task_id') == task_id), None)
    return None

def update_task_in_owner_doc(owner_id, task_id, update_query):
    return users_db.update_one({"chat_id": owner_id, "adding_tasks.task_id": task_id}, update_query)
