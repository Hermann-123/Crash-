import os
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("WallStreet_OS")

class Settings:
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "8000472746:AAGPBg6Dsr7SBjX_jhzo0pFuFpB6l0xHP-M")
    ADMIN_ID: int = int(os.getenv("ADMIN_ID", "5968288964"))
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    
    # 🔑 TA NOUVELLE CLÉ API-FOOTBALL OFFICIELLE
    API_KEY_FOOTBALL: str = os.getenv("API_KEY_FOOTBALL", "99f9731b68429ed4aaf0383cd7ca8cd4")
    
    ARCHIVE_CHANNEL_ID: str = os.getenv("ARCHIVE_CHANNEL_ID", "-1003948159715")

settings = Settings()

CACHE_PORTFOLIO = {}
SENT_ALERTS = set()
