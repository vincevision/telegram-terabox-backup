import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    API_ID = int(os.getenv("API_ID", 0))
    API_HASH = os.getenv("API_HASH", "")
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")

    USE_USERBOT = os.getenv("USE_USERBOT", "true").lower() == "true"
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))

    TERABOX_COOKIE = os.getenv("TERABOX_COOKIE", "")
    TERABOX_JSTOKEN = os.getenv("TERABOX_JSTOKEN", "")

    DEFAULT_TERABOX_FOLDER = os.getenv("DEFAULT_TERABOX_FOLDER", "/TelegramBackup")
    TEMP_DOWNLOAD_DIR = os.getenv("TEMP_DOWNLOAD_DIR", "./downloads")
    DATABASE_PATH = os.getenv("DATABASE_PATH", "./backup_tracker.db")

    @classmethod
    def validate(cls):
        errors = []
        if not cls.API_ID:
            errors.append("API_ID is missing")
        if not cls.API_HASH:
            errors.append("API_HASH is missing")
        if not cls.ADMIN_USER_ID:
            errors.append("ADMIN_USER_ID is missing")
        if not cls.TERABOX_COOKIE:
            errors.append("TERABOX_COOKIE is missing")
        if errors:
            raise ValueError(f"Configuration errors: {', '.join(errors)}")