import os

class Config:
    API_ID = int(os.environ.get("API_ID", "YOUR_API_ID"))
    API_HASH = os.environ.get("API_HASH", "YOUR_API_HASH")
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")
    MONGODB_URL = os.environ.get("MONGODB_URL", "YOUR_MONGODB_URL")
    OWNER_ID = int(os.environ.get("OWNER_ID", "YOUR_OWNER_ID"))
    UPDATES_CHANNEL_URL = os.environ.get("UPDATES_CHANNEL_URL", "https://t.me/your_updates_channel")
    FORCE_SUB_CHANNEL = os.environ.get("FORCE_SUB_CHANNEL", None) # Can remove if not needed
    START_IMAGE_URL = os.environ.get("START_IMAGE_URL", "https://telegra.ph/file/0c9a4d8b6c4e0f4f9a7d0.jpg")

    MAX_DAILY_ADDS_PER_ACCOUNT = int(os.environ.get("MAX_DAILY_ADDS_PER_ACCOUNT", 20))
    SOFT_ADD_LIMIT_ERRORS = int(os.environ.get("SOFT_ADD_LIMIT_ERRORS", 15))
    MIN_ADD_DELAY = float(os.environ.get("MIN_ADD_DELAY", 5.0))
    MAX_ADD_DELAY = float(os.environ.get("MAX_ADD_DELAY", 15.0))
    MEMBER_SCRAPE_LIMIT = int(os.environ.get("MEMBER_SCRAPE_LIMIT", 500))
    USER_COOLDOWN_SECONDS = int(os.environ.get("USER_COOLDOWN_SECONDS", 1)) # Can be lower if only for bot commands
