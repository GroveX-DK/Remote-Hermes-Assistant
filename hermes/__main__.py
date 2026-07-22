"""Hermes CLI.

Usage:
    python -m hermes add "Research the top 5 CRM tools for a small business"
    python -m hermes list
    python -m hermes run            # run the next pending task
    python -m hermes run 3          # run a specific task
    python -m hermes work           # run all pending tasks, then exit
    python -m hermes work --watch   # run forever: poll inbox + queue (daemon mode)
    python -m hermes log [task_id]
    python -m hermes memory
    python -m hermes test-email
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

from . import agent, config, db, mailer


def _check_ollama() -> None:
    """Fail fast with a helpful message if Ollama or the model is unavailable."""
    try:
        with urllib.request.urlopen(f"{config.OLLAMA_HOST}/api/tags", timeout=10) as resp:
            models = [m.get("name", "") for m in json.loads(resp.read()).get("models", [])]
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        print(f"Cannot reach Ollama at {config.OLLAMA_HOST}.")
        print("Install it from https://ollama.com and make sure it is running ('ollama serve').")
        sys.exit(1)
    base = config.MODEL.split(":")[0]
    if not any(m == config.MODEL or m.split(":")[0] == base for m in models):
        print(f"Model '{config.MODEL}' is not available in Ollama.")
        print(f"Pull it first:  ollama pull {config.MODEL}")
        sys.exit(1)


def _check_inbox() -> int:
    """Pull new task emails from the owner into the queue. Returns count added."""
    added = 0
    for item in mailer.fetch_new_tasks():
        task_id = db.add_task(
            item["description"],
            email_message_id=item["message_id"] or None,
            email_subject=item["subject"] or None,
        )
        db.add_log(f"Task received by email from {item['from_addr']}", task_id)
        print(f"[mail] queued task #{task_id} from email: {item['subject'] or item['description'][:80]}")
        added += 1
    return added


def cmd_add(args) -> None:
    description = " ".join(args.description).strip()
    if not description:
        print("Task description is empty.")
        sys.exit(1)
    task_id = db.add_task(description)
    print(f"Added task #{task_id}: {description}")


def cmd_list(args) -> None:  # noqa: ARG001
    rows = db.list_tasks()
    if not rows:
        print("No tasks yet. Add one with: python -m hermes add \"...\"")
        return
    for t in rows:
        print(f"#{t['id']:<4} [{t['status']:<11}] {t['created_at']}  {t['description'][:100]}")
        if t["result"]:
            print(f"      -> {t['result'][:200]}")


def cmd_run(args) -> None:
    if args.task_id is not None:
        task = db.get_task(args.task_id)
        if task is None:
            print(f"Task #{args.task_id} not found.")
            sys.exit(1)
    else:
        task = db.next_pending_task()
        if task is None:
            print("No pending tasks.")
            return
    ok = agent.run_task(task["id"], notify=not args.quiet)
    sys.exit(0 if ok else 1)


def cmd_work(args) -> None:
    print("Hermes worker started." + (" Watching inbox and queue..." if args.watch else ""))
    if args.watch and not mailer.is_configured():
        print("Note: Gmail is not configured, so tasks can only arrive via 'hermes add'.")
    while True:
        if mailer.is_configured():
            _check_inbox()
        task = db.next_pending_task()
        if task is not None:
            agent.run_task(task["id"], notify=not args.quiet)
            continue
        if not args.watch:
            print("Queue empty. Done.")
            return
        time.sleep(config.POLL_SECONDS)


def cmd_log(args) -> None:
    rows = db.get_log(args.task_id)
    if not rows:
        print("No log entries.")
        return
    for r in rows:
        prefix = f"[task #{r['task_id']}] " if r["task_id"] else ""
        print(f"{r['created_at']}  {prefix}{r['entry']}")


def cmd_memory(args) -> None:  # noqa: ARG001
    rows = db.list_memories()
    if not rows:
        print("No memory notes yet.")
        return
    for m in rows:
        print(f"## {m['key']}  (updated {m['updated_at']})")
        print(m["content"])
        print()


def cmd_test_email(args) -> None:  # noqa: ARG001
    if not mailer.is_configured():
        print("Gmail is not configured. Set GMAIL_ADDRESS, GMAIL_APP_PASSWORD and HERMES_OWNER_EMAIL in .env")
        sys.exit(1)
    ok = mailer.send(
        "Hermes is connected",
        "Hermes assistant is connected and ready.\n\n"
        "Reply to this address with a task and the worker will pick it up.",
    )
    print("Test email sent." if ok else "Test email FAILED — check GMAIL_ADDRESS / GMAIL_APP_PASSWORD.")
    sys.exit(0 if ok else 1)


def main() -> None:
    parser = argparse.ArgumentParser(prog="hermes", description="Hermes business assistant")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="Add a task to the queue")
    p_add.add_argument("description", nargs="+", help="What Hermes should do")
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="List tasks")
    p_list.set_defaults(func=cmd_list)

    p_run = sub.add_parser("run", help="Run the next pending task (or a specific one)")
    p_run.add_argument("task_id", nargs="?", type=int, default=None)
    p_run.add_argument("--quiet", action="store_true", help="Skip email notifications")
    p_run.set_defaults(func=cmd_run)

    p_work = sub.add_parser("work", help="Run all pending tasks")
    p_work.add_argument("--watch", action="store_true", help="Run forever: keep polling inbox and queue")
    p_work.add_argument("--quiet", action="store_true", help="Skip email notifications")
    p_work.set_defaults(func=cmd_work)

    p_log = sub.add_parser("log", help="Show the activity log")
    p_log.add_argument("task_id", nargs="?", type=int, default=None)
    p_log.set_defaults(func=cmd_log)

    p_mem = sub.add_parser("memory", help="Show saved memory notes")
    p_mem.set_defaults(func=cmd_memory)

    p_test = sub.add_parser("test-email", help="Send a test email to the owner")
    p_test.set_defaults(func=cmd_test_email)

    args = parser.parse_args()

    if args.command in ("run", "work"):
        _check_ollama()

    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(130)


if __name__ == "__main__":
    main()
