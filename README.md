# Remote Hermes Assistant

Hermes is an autonomous research and operations assistant for your business. It runs
unattended on a dedicated machine, works through a persistent task queue, researches on
the web, **remembers what it has done and what it has been tasked to do**, and reports to
you on **WhatsApp via the CallMeBot API**.

Built on the Claude API (`claude-opus-4-8`) with server-side web search and web fetch.

## What it can do

- **Research & search the web** — Claude's built-in web search + web fetch tools, so no
  extra search-API keys are needed.
- **Complete goals** — you queue tasks; Hermes works each one to completion in an agentic
  loop (research → act → verify → summarize).
- **Remember** — everything is persisted in a local SQLite database (`data/hermes.db`):
  - the **task queue** (what it has been tasked to do, with status and results),
  - an **activity log** (what it has done, step by step),
  - **long-term memory notes** the agent reads and writes itself (facts about your
    business, sources, decisions, lessons learned) — carried into every future task.
- **Communicate on WhatsApp** — sends you a message when a task starts, finishes, or
  fails, and can message you mid-task with important findings or blocking questions.

## Setup (on the machine that will run it)

Requires Python 3.10+.

```bash
git clone https://github.com/GroveX-DK/Remote-Hermes-Assistant.git
cd Remote-Hermes-Assistant

python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
# source .venv/bin/activate

pip install -r requirements.txt

# Configure secrets
copy .env.example .env     # Windows  (Linux/macOS: cp .env.example .env)
```

Edit `.env`:

1. **`ANTHROPIC_API_KEY`** — from <https://platform.claude.com/>
2. **CallMeBot WhatsApp key** (free):
   - Add the phone number **+34 644 51 95 23** to your phone's contacts (name it e.g. "CallMeBot").
   - Send it this WhatsApp message: `I allow callmebot to send me messages`
   - The bot replies with your personal `apikey`.
   - Put your own phone number (with country code, e.g. `+4512345678`) in `CALLMEBOT_PHONE`
     and the received key in `CALLMEBOT_APIKEY`.

Verify the WhatsApp connection:

```bash
python -m hermes test-whatsapp
```

## Usage

```bash
# Queue tasks
python -m hermes add "Find the 5 best CRM tools for a small Danish trading business and compare pricing"
python -m hermes add "Research which trade fairs in Europe in 2026 are relevant for us"

# See the queue / results
python -m hermes list

# Run the next pending task once
python -m hermes run

# Worker mode: run all pending tasks, then keep watching for new ones
python -m hermes work --watch

# Inspect what Hermes has done and what it remembers
python -m hermes log          # full activity log (or: log <task_id>)
python -m hermes memory       # long-term memory notes
```

While `work --watch` is running you can add tasks from another terminal (or over SSH)
at any time — the worker picks them up automatically and pings you on WhatsApp when
each one is done.

## Running it permanently on another computer

**Windows** — create a Scheduled Task that runs at logon:

```powershell
schtasks /Create /TN "Hermes" /SC ONLOGON /TR "C:\path\to\Remote-Hermes-Assistant\.venv\Scripts\python.exe -m hermes work --watch" /RU $env:USERNAME
```

(or just keep a terminal open with `python -m hermes work --watch`).

**Linux** — a systemd service:

```ini
# /etc/systemd/system/hermes.service
[Unit]
Description=Hermes business assistant
After=network-online.target

[Service]
WorkingDirectory=/opt/Remote-Hermes-Assistant
ExecStart=/opt/Remote-Hermes-Assistant/.venv/bin/python -m hermes work --watch
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now hermes
```

## Configuration reference

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Claude API key (required) |
| `CALLMEBOT_PHONE` | — | Your WhatsApp number incl. country code |
| `CALLMEBOT_APIKEY` | — | Your personal CallMeBot key |
| `HERMES_MODEL` | `claude-opus-4-8` | Claude model to use |
| `HERMES_DATA_DIR` | `./data` | Where the SQLite database lives |
| `HERMES_MAX_ITERATIONS` | `60` | Safety cap on agent steps per task |
| `HERMES_POLL_SECONDS` | `60` | Queue poll interval in `work --watch` mode |

## Notes

- `.env` and `data/` are git-ignored — secrets and the assistant's memory stay on the
  machine and are never pushed to GitHub.
- CallMeBot is a free third-party service intended for personal notifications; messages
  are truncated to ~1500 characters and delivery is best-effort.
- Tasks interrupted by network errors or Ctrl+C are returned to the queue, not lost.
