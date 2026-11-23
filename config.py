import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env", override=True)

DEFAULT_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
PROVIDER_KEYS = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}


def startup_token() -> str:
    if DEFAULT_PROVIDER not in PROVIDER_KEYS:
        raise ValueError("LLM_PROVIDER 必須為 gemini 或 openai。")
    required = ("DISCORD_BOT_TOKEN", PROVIDER_KEYS[DEFAULT_PROVIDER])
    missing = [key for key in required if not os.getenv(key, "").strip()]
    if missing:
        raise ValueError("請在 .env 填寫 " + "、".join(missing))
    return os.environ["DISCORD_BOT_TOKEN"].strip()
