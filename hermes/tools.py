"""Tool definitions and dispatch for the Hermes agent (Ollama function-calling format)."""

from __future__ import annotations

import os

from . import config, db, mailer, web


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


ALL_TOOLS = [
    _tool(
        "web_search",
        "Search the web (DuckDuckGo). Returns titles, URLs and snippets. "
        "Use web_fetch on the most promising URLs to read full pages.",
        {
            "query": {"type": "string", "description": "The search query."},
            "max_results": {"type": "integer", "description": "How many results (default 8, max 15)."},
        },
        ["query"],
    ),
    _tool(
        "web_fetch",
        "Fetch a web page and return its readable text content. Use after web_search "
        "to read sources in full. Only text-based pages are supported.",
        {"url": {"type": "string", "description": "Full http(s) URL to fetch."}},
        ["url"],
    ),
    _tool(
        "save_memory",
        "Save a long-term memory note that persists across tasks and restarts. "
        "Use this to remember facts about the business, decisions made, useful sources, "
        "and lessons learned. Saving to an existing key overwrites that note.",
        {
            "key": {
                "type": "string",
                "description": "Short kebab-case identifier, e.g. 'competitor-pricing'.",
            },
            "content": {"type": "string", "description": "The note content."},
        },
        ["key", "content"],
    ),
    _tool(
        "read_memory",
        "Read the full content of a saved memory note by its key. "
        "The list of available keys is provided in your task context.",
        {"key": {"type": "string", "description": "The memory key to read."}},
        ["key"],
    ),
    _tool(
        "delete_memory",
        "Delete a memory note that is outdated or wrong.",
        {"key": {"type": "string", "description": "The memory key to delete."}},
        ["key"],
    ),
    _tool(
        "log_progress",
        "Record a short progress entry in the persistent activity log. "
        "Call this after each significant step so there is a durable record of what you did.",
        {"entry": {"type": "string", "description": "One or two sentences describing the step taken."}},
        ["entry"],
    ),
    _tool(
        "write_file",
        "Save a deliverable file for the current task: a report, an HTML demo or mockup, "
        "a CSV, a script, etc. Text content only. All files written during the task are "
        "attached to the completion email sent to the owner. Writing the same filename "
        "again overwrites it.",
        {
            "filename": {
                "type": "string",
                "description": "Plain filename with extension, e.g. 'demo.html'. No directories.",
            },
            "content": {"type": "string", "description": "The full file content."},
        },
        ["filename", "content"],
    ),
    _tool(
        "send_email",
        "Email the business owner. Use for important interim findings or genuinely "
        "blocking questions during long tasks. If the task came in by email, your message "
        "is sent as a reply in the same thread. A completion summary (with any files you "
        "wrote) is sent automatically when the task finishes, so do not duplicate it.",
        {"message": {"type": "string", "description": "The message to send."}},
        ["message"],
    ),
    _tool(
        "add_task",
        "Add a new task to the task queue for later execution. Use when you discover "
        "follow-up work that is out of scope for the current task.",
        {"description": {"type": "string", "description": "Clear, self-contained task description."}},
        ["description"],
    ),
]


def task_output_dir(task_id: int):
    path = config.OUTPUT_DIR / f"task_{task_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def dispatch(name: str, tool_input: dict, task_id: int | None) -> tuple[str, bool]:
    """Execute a tool call. Returns (result_text, is_error)."""
    try:
        if name == "web_search":
            max_results = min(int(tool_input.get("max_results") or 8), 15)
            return web.search(tool_input["query"], max_results=max_results), False

        if name == "web_fetch":
            return web.fetch(tool_input["url"]), False

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
    except KeyError as exc:
        return f"Tool '{name}' is missing required argument: {exc}", True
    except Exception as exc:  # noqa: BLE001 - report tool failures back to the model
        return f"Tool '{name}' failed: {exc}", True
