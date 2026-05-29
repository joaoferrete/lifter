import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

HEVY_API_KEY: str = os.environ.get("HEVY_API_KEY", "")
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL: str = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")
BASE_URL: str = "https://api.hevyapp.com"
DB_PATH: Path = Path(os.environ.get("DB_PATH", "hevy.db"))
