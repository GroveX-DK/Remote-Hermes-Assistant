"""The Hermes agent loop: runs one task with a local Ollama model until completion."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from . import config, db, mailer, tools

SYSTEM_PROMPT = """\
You are Hermes, an autonomous research and operations assistant for a small business. \
You run unattended on a dedicated machine and communicate with the business owner by email.

How you work:
- You are given one task at a time from a persistent task queue. Work it to completion in this session.
- Research using the web_search tool, then read the best sources with web_fetch. Prefer primary \
sources and note where information came from. Do not invent facts — if you did not find it, say so.
- Your context does NOT persist between tasks. Anything worth keeping must be stored with save_memory \
(durable facts, decisions, sources, lessons learned). Check the memory notes listed in your task \
context before re-researching something you may already know.
- Use log_progress after each significant step so there is a durable record of what you did.
- For deliverables — reports, comparisons, HTML demos or mockups, CSV data, scripts — use write_file. \
Every file you write is attached to the completion email, so produce real artifacts rather than \
describing what one would look like.
- If you discover follow-up work outside the current task's scope, queue it with add_task.
- Use send_email only for important interim findings or genuinely blocking questions; a completion \
summary with your files attached is sent automatically when you finish.
- Call one tool at a time and use its result before deciding the next step.

When you are done, reply WITHOUT any tool call, giving a final summary of what you accomplished, \
key findings, and recommended next steps. That summary becomes the body of the completion email, \
so write it for the owner: lead with the outcome, keep it concise and in plain language, and \
mention any attached files by name.

If a task is impossible or blocked, say so clearly in your final summary and explain what is needed.
"""


def _chat(messages: list[dict]) -> dict:
    """One non-streaming call to Ollama's chat API."""
    options = {"num_ctx": config.NUM_CTX}
    if config.NUM_GPU != "":
        options["num_gpu"] = int(config.NUM_GPU)
    payload = {
        "model": config.MODEL,
        "messages": messages,
        "tools": tools.ALL_TOOLS,
        "stream": False,
        "options": options,
    }
    req = urllib.request.Request(
        f"{config.OLLAMA_HOST}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=config.REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _strip_think(text: str) -> str:
    """Remove <think>...</think> blocks some models emit."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _build_context(task_row) -> str:
    parts = [f"# Task #{task_row['id']}\n{task_row['description']}"]

    memories = db.list_memories()
    if memories:
        lines = [f"- {m['key']} (updated {m['updated_at']}): {m['content'][:150]}" for m in memories]
        parts.append(
            "# Saved memory notes (use read_memory for full content)\n" + "\n".join(lines)
        )
    else:
        parts.append("# Saved memory notes\n(none yet)")

    recent = db.recent_completed_tasks(limit=8)
    if recent:
        lines = [
            f"- #{t['id']} [{t['status']}] {t['description'][:120]}"
            + (f" -> {t['result'][:150]}" if t["result"] else "")
            for t in recent
        ]
        parts.append("# Recently completed tasks\n" + "\n".join(lines))

    parts.append("Complete the task now.")
    return "\n\n".join(parts)


def _notify(task_row, subject: str, body: str, with_attachments: bool = False) -> None:
    """Email the owner about this task, threading the reply if it came in by email."""
    if task_row["email_subject"]:
        subject = f"Re: {task_row['email_subject']}"
    attachments = None
    if with_attachments:
        out_dir = config.OUTPUT_DIR / f"task_{task_row['id']}"
        if out_dir.is_dir():
            attachments = sorted(p for p in out_dir.iterdir() if p.is_file())
    mailer.send(subject, body, attachments=attachments, in_reply_to=task_row["email_message_id"])


def run_task(task_id: int, notify: bool = True) -> bool:
    """Run one task to completion. Returns True if the task finished successfully."""
    task = db.get_task(task_id)
    if task is None:
        print(f"Task #{task_id} not found.")
        return False

    db.set_task_status(task_id, "in_progress")
    db.add_log(f"Task started: {task['description'][:200]}", task_id)
    print(f"\n=== Running task #{task_id}: {task['description']}\n")
    if notify:
        _notify(
            task,
            f"Hermes started task #{task_id}",
            f"Working on it:\n\n{task['description'][:500]}\n\nYou'll get a summary when it's done.",
        )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_context(task)},
    ]
    final_text = ""

    try:
        for iteration in range(config.MAX_ITERATIONS):
            data = _chat(messages)
            msg = data.get("message", {})
            content = msg.get("content", "") or ""
            tool_calls = msg.get("tool_calls") or []

            visible = _strip_think(content)
            if visible:
                print(visible)

            assistant_msg = {"role": "assistant", "content": content}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)

            if not tool_calls:
                final_text = visible
                if final_text:
                    break
                # Model produced nothing; nudge it once instead of failing.
                messages.append(
                    {
                        "role": "user",
                        "content": "You replied with empty content. Either call a tool to make "
                        "progress or give your final summary now.",
                    }
                )
                continue

            for call in tool_calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                print(f"  [tool] {name}({json.dumps(args, ensure_ascii=False)[:200]})")
                result_text, is_error = tools.dispatch(name, args, task_id)
                if is_error:
                    result_text = f"ERROR: {result_text}"
                messages.append({"role": "tool", "tool_name": name, "content": result_text})
        else:
            summary = f"Stopped after reaching the {config.MAX_ITERATIONS}-iteration safety limit."
            db.set_task_status(task_id, "failed", summary)
            db.add_log(summary, task_id)
            if notify:
                _notify(task, f"Hermes task #{task_id} stopped", summary)
            return False

        summary = final_text or "(no summary produced)"
        db.set_task_status(task_id, "done", summary)
        db.add_log("Task completed.", task_id)
        if notify:
            _notify(task, f"Hermes finished task #{task_id}", summary, with_attachments=True)
        print(f"\n=== Task #{task_id} done.\n")
        return True

    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:  # noqa: BLE001
            pass
        msg = f"Ollama error {exc.code}: {body or exc.reason}"
        if exc.code == 404:
            msg += f" — is the model pulled? Try: ollama pull {config.MODEL}"
        db.set_task_status(task_id, "failed", msg)
        db.add_log(msg, task_id)
        if notify:
            _notify(task, f"Hermes task #{task_id} failed", msg)
        print(msg)
        return False
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        msg = (
            f"Could not reach Ollama at {config.OLLAMA_HOST} (is 'ollama serve' running?). "
            "Task returned to the queue."
        )
        db.set_task_status(task_id, "pending")
        db.add_log(msg, task_id)
        print(msg)
        return False
    except KeyboardInterrupt:
        db.set_task_status(task_id, "pending")
        db.add_log("Interrupted by operator; task returned to the queue.", task_id)
        print(f"\nInterrupted. Task #{task_id} returned to the queue.")
        raise
