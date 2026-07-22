"""Configuration for the Hermes assistant.

Values come from environment variables, with a simple .env loader so the
assistant can run unattended on a machine without extra tooling.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from a .env file into os.environ (no overrides)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(PROJECT_ROOT / ".env")


# Local Ollama server.
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
MODEL = os.environ.get("HERMES_MODEL", "qwen3:8b")
# Context window for the model; agentic loops with web content need room.
NUM_CTX = int(os.environ.get("HERMES_NUM_CTX", "16384"))
# Seconds to wait for a single model response (local inference can be slow).
REQUEST_TIMEOUT = int(os.environ.get("HERMES_REQUEST_TIMEOUT", "600"))

# Gmail channel: the assistant's own account + the owner it talks to.
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
OWNER_EMAIL = os.environ.get("HERMES_OWNER_EMAIL", "")

DATA_DIR = Path(os.environ.get("HERMES_DATA_DIR", PROJECT_ROOT / "data"))
DB_PATH = DATA_DIR / "hermes.db"
OUTPUT_DIR = DATA_DIR / "outputs"

# Safety cap on agent-loop iterations per task.
MAX_ITERATIONS = int(os.environ.get("HERMES_MAX_ITERATIONS", "60"))
# How often the worker polls for new tasks in watch mode.
POLL_SECONDS = int(os.environ.get("HERMES_POLL_SECONDS", "60"))


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
