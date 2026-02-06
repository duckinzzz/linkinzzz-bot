import os

from utils.logging_utils import logger

ENV = os.getenv("ENV").lower()
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", 8080))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not found")

logger.info(f"{BOT_USERNAME} starting in {ENV} mode | token ends with ...{BOT_TOKEN[-6:]}")
