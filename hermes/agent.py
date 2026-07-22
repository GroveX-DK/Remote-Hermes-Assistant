"""The Hermes agent loop: runs one task with Claude until completion."""

from __future__ import annotations

import anthropic

from . import config, db, mailer, tools

SYSTEM_PROMPT = """\
You are Hermes, an autonomous research and operations assistant for a small business. \
You run unattended on a dedicated machine and communicate with the business owner by email.

How you work:
- You are given one task at a time from a persistent task queue. Work it to completion in this session.
- Research thoroughly using web_search and web_fetch. Prefer primary sources; note where information came from.
- Your context does NOT persist between tasks. Anything worth keeping must be stored with save_memory \
(durable facts, decisions, sources, lessons learned) or log_progress (a record of steps taken). \
Check the memory notes provided in your task context before re-researching something you may already know.
- Use log_progress after each significant step so there is always a durable record of what you did.
- For deliverables — reports, comparisons, HTML demos or mockups, CSV data, scripts — use write_file. \
Every file you write is attached to the completion email, so produce real artifacts rather than \
describing what one would look like.
- If you discover follow-up work outside the current task's scope, queue it with add_task instead of drifting.
- Use send_email only for important interim findings or genuinely blocking questions; a completion \
summary with your files attached is sent automatically when you finish.

When you finish, end with a final summary of what you accomplished, key findings, and any recommended \
next steps. That summary becomes the body of the completion email, so write it for the owner: lead \
with the outcome, keep it concise and in plain language, and mention any attached files by name.

If a task is impossible or blocked, say so clearly in your final summary and explain what is needed.
"""


def _build_context(task_row) -> str:
    """Dynamic context injected into the user turn (keeps the system prompt cacheable)."""
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


def _final_text(response) -> str:
    return "\n".join(b.text for b in response.content if b.type == "text").strip()


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

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY or None)

    db.set_task_status(task_id, "in_progress")
    db.add_log(f"Task started: {task['description'][:200]}", task_id)
    print(f"\n=== Running task #{task_id}: {task['description']}\n")
    if notify:
        _notify(
            task,
            f"Hermes started task #{task_id}",
            f"Working on it:\n\n{task['description'][:500]}\n\nYou'll get a summary when it's done.",
        )

    messages = [{"role": "user", "content": _build_context(task)}]
    response = None

    try:
        for iteration in range(config.MAX_ITERATIONS):
            with client.messages.stream(
                model=config.MODEL,
                max_tokens=64000,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                thinking={"type": "adaptive"},
                output_config={"effort": "high"},
                tools=tools.ALL_TOOLS,
                messages=messages,
            ) as stream:
                for text in stream.text_stream:
                    print(text, end="", flush=True)
                response = stream.get_final_message()
            print()

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                summary = "Task was declined by the model's safety systems and cannot be completed."
                db.set_task_status(task_id, "failed", summary)
                db.add_log(summary, task_id)
                if notify:
                    _notify(task, f"Hermes task #{task_id} failed", summary)
                return False

            if response.stop_reason == "pause_turn":
                # Server-side tool loop paused; re-send to let it resume.
                continue

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    print(f"  [tool] {block.name}")
                    result_text, is_error = tools.dispatch(block.name, block.input, task_id)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_text,
                            "is_error": is_error,
                        }
                    )
                messages.append({"role": "user", "content": tool_results})
                continue

            if response.stop_reason == "max_tokens":
                messages.append(
                    {
                        "role": "user",
                        "content": "Your previous response was cut off by the output limit. "
                        "Continue from where you stopped and finish the task.",
                    }
                )
                continue

            break  # end_turn
        else:
            summary = f"Stopped after reaching the {config.MAX_ITERATIONS}-iteration safety limit."
            db.set_task_status(task_id, "failed", summary)
            db.add_log(summary, task_id)
            if notify:
                _notify(task, f"Hermes task #{task_id} stopped", summary)
            return False

        summary = _final_text(response) or "(no summary produced)"
        db.set_task_status(task_id, "done", summary)
        db.add_log("Task completed.", task_id)
        if notify:
            _notify(task, f"Hermes finished task #{task_id}", summary, with_attachments=True)
        print(f"\n=== Task #{task_id} done.\n")
        return True

    except anthropic.APIConnectionError:
        msg = "Network error while talking to the Claude API. Task returned to the queue."
        db.set_task_status(task_id, "pending")
        db.add_log(msg, task_id)
        print(msg)
        return False
    except anthropic.APIStatusError as exc:
        msg = f"Claude API error {exc.status_code}: {exc.message}"
        db.set_task_status(task_id, "failed", msg)
        db.add_log(msg, task_id)
        if notify:
            _notify(task, f"Hermes task #{task_id} failed", msg[:500])
        print(msg)
        return False
    except KeyboardInterrupt:
        db.set_task_status(task_id, "pending")
        db.add_log("Interrupted by operator; task returned to the queue.", task_id)
        print(f"\nInterrupted. Task #{task_id} returned to the queue.")
        raise
