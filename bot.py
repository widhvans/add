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
    # This function is used for top-level updates on the user document (e.g., 'state', pushing/pulling user_accounts)
    return users_db.update_one({"chat_id": user_id}, update_query)

def find_user_account_in_owner_doc(owner_id, account_id):
    owner_data = users_db.find_one({"chat_id": owner_id})
    if owner_data:
        return next((acc for acc in owner_data.get('user_accounts', []) if acc.get('account_id') == account_id), None)
    return None

# CRITICAL FIX: Modified update_user_account_in_owner_doc to correctly use arrayFilters
def update_user_account_in_owner_doc(owner_id, account_id, update_fields_dict):
    """
    Updates specific fields of a user account within the 'user_accounts' array
    for a given owner, using arrayFilters for precise targeting.
    
    update_fields_dict should be a dictionary like:
    {"user_accounts.$[account].logged_in": True, "user_accounts.$[account].temp_login_data": {}}
    """
    
    # We construct the update query with the positional filtered operator "$[account]"
    # and provide the arrayFilters to specify which 'account' element to apply it to.
    
    # Example: update_fields_dict = {
    #   "user_accounts.$[account].session_string": "new_session",
    #   "user_accounts.$[account].logged_in": True
    # }
    
    # array_filters defines which element is referred to by '$[account]'
    array_filters = [{"account.account_id": account_id}]

    return users_db.update_one(
        {"chat_id": owner_id}, # Main filter to find the owner's document
        {"$set": update_fields_dict}, # Use $set with the positional filtered operator
        array_filters=array_filters # Specify the filter for the array element
    )


def get_task_in_owner_doc(owner_id, task_id):
    owner_data = users_db.find_one({"chat_id": owner_id})
    if owner_data:
        return next((t for t in owner_data.get('adding_tasks', []) if t.get('task_id') == task_id), None)
    return None

def update_task_in_owner_doc(owner_id, task_id, update_query):
    # This also needs to be updated to use arrayFilters if updating nested fields in 'adding_tasks' array
    # For now, it might be fine if it only updates top-level task fields or uses $set for a known element.
    # If you encounter similar errors with tasks, modify this function too.
    return users_db.update_one({"chat_id": owner_id, "adding_tasks.task_id": task_id}, update_query)
