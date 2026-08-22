"""
Application Configuration & Environment Loader.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_FILE = BASE_DIR / "quant_trades.db"
OUTPUT_JSON = BASE_DIR / "dashboard_data.json"
CHARTS_DIR = BASE_DIR / "data" / "charts"
STREAM_CACHE = BASE_DIR / "wepoll_latest_stream.json"
ENV_FILE = BASE_DIR / ".env"

def load_env():
    """Loads key-value pairs from .env into os.environ if present."""
    if ENV_FILE.exists():
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ[k.strip()] = v.strip().strip("\"'")
        except Exception:
            pass

load_env()
