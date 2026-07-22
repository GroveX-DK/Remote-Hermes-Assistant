# Remote Hermes Assistant

Hermes is an autonomous research and operations assistant for your business, powered by a
**local Ollama model** — no cloud AI API keys and no per-token costs. It runs unattended
on a dedicated machine, works through a persistent task queue, researches on the web,
**remembers what it has done and what it has been tasked to do**, and talks to you
**two-way over Gmail**: email it a task from anywhere, and it replies in the same thread
with results and file attachments.

## What it can do

- **Research & search the web** — built-in DuckDuckGo search plus a page reader, all
  running locally.
- **Complete goals** — email it a task (or queue one with the CLI); Hermes works each one
  to completion in an agentic loop (search → read sources → act → summarize).
- **Produce deliverables** — reports, comparisons, CSV data, scripts, and HTML
  demos/mockups. Every file it writes is attached to the completion email.
- **Remember** — everything is persisted in a local SQLite database (`data/hermes.db`):
  - the **task queue** (what it has been tasked to do, with status and results),
  - an **activity log** (what it has done, step by step),
  - **long-term memory notes** the agent reads and writes itself — carried into every
    future task.
- **Communicate over Gmail, both directions**:
  - **You → Hermes:** send an email to the assistant's address; the subject + body become
    a task. Only mail from your configured owner address is accepted.
  - **Hermes → you:** it emails you when a task starts and finishes (threaded as a reply
    to your original email), and can email you mid-task with findings or blocking questions.

## Setup (on the machine that will run it)

Requires Python 3.10+ and [Ollama](https://ollama.com).

**1. Install Ollama and pull a tool-calling model:**

```bash
# Install from https://ollama.com/download, then:
ollama pull qwen3:8b
```

`qwen3:8b` is the default (good quality, ~5 GB, runs on 16 GB RAM). Other models that
support tool calling work too — e.g. `llama3.1:8b`, `qwen3:14b`, `mistral-nemo` — set
`HERMES_MODEL` in `.env` to change. Bigger models give noticeably better research if the
machine can handle them.

**2. Install Hermes:**

```bash
git clone https://github.com/GroveX-DK/Remote-Hermes-Assistant.git
cd Remote-Hermes-Assistant

python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
# source .venv/bin/activate

pip install -r requirements.txt

copy .env.example .env     # Windows  (Linux/macOS: cp .env.example .env)
```

**3. Configure Gmail in `.env`** (recommended: create a dedicated Gmail account for
Hermes, e.g. `hermes.mybusiness@gmail.com`, so it has its own clean inbox):

- On that Google account, turn on **2-Step Verification**.
- Create an **App Password**: <https://myaccount.google.com/apppasswords> — Google shows
  a 16-character password.
- Set `GMAIL_ADDRESS` to the assistant's address and `GMAIL_APP_PASSWORD` to that app
  password.
- Set `HERMES_OWNER_EMAIL` to your own address. Results are sent here, and **only tasks
  emailed from this address are accepted** (anything else in the inbox is ignored).

**4. Verify:**

```bash
python -m hermes test-email
```

You should receive an email from the assistant within seconds.

## Run it forever

This is the one command to start after setup — it processes the queue and then keeps
watching the inbox for new task emails (checked every 60 seconds):

```bash
python -m hermes work --watch
```

From then on you don't need the terminal: **email the assistant's address from your phone
or laptop, and it emails you back when the work is done.**

To survive reboots, register it with the OS:

**Windows** — Scheduled Task that starts at logon:

```powershell
schtasks /Create /TN "Hermes" /SC ONLOGON /TR "C:\path\to\Remote-Hermes-Assistant\.venv\Scripts\python.exe -m hermes work --watch" /RU $env:USERNAME
```

**Linux** — systemd service:

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

## CLI reference

```bash
# Queue tasks locally (email works too — that's the main way)
python -m hermes add "Find the 5 best CRM tools for a small Danish trading business and compare pricing"

# See the queue / results
python -m hermes list

# Run just the next pending task once
python -m hermes run

# Run forever: poll inbox + queue
python -m hermes work --watch

# Inspect what Hermes has done and what it remembers
python -m hermes log          # full activity log (or: log <task_id>)
python -m hermes memory       # long-term memory notes

# Verify Gmail settings
python -m hermes test-email
```

## Configuration reference

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | Where the Ollama server runs |
| `HERMES_MODEL` | `qwen3:8b` | Ollama model (must support tool calling) |
| `HERMES_NUM_CTX` | `16384` | Model context window (tokens) |
| `HERMES_REQUEST_TIMEOUT` | `600` | Seconds to wait for one model response |
| `GMAIL_ADDRESS` | — | The assistant's own Gmail address |
| `GMAIL_APP_PASSWORD` | — | App password for that account (needs 2-Step Verification) |
| `HERMES_OWNER_EMAIL` | — | Your address: gets all results; only sender allowed to queue tasks |
| `HERMES_DATA_DIR` | `./data` | Where the SQLite database and output files live |
| `HERMES_MAX_ITERATIONS` | `60` | Safety cap on agent steps per task |
| `HERMES_POLL_SECONDS` | `60` | Inbox/queue poll interval in `work --watch` mode |

## Notes

- Runs fully locally except for web searches (DuckDuckGo) and Gmail. No AI API costs.
- Model quality matters: small local models are slower and less reliable at multi-step
  research than cloud frontier models. If results are weak, try a larger model
  (`qwen3:14b`, `qwen3:32b`) — quality scales with what your hardware can run.
- `.env` and `data/` are git-ignored — secrets, the assistant's memory, and generated
  files stay on the machine.
- Deliverable files are also kept on disk under `data/outputs/task_<id>/`.
- Attachments are capped at ~20 MB per email (Gmail limit).
- Tasks interrupted by errors or Ctrl+C are returned to the queue, not lost.
- Every unread email in the assistant's inbox is marked as read when checked; only mail
  from `HERMES_OWNER_EMAIL` becomes a task.
