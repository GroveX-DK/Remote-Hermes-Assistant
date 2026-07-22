"""Hermes CLI.

Usage:
    python -m hermes add "Research the top 5 CRM tools for a small business"
    python -m hermes list
    python -m hermes run            # run the next pending task
    python -m hermes run 3          # run a specific task
    python -m hermes work           # run all pending tasks, then exit
    python -m hermes work --watch   # keep polling for new tasks (daemon mode)
    python -m hermes log [task_id]
    python -m hermes memory
    python -m hermes test-whatsapp
"""

from __future__ import annotations

import argparse
import sys
import time

from . import agent, config, db, whatsapp


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
    print("Hermes worker started." + (" Watching for new tasks..." if args.watch else ""))
    while True:
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


def cmd_test_whatsapp(args) -> None:  # noqa: ARG001
    if not whatsapp.is_configured():
        print("CallMeBot is not configured. Set CALLMEBOT_PHONE and CALLMEBOT_APIKEY in .env")
        sys.exit(1)
    ok = whatsapp.send("Hermes assistant is connected and ready. \U0001f680")
    print("Test message sent." if ok else "Test message FAILED — check phone/apikey.")
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
    p_run.add_argument("--quiet", action="store_true", help="Skip WhatsApp notifications")
    p_run.set_defaults(func=cmd_run)

    p_work = sub.add_parser("work", help="Run all pending tasks")
    p_work.add_argument("--watch", action="store_true", help="Keep polling for new tasks")
    p_work.add_argument("--quiet", action="store_true", help="Skip WhatsApp notifications")
    p_work.set_defaults(func=cmd_work)

    p_log = sub.add_parser("log", help="Show the activity log")
    p_log.add_argument("task_id", nargs="?", type=int, default=None)
    p_log.set_defaults(func=cmd_log)

    p_mem = sub.add_parser("memory", help="Show saved memory notes")
    p_mem.set_defaults(func=cmd_memory)

    p_test = sub.add_parser("test-whatsapp", help="Send a test WhatsApp message")
    p_test.set_defaults(func=cmd_test_whatsapp)

    args = parser.parse_args()

    needs_api_key = args.command in ("run", "work")
    if needs_api_key and not config.ANTHROPIC_API_KEY:
        print("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill it in.")
        sys.exit(1)

    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(130)


if __name__ == "__main__":
    main()
