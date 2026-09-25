# AURA — holographic voice assistant

Three layers, each swappable on its own:

```
┌──────────────┐  WebSocket /ws   ┌──────────────┐  Agent interface  ┌──────────────────────────┐
│  Frontend    │ ───────────────▶ │  Bridge      │ ────────────────▶ │  Agent                   │
│  React HUD   │ ◀─── tokens ──── │  FastAPI     │ ◀── async stream ─│  echo / openai /         │
│  voice in+out│   REST /api/*    │  sessions    │                   │  anthropic / webhook /   │
└──────────────┘                  └──────────────┘                   │  custom Python class     │
                                                                     └──────────────────────────┘
```

## Run it

**Bridge + agent**
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # pick AGENT_PROVIDER and add keys
uvicorn bridge.main:app --reload --port 8000
```

**Frontend**
```bash
cd frontend
npm install
npm run dev                   # http://localhost:5173 (proxies /api and /ws to :8000)
```

**Or both with Docker**
```bash
cp backend/.env.example backend/.env
docker compose up --build     # http://localhost:8080
```

Voice input uses the browser's Web Speech API (Chrome and Edge work best) and needs
`localhost` or HTTPS for microphone access. Replies are read aloud with the system voice.

Shortcuts: **Ctrl+Space** talk, **Enter** send, **Esc** stop the reply and the voice.

## Choosing the agent

Set `AGENT_PROVIDER` in `backend/.env`:

| Provider    | What it connects to | Needs |
|-------------|--------------------|-------|
| `echo`      | Built-in demo, no network | nothing |
| `openai`    | Any OpenAI-compatible server: OpenAI, Ollama, LM Studio, vLLM, Groq, OpenRouter | `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL` |
| `anthropic` | Claude via the Messages API | `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` |
| `claude_code` | Claude via the Claude Code CLI, signed in with your Claude account (no API key). Tools are disabled; personal local use only | the `claude` CLI and `claude auth login` |
| `webhook`   | Your own agent service (n8n, LangGraph server, CrewAI API, anything HTTP) | `WEBHOOK_URL`, optional `WEBHOOK_TOKEN` |
| `custom`    | A Python class loaded in-process (LangChain, LangGraph, your tool loop) | `CUSTOM_AGENT=module:Class` |

### Webhook contract
The bridge POSTs `{"system": "...", "messages": [{"role": "user", "content": "..."}]}` and accepts
`text/event-stream` (raw text or `{"token": "..."}` per event), JSON `{"reply": "..."}`, or plain text.

### Custom agent
Subclass `agent.base.Agent` and implement `async def stream(self, messages, system)` that yields text.
`backend/agent/examples/my_agent.py` is a ready template with a LangGraph snippet.

## Bridge API

| Method | Path | Purpose |
|--------|------|---------|
| WS   | `/ws?session=<id>` | Streaming chat (protocol documented at the top of `bridge/main.py`) |
| POST | `/api/chat` | `{"text", "session_id"}` → `{"reply"}` — handy for scripts and home automation |
| GET  | `/api/agent` | Active assistant name, provider and model |
| POST | `/api/sessions/{id}/reset` | Clear a session's memory |
| GET  | `/api/health` | Liveness |

## Customising

- **Name and personality:** `ASSISTANT_NAME` and `SYSTEM_PROMPT` in `backend/.env`; the HUD picks up the name automatically.
- **Colours:** tokens at the top of `frontend/src/styles.css`. The accent shifts by state (cyan idle, amber working, bright cyan listening, grey offline).
- **Memory:** `bridge/sessions.py` is in-memory; swap in Redis or a database for persistence.
- **Production:** add auth in front of the bridge before exposing it beyond your machine.

## Gmail connector (read-only)

`backend/connectors/gmail.py` searches and reads Gmail messages in full: headers, the
plain-text body (or text taken from the HTML), the raw HTML, and attachment names and sizes.
It only asks for the `gmail.readonly` scope.

1. In Google Cloud Console, enable the **Gmail API** and create an OAuth client of type **Desktop app**.
   Save the JSON as `backend/credentials.json`.
2. `cd backend && python -m connectors.gmail auth`, then approve in the browser. This writes `token.json`.
3. Try it:
   ```bash
   python -m connectors.gmail list -q "is:unread newer_than:7d" -n 5
   python -m connectors.gmail read <message_id>
   python -m connectors.gmail thread <thread_id>
   ```

From Python: `GmailConnector().search(query)` returns summaries, `.read(id)` returns an `Email`,
and `.read_thread(id)` returns every message in a conversation. `credentials.json` and `token.json`
are gitignored; keep them private.

## Gmail alert watcher

`backend/connectors/gmail_watcher.py` listens to your inbox for alert emails and sorts each one:

- **Candidate for analysis**: stays in the inbox, gets the `candidates` label, and is appended to
  `backend/data/candidates.jsonl` (the hand-off for the analysis step).
- **Not a candidate**: gets the `discarded` label and leaves the inbox, so it only appears under
  **discarded** in Gmail's sidebar. Nothing is deleted.

- **Own folder** (`folders` in the rules, e.g. `analyzed` for analysis reports): gets that label and
  leaves the inbox. Folders are checked before any discard rule, so these emails are never discarded.

Emails that don't match `watch_query` are never touched.

1. Allow labelling and moving mail (one-time; replaces a read-only token):
   `cd backend && python -m connectors.gmail auth --modify`
2. `cp alert_filters.example.json alert_filters.json` and edit the rules:
   - `watch_query`: which emails count as alerts (any Gmail search).
   - `folders`: `[{"label": "analyzed", "if_any": [rules]}]`, checked first.
   - `discard_if_any`: always discard when one of these rules matches.
   - `candidate_if_any`: a candidate when one of these matches; everything else is discarded.
   - Rule fields: `from`, `to`, `subject`, `body` (a word or list of words, case-insensitive),
     `subject_regex`, `body_regex`, `has_attachment`. Every field in a rule must match.
3. Check the decisions first. `dry_run` is `true` in the example, so nothing is changed:
   ```bash
   python -m connectors.gmail_watcher once
   python -m connectors.gmail_watcher test <message_id>
   ```
4. Set `"dry_run": false`, then keep it listening (polls every `poll_seconds`):
   ```bash
   python -m connectors.gmail_watcher watch
   ```

## Incident analysis from the HUD

When you open the HUD, J.A.R.V.I.G. greets you and asks whether to enable incident analysis. Answer
"yes" (or "sure", "go ahead", "sim") and the bridge runs the Gmail alert watcher in the background;
"no" keeps it off. At any time you can say or type:

- "enable incident analysis" / "turn off incident analysis"
- "is incident analysis running?" for the status and what has been sorted so far

The **Systems** panel shows the state and counts, with an on/off button. These commands are handled by
the bridge itself (`bridge/commands.py`), not the agent, so they work with every provider. Incident
analysis always starts switched off when the bridge starts. `GET/POST /api/incidents` (`{"enabled": true}`)
does the same from scripts.
