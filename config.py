# config.py
import os

class AppConfig:
    API_ID = int(os.environ.get("API_ID", "10389378"))
    API_HASH = os.environ.get("API_HASH", "cdd5c820cb6abeecaef38e2bb8db4860")
    
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "7634067741:AAGQAa5mDY232XXDYE21Xnx3eAvLrHrbq-k")
    
    MONGODB_URL = os.environ.get("MONGODB_URL", "mongodb+srv://soniji:chaloji@cluster0.i5zy74f.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
    
    OWNER_ID = int(os.environ.get("OWNER_ID", "1938030055"))
    
    UPDATES_CHANNEL_URL = os.environ.get("UPDATES_CHANNEL_URL", "https://t.me/your_updates_channel") 
    FORCE_SUB_CHANNEL = os.environ.get("FORCE_SUB_CHANNEL", None) 

    START_IMAGE_URL = os.environ.get("START_IMAGE_URL", "https://iili.io/FATfoQV.md.jpg") 

    device_info = {
        'device_model': 'Samsung Galaxy S25 Ultra', 
        'system_version': 'SDK 35 (Android 15)', 
        'app_version': '11.5.0 (5124)', 
        'lang_code': 'en',
        'system_lang_code': 'en-US'
    }

    MAX_DAILY_ADDS_PER_ACCOUNT = int(os.environ.get("MAX_DAILY_ADDS_PER_ACCOUNT", 20)) 
    SOFT_ADD_LIMIT_ERRORS = int(os.environ.get("SOFT_ADD_LIMIT_ERRORS", 15)) 
    
    MIN_ADD_DELAY = float(os.environ.get("MIN_ADD_DELAY", 5.0)) 
    MAX_ADD_DELAY = float(os.environ.get("MAX_ADD_DELAY", 15.0)) 
    
    MEMBER_SCRAPE_LIMIT = int(os.environ.get("MEMBER_SCRAPE_LIMIT", 500)) 

    USER_COOLDOWN_SECONDS = int(os.environ.get("USER_COOLDOWN_SECONDS", 1)) 

config = AppConfig()
