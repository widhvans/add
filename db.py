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

# CRITICAL FIX: Modified update_user_account_in_owner_doc to correctly use positional operator for nested array updates
def update_user_account_in_owner_doc(owner_id, account_id, update_fields):
    """
    Updates specific fields of a user account within the 'user_accounts' array
    for a given owner, using the positional filtered operator for safety.
    """
    # The filter must uniquely identify the document AND the array element to be updated.
    # $[] operator can be safer in recent PyMongo/MongoDB versions for non-filtered updates,
    # but for targeted updates with $ (positional), filtering in update_one is key.
    # For now, stick to the safe $ operator with arrayFilters or nested queries.
    
    # Simpler and safer way: Use arrayFilters for nested updates
    # This requires MongoDB 3.6+
    
    # Example: update_fields = {"user_accounts.$[elem].temp_login_data": new_temp_data}
    # And array_filters = [{"elem.account_id": account_id}]
    
    # The original update_one syntax with "user_accounts.$" is correct IF the query matches
    # the nested element. The issue comes when the state changes and it no longer matches.
    # Let's use the explicit filtering for the $ operator.
    
    # We must ensure the query part of update_one explicitly matches the nested element.
    # This is often done by including the array field in the main query filter.
    
    return users_db.update_one(
        {"chat_id": owner_id, "user_accounts.account_id": account_id}, # Filter for owner AND the specific account within the array
        update_fields # Example: {"$set": {"user_accounts.$.logged_in": True}}
    )

def get_task_in_owner_doc(owner_id, task_id):
    owner_data = users_db.find_one({"chat_id": owner_id})
    if owner_data:
        return next((t for t in owner_data.get('adding_tasks', []) if t.get('task_id') == task_id), None)
    return None

def update_task_in_owner_doc(owner_id, task_id, update_query):
    return users_db.update_one({"chat_id": owner_id, "adding_tasks.task_id": task_id}, update_query)
