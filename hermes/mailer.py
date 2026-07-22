"""Gmail channel: send results via SMTP, receive tasks via IMAP.

Uses a Gmail account with an App Password (requires 2-factor authentication
on the Google account). Only mail from the configured owner address is
accepted as tasks.
"""

from __future__ import annotations

import email
import email.policy
import imaplib
import mimetypes
import smtplib
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path

from . import config

_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 465
_IMAP_HOST = "imap.gmail.com"

# Stay under Gmail's 25 MB message cap.
_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


def is_configured() -> bool:
    return bool(config.GMAIL_ADDRESS and config.GMAIL_APP_PASSWORD and config.OWNER_EMAIL)


def send(
    subject: str,
    body: str,
    attachments: list[Path] | None = None,
    in_reply_to: str | None = None,
) -> bool:
    """Email the owner. Returns True on success. Never raises —
    a failed notification must not kill a running task."""
    if not is_configured():
        print("[mail] not configured (GMAIL_ADDRESS / GMAIL_APP_PASSWORD / HERMES_OWNER_EMAIL missing)")
        return False

    msg = EmailMessage()
    msg["From"] = config.GMAIL_ADDRESS
    msg["To"] = config.OWNER_EMAIL
    msg["Subject"] = subject
    if in_reply_to:
        # Thread the reply under the owner's original email.
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)

    total = 0
    for path in attachments or []:
        try:
            data = path.read_bytes()
        except OSError as exc:
            print(f"[mail] skipping attachment {path.name}: {exc}")
            continue
        total += len(data)
        if total > _MAX_ATTACHMENT_BYTES:
            print(f"[mail] skipping attachment {path.name}: total size over limit")
            continue
        ctype, _ = mimetypes.guess_type(path.name)
        maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=path.name)

    try:
        with smtplib.SMTP_SSL(_SMTP_HOST, _SMTP_PORT, timeout=60) as smtp:
            smtp.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
            smtp.send_message(msg)
        return True
    except Exception as exc:  # noqa: BLE001 - notifications are best-effort
        print(f"[mail] send failed: {exc}")
        return False


def _body_text(msg: email.message.Message) -> str:
    """Extract the plain-text body of an email."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(
                part.get("Content-Disposition", "")
            ):
                payload = part.get_payload(decode=True)
                if payload is not None:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        return ""
    payload = msg.get_payload(decode=True)
    if payload is None:
        return ""
    charset = msg.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def fetch_new_tasks() -> list[dict]:
    """Read unseen inbox mail from the owner and return task dicts.

    Each dict has: description, from_addr, message_id, subject.
    All fetched mail is marked as read so it is processed only once.
    Never raises — a mail outage must not kill the worker loop.
    """
    if not is_configured():
        return []

    tasks: list[dict] = []
    try:
        with imaplib.IMAP4_SSL(_IMAP_HOST) as imap:
            imap.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
            imap.select("INBOX")
            status, data = imap.search(None, "UNSEEN")
            if status != "OK":
                return []
            for num in data[0].split():
                status, msg_data = imap.fetch(num, "(RFC822)")
                if status != "OK" or not msg_data or msg_data[0] is None:
                    continue
                msg = email.message_from_bytes(msg_data[0][1], policy=email.policy.default)
                _, from_addr = parseaddr(str(msg.get("From", "")))

                # Marking seen (via fetch) already happened; only the owner may queue tasks.
                if from_addr.lower() != config.OWNER_EMAIL.lower():
                    print(f"[mail] ignoring message from non-owner address: {from_addr}")
                    continue

                subject = str(msg.get("Subject", "")).strip()
                body = _body_text(msg).strip()
                description = f"{subject}\n\n{body}".strip() if body else subject
                if not description:
                    continue

                tasks.append(
                    {
                        "description": description,
                        "from_addr": from_addr,
                        "message_id": str(msg.get("Message-ID", "")).strip(),
                        "subject": subject,
                    }
                )
    except Exception as exc:  # noqa: BLE001 - keep the worker alive through mail outages
        print(f"[mail] inbox check failed: {exc}")
        return []
    return tasks
