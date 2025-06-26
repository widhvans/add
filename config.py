# config.py
import os

class AppConfig: # Renamed the class to avoid potential name collision with the module 'config'
    # Telegram API credentials (Get these from my.telegram.org)
    API_ID = int(os.environ.get("API_ID", "10389378"))
    API_HASH = os.environ.get("API_HASH", "cdd5c820cb6abeecaef38e2bb8db4860")
    
    # Bot Token (Get this from @BotFather)
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "7320891454:AAHp3AAIZK2RKIkWyYIByB_fSEq9Xuk9-bk")
    
    # MongoDB Connection URL
    MONGODB_URL = os.environ.get("MONGODB_URL", "mongodb+srv://soniji:chaloji@cluster0.i5zy74f.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
    
    # Your Telegram User ID (The owner of the bot)
    OWNER_ID = int(os.environ.get("OWNER_ID", "1938030055"))
    
    # Updates Channel URL (Optional: For force subscription)
    UPDATES_CHANNEL_URL = os.environ.get("UPDATES_CHANNEL_URL", "https://t.me/your_updates_channel") 
    # Force Subscribe Channel ID (Optional: User must join this channel to use the bot)
    # Example: -1001234567890 (for a channel) or 'your_channel_username' (if public)
    FORCE_SUB_CHANNEL = os.environ.get("FORCE_SUB_CHANNEL", None) 

    # URL for the start image (Optional: Recommended to use a telegra.ph or other direct link)
    START_IMAGE_URL = os.environ.get("START_IMAGE_URL", "https://iili.io/FATfoQV.md.jpg") 

    # Device information for Telethon sessions (mimics a real mobile client)
    device_info = {
        'device_model': 'Samsung Galaxy S25 Ultra', 
        'system_version': 'SDK 35 (Android 15)', 
        'app_version': '11.5.0 (5124)', 
        'lang_code': 'en',
        'system_lang_code': 'en-US'
    }

    # Member Adding Bot specific configurations
    MAX_DAILY_ADDS_PER_ACCOUNT = int(os.environ.get("MAX_DAILY_ADDS_PER_ACCOUNT", 20)) 
    SOFT_ADD_LIMIT_ERRORS = int(os.environ.get("SOFT_ADD_LIMIT_ERRORS", 15)) 
    
    # Delays for member adding (in seconds)
    MIN_ADD_DELAY = float(os.environ.get("MIN_ADD_DELAY", 5.0)) 
    MAX_ADD_DELAY = float(os.environ.get("MAX_ADD_DELAY", 15.0)) 
    
    # Maximum members to scrape from a source chat in one go
    MEMBER_SCRAPE_LIMIT = int(os.environ.get("MEMBER_SCRAPE_LIMIT", 500)) 

    # Cooldown for bot owner commands (e.g., if you had a /download feature, time between downloads)
    USER_COOLDOWN_SECONDS = int(os.environ.get("USER_COOLDOWN_SECONDS", 1)) 

# Create a global instance of the AppConfig class
# This is the object that other modules will import and use.
config = AppConfig()
