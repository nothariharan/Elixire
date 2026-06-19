"""Centralized .env loader — always resolves to elixir/.env regardless of CWD."""
import os
from pathlib import Path
from dotenv import load_dotenv

# walk up from this file to find the .env (lives in elixir/)
_env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=_env_path, override=True)
