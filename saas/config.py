"""Configuration and environment variables."""

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
env_path = BASE_DIR / ".env"
if env_path.exists():
    load_dotenv(env_path, override=True)

# Telegram Bot
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# WayForPay Configuration
WAYFORPAY_MERCHANT_ACCOUNT = os.getenv("WAYFORPAY_MERCHANT_ACCOUNT", "test_merch_n1").strip()
WAYFORPAY_SECRET_KEY = os.getenv("WAYFORPAY_SECRET_KEY", "").strip()
WAYFORPAY_DOMAIN_NAME = os.getenv("WAYFORPAY_DOMAIN_NAME", "localhost").strip()
PAYMENT_WEBHOOK_SECRET = os.getenv("PAYMENT_WEBHOOK_SECRET", "wayforpay-webhook-secret").strip()
WAYFORPAY_SERVICE_URL = os.getenv("WAYFORPAY_SERVICE_URL", "").strip()

if not WAYFORPAY_SERVICE_URL:
    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if railway_domain:
        WAYFORPAY_SERVICE_URL = f"https://{railway_domain}/payments/wayforpay/{PAYMENT_WEBHOOK_SECRET}"

# Web Server
APP_HOST = os.getenv("APP_HOST", "0.0.0.0").strip()
APP_PORT = int(os.getenv("APP_PORT", "8080").strip())

# Payment Configuration
PAYMENT_PROVIDER = "wayforpay"
DEPOSIT_AMOUNT_UAH = 200
RESERVATION_TTL_MINUTES = int(os.getenv("RESERVATION_TTL_MINUTES", "30"))
WAYFORPAY_API_URL = "https://api.wayforpay.com/api"
WAYFORPAY_DEBUG = os.getenv("WAYFORPAY_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}

# Admin Configuration
def _parse_admin_ids(raw: str) -> list[int]:
    """Parse comma/semicolon-separated admin IDs from environment."""
    ids: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return ids


ADMIN_IDS = _parse_admin_ids(os.getenv("ADMIN_IDS", ""))

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Validation
if not TOKEN:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN is not set. "
        "Add it to your environment variables or to a .env file in the project root."
    )
if not WAYFORPAY_SECRET_KEY:
    raise RuntimeError(
        "WAYFORPAY_SECRET_KEY is not set. "
        "Add it to your environment variables or to a .env file in the project root."
    )
if WAYFORPAY_DOMAIN_NAME.lower() == "localhost":
    logger.warning(
        "WAYFORPAY_DOMAIN_NAME is set to localhost. WayForPay usually expects the merchant domain configured in your store."
    )
