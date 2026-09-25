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
