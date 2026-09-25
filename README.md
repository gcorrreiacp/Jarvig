# J.A.R.V.I.G. — voice assistant for incident handling

J.A.R.V.I.G. is a voice-and-text assistant that runs in your browser. Talk to it or type, and it answers
out loud. It can also look after your alert emails: it watches a Gmail inbox, sorts incoming alerts,
uploads the ones worth analysing to an FTP server, and files them away once their analysis report
comes back.

- **Chat by voice or keyboard** with the AI of your choice (Claude, OpenAI, a local model, or your own agent).
- **Incident analysis:** sorts alert emails in Gmail into *candidates*, *discarded* and *analyzed*.
- **Incident dispatcher:** uploads candidate alerts to an FTP server as text files.
- **MR summarizer:** posts a summary comment on each open GitHub pull request, based on its code.
- **MR reviewer:** comments on the faulty lines of each open pull request with what to change.
- Each feature is switched on and off on its own, by voice or button ("start the MR reviewer and incident analysis").

---

## Quick start

About 15 minutes. You'll end up with J.A.R.V.I.G. running at http://localhost:5173.

### What you need

| | Why | Check with |
|---|---|---|
| **Python 3.11+** | runs the backend | `python3 --version` |
| **Node.js 18+** | runs the frontend | `node --version` |
| **Chrome or Edge** | voice input and output | |
| **An AI to talk to** | the assistant's brain; see [step 3](#3-choose-the-assistants-brain) | |

On a Mac without Python or Node: `brew install python node` ([Homebrew](https://brew.sh)).

### 1. Get the code

```bash
git clone https://github.com/gcorrreiacp/Jarvig.git
cd Jarvig
```

### 2. Set up the backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`backend/.env` holds all your settings. It is never committed to git.

### 3. Choose the assistant's brain

Open `backend/.env` and set `AGENT_PROVIDER`. Pick one:

| You have | Set | Also set |
|---|---|---|
| Nothing yet, just trying it out | `AGENT_PROVIDER=echo` | nothing (replies are canned) |
| A Claude account (Pro, Max, Team), no API key | `AGENT_PROVIDER=claude_code` | see below |
| An Anthropic API key | `AGENT_PROVIDER=anthropic` | `ANTHROPIC_API_KEY` |
| An OpenAI key, or Ollama / LM Studio locally | `AGENT_PROVIDER=openai` | `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL` |

**Using your Claude account (`claude_code`):** install the Claude Code CLI and sign in once:

```bash
curl -fsSL https://claude.ai/install.sh | bash
~/.local/bin/claude auth login
```

J.A.R.V.I.G. finds the CLI in `~/.local/bin` on its own. This runs on your personal Claude account, so
keep it for your own use on your own machine.

**Using Ollama:** `OPENAI_BASE_URL=http://localhost:11434/v1`, `OPENAI_MODEL=llama3.1`, any `OPENAI_API_KEY`.

### 4. Set up the frontend

In a **second terminal tab**, from the `Jarvig` folder:

```bash
cd frontend
npm install
```

### 5. Start it

**Tab 1, backend** (from `backend/`, with `.venv` active):

```bash
uvicorn bridge.main:app --reload --port 8000
```

Wait for `Agent ready: {'provider': '…'}`: that confirms which brain it's using.

**Tab 2, frontend** (from `frontend/`):

```bash
npm run dev
```

### 6. Say hello

Open **http://localhost:5173** in Chrome or Edge and allow the microphone when asked.

J.A.R.V.I.G. greets you and asks whether to enable incident analysis. Answer **"no"** for now (it
needs Gmail, set up below) and ask it anything. Type and press **Enter**, or press **Ctrl+Space** and speak.

Next time, you only need step 5. Stop both with **Ctrl+C**.

---

## Connecting Gmail (for incident analysis)

Do this once. You need a Google account and about 10 minutes.

**1. Create a Google OAuth client** at https://console.cloud.google.com:

1. Create a project (or pick one).
2. **APIs & Services → Library →** search **Gmail API → Enable**.
3. **APIs & Services → OAuth consent screen:** choose **External**, fill in an app name and your email,
   and add the Gmail address you'll watch under **Test users**.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID → Desktop app → Create →
   Download JSON.**
5. Save the file as `backend/credentials.json`.

**2. Sign in** (from `backend/`, with `.venv` active):

```bash
python -m connectors.gmail auth --modify
```

A browser opens. Pick the account, click **Continue** on "Google hasn't verified this app" (expected for
your own app), and allow access. This saves `backend/token.json`. `--modify` lets J.A.R.V.I.G. label
and move emails; it never deletes or sends any.

**3. Describe your alerts.** Copy the example rules and edit them:

```bash
cp alert_filters.example.json alert_filters.json
```

The file decides which emails are alerts and how they're sorted. See
[Alert rules](#alert-rules-alert_filtersjson) for every option.

**4. Check the rules without changing anything.** The example starts with `"dry_run": true`:

```bash
python -m connectors.gmail_watcher once
```

Each alert is listed with what would happen to it (`CANDIDATE`, `DISCARDED`, `ANALYZED`) and why. Adjust
the rules until it's right, then set `"dry_run": false`.

Now answer **"yes"** when J.A.R.V.I.G. greets you, or say "enable incident analysis".

## Connecting FTP (for the incident dispatcher)

Fill in the FTP section of `backend/.env`:

```ini
FTP_HOST=ftp.example.com
FTP_PORT=21
FTP_USER=jarvig
FTP_PASSWORD=your-password
FTP_DIR=/incidents
FTP_TLS=true
```

`FTP_TLS=true` encrypts the connection (FTPS). Only set it to `false` if your server has no TLS: plain
FTP sends the password and the email contents unencrypted.

Preview the file for one email before sending anything (message IDs come from
`python -m connectors.gmail list -q "label:candidates"`):

```bash
python -m connectors.incident_dispatcher preview <message_id>
```

Restart the backend, then say "enable incident dispatcher". **On its first run it uploads every email
currently in `candidates`.**

## Connecting GitHub (for the MR summarizer and MR reviewer)

**1. Create a token** at https://github.com/settings/personal-access-tokens → **Generate new token**
(fine-grained):

- **Repository access:** *Only select repositories* → the repository to watch.
- **Permissions → Repository:** *Pull requests: Read and write* and *Contents: Read-only*.

**2. Add it to `backend/.env`:**

```ini
GITHUB_TOKEN=github_pat_...
GITHUB_REPO=owner/repository
```

Summaries and reviews are posted on the pull requests, **visible to everyone who can see them**, and
appear under the account the token belongs to.

**3. Try it on one pull request first** (prints only; posts nothing):

```bash
python -m connectors.github_prs list
python -m connectors.github_prs summarize <number>
python -m connectors.github_prs review <number>
```

Restart the backend, then say "start the MR summarizer" and/or "start the MR reviewer". **When switched
on, each one handles every open pull request that doesn't have its comment yet**, up to 3 per check.

---

## Using J.A.R.V.I.G.

### Things you can say or type

| Say | What happens |
|---|---|
| "yes" / "no" (to the greeting) | Switches incident analysis on, or leaves it off |
| "enable incident analysis" / "turn off incident analysis" | Starts or stops sorting your alert emails |
| "enable incident dispatcher" / "turn off incident dispatcher" | Starts or stops uploading candidates to FTP |
| "start incident dispatcher and incident analysis", "stop both" | Several at once |
| "start the dispatcher and stop incident analysis" | Different actions in one sentence |
| "start the MR summarizer" / "turn off the MR reviewer" | Starts or stops one pull request feature |
| "turn on the MR features", "start the summarizer but stop the reviewer" | Both pull request features, or one each |
| "stop everything" | All four features |
| "is incident analysis running?", "status of both" | State and what's been handled so far ("both" = the two incident features) |
| "what did the last pull request change?" | Answered by the AI, which is given the latest summaries and reviews |
| anything else | Answered by the AI |

These commands are handled by J.A.R.V.I.G. itself, not the AI, so they're instant and work with every
provider. All four features start **off** each time the backend starts.

### The screen

- **Centre:** the core changes colour with J.A.R.V.I.G.'s state (idle, listening, thinking, speaking, offline).
- **Right, Conversation:** everything said so far.
- **Left, Systems:** connection, AI provider and speed, both services with their counts, and on/off
  buttons for each. (Hidden on windows narrower than 1100 px; voice commands still work.)
- **Bottom:** the text box, microphone button and voice on/off.

**Shortcuts:** **Ctrl+Space** talk · **Enter** send · **Esc** stop the reply and the voice.

Browsers only allow speech after your first click or key press, so the greeting is read aloud on your
first click.

### How an alert moves through Gmail

```
new alert email
      │  incident analysis
      ├──────────────▶ discarded     not worth analysing (e.g. recovered, test environment)
      ├──────────────▶ analyzed      analysis reports ("Alert report: …")
      ▼
  candidates                         worth analysing; stays in the inbox
      │  incident dispatcher: uploaded to FTP as <date>_<object>_<gmail id>.txt
      ▼
  dispatched
      │  its "Alert report: <that file>.txt" arrives (incident analysis must be on)
      ▼
  analyzed
```

These "folders" are Gmail labels, shown in Gmail's left sidebar. Nothing is ever deleted. Move an email
back by hand in Gmail if it was sorted wrongly.

Each uploaded file contains the incident date (from the alert itself), the email headers and the full
email text.

### How pull requests are summarized and reviewed

Every `PR_POLL_SECONDS` (default 2 minutes), each feature that is on looks at **every open pull request**
of `GITHUB_REPO` and checks on GitHub whether it already did its part. What it posts carries a hidden
marker with the commit it covers, and only markers written by the token's account count. So a pull
request opened while J.A.R.V.I.G. was off is still picked up, and a post that failed is simply retried
on the next check.

| | MR summarizer | MR reviewer |
|---|---|---|
| **Posts** | One general comment on the pull request with a summary of what the code changes | A GitHub review with a comment on each faulty line saying what to change, as a one-click *suggestion* when the fix is exact code |
| **No problems?** | n/a | Still posts a review saying *no problems found*, which marks it as reviewed |
| **New commits** | Edits its summary comment to match the latest code | Reviews only the changes since the commit it last reviewed |

- The AI only sees the **code diff**; the description and commit messages are deliberately not used.
  Lock files and binaries are left out; diffs longer than `PR_MAX_DIFF_CHARS` are cut off and the
  result says it is partial.
- Findings on lines GitHub doesn't allow comments on (outside the diff) are listed in the review's
  overall text instead of being dropped.
- Reviews are comment-only: J.A.R.V.I.G. never approves or blocks a merge.
- Each result is also said aloud in short and shown in full in the conversation, and the latest ones are
  given to the AI so you can ask about them.
- Reviews come from an AI and can be wrong: treat them as suggestions.

---

## Troubleshooting

| You see | Fix |
|---|---|
| `Address already in use` | A backend is already running. Stop it with Ctrl+C in its tab, or `lsof -ti:8000 \| xargs kill`. |
| HUD says **offline** (grey) | The backend isn't running. Start it (step 5, tab 1). |
| Systems shows provider **echo** when you chose another | `.env` must be in `backend/`. Restart the backend after editing `.env`. |
| `Claude Code CLI not found` | Install it (step 3), then restart the backend. |
| `Claude Code is not signed in` | `~/.local/bin/claude auth login` |
| `Gmail is not authorised for this yet` | `python -m connectors.gmail auth --modify` (from `backend/`) |
| Google says **Access blocked** | Add your address under **OAuth consent screen → Test users**. Work (Workspace) accounts may need an admin's approval. |
| `OAuth client file not found` | Put the downloaded JSON at `backend/credentials.json`. |
| `rateLimitExceeded` warnings | Google's per-minute limit. J.A.R.V.I.G. waits and retries on its own. |
| `alert_filters.json not found` | `cp alert_filters.example.json alert_filters.json` in `backend/`. |
| `FTP login failed` / `FTP server refused TLS` | Check `FTP_USER`/`FTP_PASSWORD`; set `FTP_TLS=false` only if the server has no TLS. |
| `GITHUB_TOKEN is empty` / `GitHub rejected the token (401)` | Create a token ([Connecting GitHub](#connecting-github-for-the-mr-summarizer-and-mr-reviewer)) and put it in `backend/.env`; restart. |
| `Repository … not found (404)` | Check `GITHUB_REPO` is `owner/name`, and that the token was given access to that repository. |
| `GitHub refused to post (403)` | The token needs *Pull requests: Read and write* on that repository. |
| `Operation not permitted` in a terminal | That tab is in a folder that was moved or deleted. `cd` into the project again. |
| No voice | Click the page once; use Chrome or Edge; check the voice toggle at the bottom. |

---

## Reference

### Settings (`backend/.env`)

| Setting | Default | Meaning |
|---|---|---|
| `ASSISTANT_NAME` | `J.A.R.V.I.G.` | Name shown in the HUD and used in the greeting |
| `SYSTEM_PROMPT` | | Personality and instructions for the AI |
| `AGENT_PROVIDER` | `echo` | `echo`, `claude_code`, `anthropic`, `openai`, `webhook` or `custom` |
| `CLAUDE_CODE_CLI`, `CLAUDE_CODE_MODEL` | `claude`, account default | CLI path and optional model (`sonnet`, `opus`) |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | | For `anthropic` |
| `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL` | OpenAI | For `openai` and compatible servers |
| `WEBHOOK_URL`, `WEBHOOK_TOKEN` | | For `webhook` |
| `CUSTOM_AGENT` | example agent | For `custom`, as `module:ClassName` |
| `FTP_HOST`, `FTP_PORT`, `FTP_USER`, `FTP_PASSWORD`, `FTP_DIR`, `FTP_TLS` | `21`, `/incidents`, `true` | Incident dispatcher target |
| `DISPATCH_POLL_SECONDS` | `60` | How often the dispatcher checks for candidates |
| `GITHUB_TOKEN`, `GITHUB_REPO` | | Token and `owner/name` of the repository for the MR features |
| `PR_POLL_SECONDS`, `PR_MAX_DIFF_CHARS` | `120`, `60000` | How often to check the pull requests; diff size limit |
| `CORS_ORIGINS`, `HISTORY_LIMIT` | | Allowed frontend origins; messages remembered per conversation |

`.env` is read from `backend/` (or the project root) wherever you start the backend from.

### Files J.A.R.V.I.G. keeps in `backend/`

These are always looked for in `backend/`, whichever folder you start from. Their locations are **not**
settings in `.env`: putting `GMAIL_…` lines in `.env` has no effect.

| File | What it is | Created by |
|---|---|---|
| `credentials.json` | Google OAuth client | You, downloaded from Google Cloud Console |
| `token.json` | Your Gmail sign-in | `python -m connectors.gmail auth --modify` |
| `alert_filters.json` | Incident analysis rules | You, copied from `alert_filters.example.json` |
| `data/candidates.jsonl` | Queue of candidate emails | Incident analysis |
| `data/pr_summarizer_state.json`, `data/pr_reviewer_state.json` | Latest results, for follow-up questions (what's done is read from GitHub) | MR summarizer / reviewer |

To keep one elsewhere, export an environment variable in the terminal **before** starting the backend,
for example `export GMAIL_TOKEN_FILE=/secure/token.json`. The variables are `GMAIL_CREDENTIALS_FILE`,
`GMAIL_TOKEN_FILE`, `GMAIL_FILTERS_FILE` and `GMAIL_CANDIDATES_FILE`; relative paths are from `backend/`.

### Alert rules (`alert_filters.json`)

```jsonc
{
  "watch_query": "in:inbox from:alerts@example.com",   // which emails are alerts (any Gmail search); others are never touched
  "lookback": "1d",                                   // only look at alerts this recent
  "poll_seconds": 60,                                 // how often incident analysis checks
  "dry_run": true,                                    // true = only log what would happen
  "labels": { "candidate": "candidates", "discarded": "discarded", "dispatched": "dispatched" },
  "folders": [                                        // checked first; these are never discarded
    { "label": "analyzed", "if_any": [{ "subject": "alert report" }], "collects_dispatched": true }
  ],
  "discard_if_any":   [{ "subject_regex": "ALERT \\w+ OK:" }],       // then these are discarded
  "candidate_if_any": [{ "subject_regex": "ALERT PROD FEHLER:" }]    // then these are candidates; the rest is discarded
}
```

- **Rule fields:** `from`, `to`, `subject`, `body` (a word or list of words, case-insensitive; any word
  matches), `subject_regex`, `body_regex`, `has_attachment`. Every field in one rule must match.
- **`collects_dispatched`:** when a report in this folder names a dispatched file
  (`…_<gmail id>.txt`), the original email moves from dispatched into this folder too.
- Test one email's decision: `python -m connectors.gmail_watcher test <message_id>`.

### Command-line tools (run from `backend/`)

| Command | Does |
|---|---|
| `python -m connectors.gmail auth [--modify]` | Sign in to Gmail |
| `python -m connectors.gmail list -q "<search>" -n 10` | List emails |
| `python -m connectors.gmail read <id>` / `thread <id>` | Print an email or conversation in full |
| `python -m connectors.gmail_watcher once [--dry-run]` / `watch` / `test <id>` | Incident analysis without the HUD |
| `python -m connectors.incident_dispatcher preview <id>` / `once` / `watch` | Incident dispatcher without the HUD |
| `python -m connectors.github_prs list` / `summarize <n>` / `review <n>` | Pull request features without the HUD (prints only) |

### Backend API

| Method | Path | Purpose |
|---|---|---|
| WS | `/ws?session=<id>` | Streaming chat used by the HUD (protocol at the top of `bridge/main.py`) |
| POST | `/api/chat` | `{"text", "session_id"}` → `{"reply"}`, for scripts |
| GET / POST | `/api/incidents` | Incident analysis status / `{"enabled": true}` |
| GET / POST | `/api/dispatcher` | Incident dispatcher status / `{"enabled": true}` |
| GET | `/api/agent` | Assistant name, provider and model |
| POST | `/api/sessions/{id}/reset` | Clear a conversation's memory |
| GET | `/api/health` | Liveness |

### How it's built

```
┌──────────────┐  WebSocket /ws   ┌──────────────────┐  Agent interface  ┌────────────────────────┐
│  Frontend    │ ───────────────▶ │  Bridge (FastAPI)│ ────────────────▶ │  Agent: echo / claude_ │
│  React HUD   │ ◀─── tokens ──── │  commands        │ ◀── text stream ─ │  code / anthropic /    │
│  voice in+out│   REST /api/*    │  services ───────┼──▶ Gmail, FTP     │  openai / webhook /    │
└──────────────┘                  └──────────────────┘                   │  custom                │
                                                                         └────────────────────────┘
```

| Folder | Contents |
|---|---|
| `frontend/src` | The HUD: `App.jsx`, `components/`, `hooks/useBridge.js` (connection), `hooks/useSpeech.js` (voice) |
| `backend/bridge` | Web server (`main.py`), built-in commands (`commands.py`), background services (`services.py`), settings (`config.py`) |
| `backend/agent` | AI providers (`adapters/`) and a template for your own (`examples/my_agent.py`) |
| `backend/connectors` | Gmail (`gmail.py`), incident analysis (`gmail_watcher.py`), incident dispatcher (`incident_dispatcher.py`), MR summarizer and reviewer (`github_prs.py`) |

**Your own agent:** subclass `agent.base.Agent`, implement `async def stream(self, messages, system)`
yielding text, and set `AGENT_PROVIDER=custom`, `CUSTOM_AGENT=module:Class`.
**Webhook agents** receive `{"system", "messages"}` and may reply with an event stream, JSON `{"reply"}`
or plain text.

**Customising:** colours are tokens at the top of `frontend/src/styles.css`; conversation memory is
in-memory (`bridge/sessions.py`).

**Docker:** `docker compose up --build` serves the HUD at http://localhost:8080. It doesn't support
`claude_code` (the CLI and its sign-in live on your machine), and the Gmail and FTP files must be in
`backend/` before building.

### Security

- Never commit `backend/.env`, `credentials.json`, `token.json` or `alert_filters.json`; `.gitignore`
  already excludes them. Anyone with `token.json` can read and label your email, and anyone with
  `GITHUB_TOKEN` can act on the repository within the token's permissions.
- The MR features send pull request diffs to the AI provider you configured.
- The backend has no login. Keep it on your own machine, or put authentication in front of it before
  exposing it to a network.
