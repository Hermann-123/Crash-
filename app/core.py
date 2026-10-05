import os
import logging
import asyncio
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("WallStreet_OS_V3")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


class Settings:
    TELEGRAM_BOT_TOKEN: str = _env("TELEGRAM_BOT_TOKEN")
    ADMIN_ID: int = int(_env("ADMIN_ID", "0") or "0")
    ODDS_API_KEY: str = _env("ODDS_API_KEY")
    API_FOOTBALL_KEY: str = _env("API_FOOTBALL_KEY")
    ARCHIVE_CHANNEL_ID: str = _env("ARCHIVE_CHANNEL_ID")
    PORT: int = int(_env("PORT", "8080"))

    SCAN_INTERVAL_MINUTES: int = int(_env("SCAN_INTERVAL_MINUTES", "45"))
    MAX_MATCHES_PER_SCAN: int = int(_env("MAX_MATCHES_PER_SCAN", "20"))

    MIN_EDGE_PERCENT: float = float(_env("MIN_EDGE_PERCENT", "4.0"))
    MIN_CONFIDENCE_PERCENT: float = float(_env("MIN_CONFIDENCE_PERCENT", "58.0"))
    MIN_SAFE_PROBABILITY: float = float(_env("MIN_SAFE_PROBABILITY", "62.0"))
    MIN_VALUE_PROBABILITY: float = float(_env("MIN_VALUE_PROBABILITY", "56.0"))

    DATA_DIR: str = _env("DATA_DIR", "data")


settings = Settings()

DATA_PATH = Path(settings.DATA_DIR)
DATA_PATH.mkdir(parents=True, exist_ok=True)

CACHE_PORTFOLIO = {}
LAST_SCAN_SUMMARY = {}
PIPELINE_LOCK = asyncio.Lock()
MANUAL_ANALYSIS_CACHE = {}
