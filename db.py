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

        unique_db_identifier = config.BOT_TOKEN.split(':')[0]
        db_name = f"member_adding_bot_db_{unique_db_identifier}"

        users_db = mongo_client[db_name].users
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
    owner_data = users_db.find_one(
        {"chat_id": owner_id, "user_accounts.account_id": account_id}, # Filter to find the owner and the specific account
        {"user_accounts.$": 1} # Project only the matching account
    )
    if owner_data and 'user_accounts' in owner_data and owner_data['user_accounts']:
        return owner_data['user_accounts'][0] # Return the found account
    return None

# CRITICAL FIX: Modified update_user_account_in_owner_doc to use arrayFilters
def update_user_account_in_owner_doc(owner_id, account_id, update_fields_dict):
    """
    Updates specific fields of a user account within the 'user_accounts' array
    for a given owner, using arrayFilters ($[elem]) for precise targeting.
    
    owner_id: The chat_id of the bot owner.
    account_id: The unique account_id of the specific member-adding account within user_accounts.
    update_fields_dict: A dictionary of fields to update, e.g., {'logged_in': True, 'session_string': '...'}.
                        The keys should be the field names within the 'user_accounts' sub-document.
    """
    set_operations = {}
    unset_operations = {}
    
    for key, value in update_fields_dict.items():
        if value is None: # Use $unset for None values to remove fields if needed
            unset_operations[f"user_accounts.$[elem].{key}"] = ""
        else:
            set_operations[f"user_accounts.$[elem].{key}"] = value

    update_query = {}
    if set_operations:
        update_query["$set"] = set_operations
    if unset_operations:
        update_query["$unset"] = unset_operations

    return users_db.update_one(
        {"chat_id": owner_id},
        update_query,
        array_filters=[{"elem.account_id": account_id}] # Target the specific element in the array
    )


def get_task_in_owner_doc(owner_id, task_id):
    owner_data = users_db.find_one({"chat_id": owner_id})
    if owner_data:
        return next((t for t in owner_data.get('adding_tasks', []) if t.get('task_id') == task_id), None)
    return None

def update_task_in_owner_doc(owner_id, task_id, update_query):
    return users_db.update_one({"chat_id": owner_id, "adding_tasks.task_id": task_id}, update_query)
