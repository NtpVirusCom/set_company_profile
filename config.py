from pathlib import Path

BASE_DIR = Path(__file__).parent

DATA_DIR = BASE_DIR / "data"

LOG_DIR = BASE_DIR / "logs"

HEADLESS = True

TIMEOUT = 30000

MAX_RETRY = 3

SLEEP_SECONDS = 2

JSON_FILE = DATA_DIR / "companies.json"

CSV_FILE = DATA_DIR / "companies.csv"

HISTORY_FILE = DATA_DIR / "history.json"

LOG_FILE = LOG_DIR / "scraper.log"
