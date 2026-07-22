"""Tool definitions and dispatch for the Hermes agent.

Server-side tools (web search / web fetch) run on Anthropic's infrastructure.
Custom tools (memory, logging, files, email) are executed here.
"""

from __future__ import annotations

import os

from . import config, db, mailer

SERVER_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 15},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 15},
]

CUSTOM_TOOLS = [
    {
        "name": "save_memory",
        "description": (
            "Save a long-term memory note that persists across tasks and restarts. "
            "Use this to remember facts about the business, decisions made, useful sources, "
            "and lessons learned. Call this whenever you learn something worth keeping. "
            "Saving to an existing key overwrites that note."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Short kebab-case identifier, e.g. 'competitor-pricing' or 'preferred-suppliers'.",
                },
                "content": {"type": "string", "description": "The note content."},
            },
            "required": ["key", "content"],
        },
    },
    {
        "name": "read_memory",
        "description": "Read the full content of a saved memory note by its key. The list of available keys is provided in your task context.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "The memory key to read."},
            },
            "required": ["key"],
        },
    },
    {
        "name": "delete_memory",
        "description": "Delete a memory note that is outdated or wrong.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "The memory key to delete."},
            },
            "required": ["key"],
        },
    },
    {
        "name": "log_progress",
        "description": (
            "Record a short progress entry in the persistent activity log. "
            "Call this after each significant step so there is a durable record of what you did."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entry": {"type": "string", "description": "One or two sentences describing the step taken."},
            },
            "required": ["entry"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Save a deliverable file for the current task: a report, an HTML demo or mockup, "
            "a CSV, a script, etc. Text content only. All files written during the task are "
            "attached to the completion email sent to the owner. Writing the same filename "
            "again overwrites it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Plain filename with extension, e.g. 'demo.html' or 'comparison.csv'. No directories.",
                },
                "content": {"type": "string", "description": "The full file content."},
            },
            "required": ["filename", "content"],
        },
    },
    {
        "name": "send_email",
        "description": (
            "Email the business owner. Use for important interim findings or genuinely "
            "blocking questions during long tasks. If the task came in by email, your message "
            "is sent as a reply in the same thread. A completion summary (with any files you "
            "wrote) is sent automatically when the task finishes, so do not duplicate it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "The message to send."},
            },
            "required": ["message"],
        },
    },
    {
        "name": "add_task",
        "description": (
            "Add a new task to the task queue for later execution. Use when you discover "
            "follow-up work that is out of scope for the current task."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "Clear, self-contained task description."},
            },
            "required": ["description"],
        },
    },
]

ALL_TOOLS = SERVER_TOOLS + CUSTOM_TOOLS


def task_output_dir(task_id: int):
    path = config.OUTPUT_DIR / f"task_{task_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def dispatch(name: str, tool_input: dict, task_id: int | None) -> tuple[str, bool]:
    """Execute a custom tool. Returns (result_text, is_error)."""
    try:
        if name == "save_memory":
            db.save_memory(tool_input["key"], tool_input["content"])
            return f"Memory '{tool_input['key']}' saved.", False

        if name == "read_memory":
            row = db.get_memory(tool_input["key"])
            if row is None:
                return f"No memory found with key '{tool_input['key']}'.", True
            return row["content"], False

        if name == "delete_memory":
            if db.delete_memory(tool_input["key"]):
                return f"Memory '{tool_input['key']}' deleted.", False
            return f"No memory found with key '{tool_input['key']}'.", True

        if name == "log_progress":
            db.add_log(tool_input["entry"], task_id)
            print(f"  [log] {tool_input['entry']}")
            return "Logged.", False

        if name == "write_file":
            if task_id is None:
                return "No active task to attach files to.", True
            safe_name = os.path.basename(tool_input["filename"]).strip()
            if not safe_name or safe_name in (".", ".."):
                return f"Invalid filename: {tool_input['filename']!r}", True
            path = task_output_dir(task_id) / safe_name
            path.write_text(tool_input["content"], encoding="utf-8")
            db.add_log(f"Wrote file: {safe_name} ({len(tool_input['content'])} chars)", task_id)
            return f"File '{safe_name}' saved; it will be attached to the completion email.", False

        if name == "send_email":
            task = db.get_task(task_id) if task_id is not None else None
            if task is not None and task["email_subject"]:
                subject = f"Re: {task['email_subject']}"
            else:
                subject = f"Hermes update on task #{task_id}"
            ok = mailer.send(
                subject,
                tool_input["message"],
                in_reply_to=task["email_message_id"] if task is not None else None,
            )
            if ok:
                db.add_log(f"Email sent: {tool_input['message'][:200]}", task_id)
                return "Email sent.", False
            return "Email send failed (check Gmail configuration).", True

        if name == "add_task":
            new_id = db.add_task(tool_input["description"])
            db.add_log(f"Queued follow-up task #{new_id}: {tool_input['description'][:200]}", task_id)
            return f"Task #{new_id} added to the queue.", False

        return f"Unknown tool: {name}", True
    except Exception as exc:  # noqa: BLE001 - report tool failures back to the model
        return f"Tool '{name}' failed: {exc}", True
