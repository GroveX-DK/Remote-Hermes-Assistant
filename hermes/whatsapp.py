"""WhatsApp notifications via the CallMeBot API (https://www.callmebot.com/)."""

from __future__ import annotations

import urllib.parse
import urllib.request

from . import config

_API_URL = "https://api.callmebot.com/whatsapp.php"
# CallMeBot handles long texts poorly; keep messages comfortably short.
_MAX_LEN = 1500


def is_configured() -> bool:
    return bool(config.CALLMEBOT_PHONE and config.CALLMEBOT_APIKEY)


def send(message: str) -> bool:
    """Send a WhatsApp message. Returns True on success, False otherwise.

    Never raises — a failed notification must not kill a running task.
    """
    if not is_configured():
        print("[whatsapp] not configured (CALLMEBOT_PHONE / CALLMEBOT_APIKEY missing)")
        return False

    text = message.strip()
    if len(text) > _MAX_LEN:
        text = text[: _MAX_LEN - 3] + "..."

    params = urllib.parse.urlencode(
        {
            "phone": config.CALLMEBOT_PHONE,
            "text": text,
            "apikey": config.CALLMEBOT_APIKEY,
        }
    )
    url = f"{_API_URL}?{params}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            ok = resp.status == 200 and "ERROR" not in body.upper()
            if not ok:
                print(f"[whatsapp] send failed: HTTP {resp.status}: {body[:200]}")
            return ok
    except Exception as exc:  # noqa: BLE001 - notifications are best-effort
        print(f"[whatsapp] send failed: {exc}")
        return False
