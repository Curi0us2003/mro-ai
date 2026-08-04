# ✈️ AI Maintenance Voice Copilot

> **A Voice-First AI Assistant for Aircraft Maintenance Technicians**
>
> AI Maintenance Voice Copilot is an intelligent, voice-first maintenance assistant that enables aircraft technicians to perform inspections naturally through conversation while automatically generating structured maintenance records, answering technical questions from maintenance manuals, and creating professional maintenance reports.
>
> The solution runs on **SAP HANA Cloud** and **SAP AI Core / Generative AI Hub**, providing an enterprise-ready AI copilot for modern aircraft maintenance operations.

---

# 📖 Table of Contents

- Project Vision
- Problem Statement
- Solution Overview
- Key Features
- System Architecture
- AI Agent
- Technology Stack
- Project Structure
- Application Workflow
- Backend Modules
- API Endpoints
- Access Model
- Environment Variables
- Installation
- Running the Application
- Ingesting Maintenance Manuals
- Managing User Accounts
- Future Roadmap
- License

---

# 🚀 Project Vision

Aircraft maintenance technicians spend a significant amount of time documenting inspections rather than performing maintenance.

Current maintenance systems require technicians to:

- Stop working
- Remove gloves
- Walk to a workstation
- Search maintenance manuals
- Fill multiple maintenance forms
- Enter maintenance findings manually

This process is slow, error-prone and often results in delayed documentation.

The AI Maintenance Voice Copilot transforms this workflow by allowing technicians to simply speak naturally while working.

The AI understands the inspection, asks intelligent follow-up questions, retrieves technical information, and automatically generates structured maintenance records.

The long-term objective is to preserve decades of maintenance expertise while significantly reducing maintenance documentation time.

---

# ❗ Problem Statement

Aircraft maintenance engineers work in challenging environments:

- Loud hangars
- Tight spaces
- Gloves
- Hands occupied
- Time-critical inspections

Traditional maintenance software interrupts their workflow.

Experienced engineers also possess valuable knowledge that is rarely documented. When they retire, that knowledge is lost.

This project captures that expertise through continuous AI-assisted conversations.

---

# 💡 Solution Overview

The AI Maintenance Voice Copilot enables technicians to:

- Speak naturally during inspections
- Record maintenance findings using voice
- Receive spoken AI responses
- Ask technical questions
- Search maintenance manuals
- Automatically generate structured maintenance records
- Generate professional PDF reports
- Store maintenance history inside SAP HANA Cloud

Supervisors get a separate, read-only view across every technician's records, filterable by aircraft registration.

---

# ✨ Key Features

## 🎤 Voice-First Maintenance

The technician speaks; the copilot asks short, targeted follow-up questions until a finding is complete - aircraft registration, component, finding, severity, location, recommended action.

## 🤖 Intelligent AI Conversation

The AI:

- Understands context across the whole session
- Asks one natural follow-up question at a time
- Saves each field as soon as it is captured, not just once the finding is complete
- Confirms the finding back to the technician when done

## 📚 Technical Knowledge Assistant

The technician can ask questions such as torque specs, part numbers, inspection intervals or procedures. The copilot answers **only** from passages retrieved from the ingested aircraft manuals, and cites them as `[file, p.N]` - if nothing relevant is found, it says so rather than guessing.

## 📝 Structured Maintenance Records

Each finding captures: aircraft registration, component, finding, severity, location, recommended action, technician, timestamp and status.

## 📄 Automatic PDF Generation

A professional report is generated on demand for any record, including the inspection details table and the full conversation transcript.

## 🧠 Organisational Knowledge Capture

Every conversation turn is persisted to SAP HANA Cloud, linked to its maintenance record, building a searchable history of technician findings over time.

---

# 🏗️ System Architecture

```
                      Browser (frontend/)
                Plain HTML + CSS + vanilla JS
                              │
                    Flask HTTP API (same origin)
                              │
                  backend/app.py  (routes, sessions, auth)
                              │
              ┌───────────────┼───────────────┐
              │               │               │
     backend/agent.py   backend/speech_   backend/text_to_
     MaintenanceAgent    to_text.py       speech.py
     (tool-calling loop)  (Gemini STT)     (Piper TTS, local)
              │
              ├── backend/embeddings.py  ─┐
              │                           │
              └── backend/database.py ────┼── SAP HANA Cloud
                                           │   (users, manuals,
                  SAP AI Core /            │    manual_chunks +
                  Generative AI Hub  ──────┘    vectors, records,
                  (gpt-4.1 chat,                 conversations)
                   text-embedding-3-large,
                   gemini-2.5-flash STT)

            backend/pdf_service.py (ReportLab) generates
            downloadable PDF reports from stored records.
```

There is no separate frontend build step and no separate frontend server: Flask serves `frontend/index.html`, `style.css` and `app.js` directly as static files, and the browser talks to the same origin's `/api/...` routes.

---

# 🤖 AI Agent

There is a single agent, `MaintenanceAgent` in [`backend/agent.py`](backend/agent.py) - one instance per active inspection session, kept in an in-memory dict keyed by `session_id`. It drives a tool-calling loop against a chat model deployed in SAP AI Core (`gpt-4.1` by default) with three tools:

| Tool | Purpose |
|------|---------|
| `search_maintenance_knowledge` | Embeds the technician's question and runs semantic search over ingested manual chunks in HANA. Only returns/cites what was actually retrieved. |
| `create_or_update_maintenance_record` | Creates the record on first call, updates whichever fields changed on later calls. Invoked every time new information is learned, even partial. |
| `get_current_record` | Reads back the record state built so far in this conversation. |

Two entry points exist for a turn:

- `send()` - blocks until the full reply is ready (used by the non-streaming `/message` and `/voice` routes, useful for testing).
- `send_stream()` - yields the reply token-by-token as it's generated (used by the `/message/stream` and `/voice/stream` routes the frontend actually calls).

The Flask layer additionally splits a streamed reply into sentences as they complete and synthesizes each one to speech immediately, so audio playback starts after the first sentence rather than after the whole reply.

---

# 💻 Technology Stack

## Backend

- Python
- Flask
- SAP HANA Cloud (`hdbcli`)
- SAP AI Core / Generative AI Hub (`generative-ai-hub-sdk`)
- Piper (local text-to-speech)
- pdfplumber (manual text extraction)
- ReportLab (PDF report generation)
- python-dotenv

## Frontend

- Plain HTML, CSS and vanilla JavaScript (no build step, no framework)
- MediaRecorder API for in-browser voice capture

## Database

- SAP HANA Cloud, including its native vector engine (`COSINE_SIMILARITY` / `REAL_VECTOR`) for manual semantic search

## AI & Voice (all via SAP AI Core / Generative AI Hub, except TTS)

- Chat: `gpt-4.1`
- Embeddings: `text-embedding-3-large` (truncated to `EMBEDDING_DIM`)
- Speech-to-text: `gemini-2.5-flash` (multimodal - there is no Whisper deployment in the hub)
- Text-to-speech: Piper, running locally with no network call

---

# 📂 Project Structure

```
mro-ai/
│
├── backend/
│   ├── app.py                    Flask app, routes, session store, streaming
│   ├── agent.py                  MaintenanceAgent - tool-calling conversation loop
│   ├── auth.py                   Login, sessions, password hashing, role decorators
│   ├── config.py                 Loads .env - the only os.getenv() call site
│   ├── database.py               All SAP HANA Cloud access (CRUD + semantic search)
│   ├── embeddings.py             Text -> vector, via SAP AI Core
│   ├── speech_to_text.py         Audio -> text, via Gemini (SAP AI Core)
│   ├── text_to_speech.py         Text -> speech, via local Piper
│   ├── pdf_service.py            Maintenance record -> PDF report (ReportLab)
│   └── scripts/
│       ├── ingest_manuals.py     Chunk + embed PDFs in manuals/ into HANA
│       ├── chat_manuals.py       Standalone CLI to query ingested manuals
│       └── manage_users.py       Create/list/disable user accounts (no sign-up API)
│
├── frontend/
│   ├── index.html                Login, technician workspace, supervisor view
│   ├── app.js                    All frontend logic (auth, voice, streaming, tables)
│   └── style.css
│
├── manuals/                       Source PDF manuals to ingest
├── uploads/                        Technician voice recordings (gitignored)
├── generated_reports/              Generated PDF reports (gitignored)
├── audio_output/                   Synthesized reply audio (gitignored)
├── voices/                         Piper voice model files (gitignored)
├── logs/                           Application logs (gitignored)
│
├── requirements.txt
├── .env                            Not committed - copy from .env.example
├── .env.example
└── README.md
```

---

# 🔄 Application Workflow

```
Technician signs in
        │
        ▼
Start an inspection session (POST /api/sessions)
        │
        ▼
🎤 Voice recording (browser MediaRecorder)
        │
        ▼
Speech-to-Text (Gemini, via SAP AI Core)
        │
        ▼
MaintenanceAgent.send_stream()
        │
        ├──> search_maintenance_knowledge   (semantic search over manuals)
        └──> create_or_update_maintenance_record   (saved incrementally)
        │
        ▼
Reply streamed back sentence-by-sentence,
each sentence synthesized to speech (Piper) as it completes
        │
        ▼
Record marked complete once every required field is captured
        │
        ▼
Technician (or a supervisor) downloads the PDF report on demand
```

---

# 🔌 Backend Modules

| Module | Responsibility |
|--------|-----------------|
| `app.py` | HTTP routes, session lifecycle, streaming responses, error handling |
| `agent.py` | Conversation state, tool schema, tool-calling loop |
| `auth.py` | Login/logout, password hashing, `login_required` / `role_required` decorators |
| `database.py` | All SAP HANA Cloud reads/writes, semantic search |
| `embeddings.py` | Embedding calls (batched, with retry) |
| `speech_to_text.py` | Audio validation + transcription |
| `text_to_speech.py` | Speech normalisation (spelling out part numbers, units, abbreviations) + synthesis |
| `pdf_service.py` | PDF report rendering |

---

# 🌐 API Endpoints

| Endpoint | Method(s) | Access | Description |
|----------|-----------|--------|-------------|
| `/api/health` | GET | Public | Liveness check |
| `/api/auth/login` | POST | Public | Sign in |
| `/api/auth/logout` | POST | Any signed-in user | Sign out |
| `/api/auth/me` | GET | Any signed-in user | Restore session on page load |
| `/api/sessions` | POST | Technician | Start an inspection session |
| `/api/sessions/<id>` | GET, DELETE | Technician (own session) | Session status / end session |
| `/api/sessions/<id>/message` | POST | Technician (own session) | Text turn (blocking) |
| `/api/sessions/<id>/message/stream` | POST | Technician (own session) | Text turn (NDJSON streaming) |
| `/api/sessions/<id>/voice` | POST | Technician (own session) | Voice turn (blocking) |
| `/api/sessions/<id>/voice/stream` | POST | Technician (own session) | Voice turn (NDJSON streaming) |
| `/api/sessions/<id>/speak` | POST | Technician (own session) | Synthesize arbitrary text to speech |
| `/api/audio/<file_name>` | GET | Any signed-in user | Fetch synthesized audio clip |
| `/api/records` | GET | Any signed-in user | List records (own only for technicians, all for supervisors) |
| `/api/records/<id>` | GET | Owner or supervisor | Record detail + conversation transcript |
| `/api/records/<id>/report` | GET, POST | Owner or supervisor | Generate/download the PDF report |
| `/api/manuals` | GET | Supervisor | Ingested manual list + chunk count |

---

# 🔐 Access Model

There is **no sign-up endpoint**. Accounts are provisioned out of band by an administrator:

```bash
python -m backend.scripts.manage_users add \
    --username jsmith --role technician --full-name "J. Smith"
```

- **TECHNICIAN** - can run inspection sessions and see only their own records.
- **SUPERVISOR** - can see and report on every record, but does not run inspection sessions.

Sessions are Flask's signed-cookie session (`SECRET_KEY`); every request re-reads the account from the database, so disabling a user takes effect on their very next request.

---

# ⚙️ Environment Variables

Copy [`.env.example`](.env.example) to `.env` in the project root and fill in real values:

```bash
cp .env.example .env
```

`.env` is loaded once, in `backend/config.py`, which also fails fast at startup if any required variable (`HANA_*`, `SECRET_KEY`, `AICORE_*`) is missing. See `.env.example` for the full list with explanations, grouped as:

- Application (`SECRET_KEY`, `DEBUG`, `PORT`, session settings, `HTTPS_ADHOC`)
- SAP HANA Cloud connection
- SAP AI Core / Generative AI Hub credentials
- Model selection (chat, embeddings, speech-to-text)
- Embedding dimension/batching
- Piper text-to-speech settings

> **Important:** Never commit your `.env` file. It's already covered by `.gitignore`.

---

# ▶️ Installation

Clone the repository and install dependencies:

```bash
git clone <repository-url>
cd mro-ai

python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate

pip install -r requirements.txt
```

Set up your environment file (see above), then create at least one account:

```bash
python -m backend.scripts.manage_users add \
    --username jsmith --role technician --full-name "J. Smith"
```

The database schema (`USERS`, `MANUALS`, `MANUAL_CHUNKS`, `MAINTENANCE_RECORDS`, `CONVERSATIONS`) is assumed to already exist in your HANA schema - this app's runtime user only has DML rights and cannot create tables. If you're pointing at a fresh schema, create those tables first via a privileged connection (e.g. SAP HANA Database Explorer).

Optionally, download a Piper voice into `voices/` for spoken replies:

```
voices/en_US-lessac-medium.onnx
voices/en_US-lessac-medium.onnx.json
```

If the voice files aren't present, the app still works - replies just come back as text without audio.

---

# ▶️ Running the Application

Flask serves both the API and the static frontend from the same process - there is nothing to build or run separately.

```bash
python -m backend.app
```

By default this serves over **HTTPS with a self-signed certificate** (`HTTPS_ADHOC=true` - see Environment Variables below), because browsers only expose the microphone to a secure context: HTTPS, or `http://localhost` on the same machine. If a technician needs to reach the app from a phone or tablet on the hangar network rather than from the server's own machine, plain HTTP won't let them record voice at all.

The certificate is generated once into `certs/dev.crt` / `certs/dev.key` and reused on every future start - **not** regenerated on every restart (that would be Werkzeug's `ssl_context="adhoc"`, which breaks with `DEBUG=true`'s auto-reloader: every restart would swap in a new certificate and invalidate the browser's "proceed anyway" exception, making the page look like it never loads).

Open, from the same machine:

```
https://localhost:5000
```

Or, from another device on the same network, replace `localhost` with the server's LAN IP, e.g. `https://192.168.1.42:5000`.

**The first time**, the browser will show a "Your connection is not private" warning - this is expected for any self-signed certificate, in every browser. Click **"Advanced" → "Proceed"** once; because the certificate is now reused across restarts, it won't ask again on this machine unless `certs/` is deleted (e.g. to force a fresh certificate after the LAN IP changes).

**To skip that warning entirely** (recommended if you find yourself clicking through it often), issue a certificate trusted by the OS instead, using [mkcert](https://github.com/FiloSottile/mkcert):

```bash
winget install --id FiloSottile.mkcert -e
mkcert -install                                        # installs a local CA into Windows' trust store, once
mkcert -cert-file certs/dev.crt -key-file certs/dev.key localhost 127.0.0.1 ::1 <your-lan-ip>
```

`backend/app.py` only checks whether `certs/dev.crt` / `certs/dev.key` already exist - it never overwrites a certificate that's already there, mkcert-issued or otherwise. If the LAN IP changes, rerun the `mkcert -cert-file ...` line with the new IP to reissue it.

If you only ever test from `http://localhost` on the same machine, you can set `HTTPS_ADHOC=false` in `.env` to skip HTTPS entirely and serve plain HTTP.

Using the Flask CLI instead of `python -m backend.app` serves plain HTTP regardless of `HTTPS_ADHOC` (pass `--cert=adhoc` yourself if you need HTTPS that way):

```bash
flask --app backend.app run --host 0.0.0.0 --port 5000
```

---

# 📚 Ingesting Maintenance Manuals

Drop PDF manuals into `manuals/`, then run:

```bash
# Ingest every new/changed manual
python -m backend.scripts.ingest_manuals

# Force re-ingestion of a manual already processed
python -m backend.scripts.ingest_manuals --force

# Ingest a single file
python -m backend.scripts.ingest_manuals --file B737_AMM.pdf

# Chunk only, no embeddings or database writes - sanity check
python -m backend.scripts.ingest_manuals --dry-run
```

Chunks are stored page-by-page so the copilot can cite `[file.pdf, p.147]` accurately. Manuals are skipped on re-run if their SHA-256 hash is unchanged.

To query the ingested manuals directly from the terminal (no login required):

```bash
python -m backend.scripts.chat_manuals "torque spec for the main gear bolts?"
# or, interactively:
python -m backend.scripts.chat_manuals
```

---

# 👤 Managing User Accounts

```bash
# Create an account
python -m backend.scripts.manage_users add --username jsmith --role technician --full-name "J. Smith"

# List accounts
python -m backend.scripts.manage_users list

# Reset a password
python -m backend.scripts.manage_users passwd --username jsmith

# Revoke / restore access
python -m backend.scripts.manage_users disable --username jsmith
python -m backend.scripts.manage_users enable  --username jsmith
```

---

# 📅 Future Roadmap

- SAP S/4HANA Integration
- SAP iMRO Integration
- SAP Knowledge Graph Integration
- SAP Joule Integration
- Predictive Maintenance
- AI Failure Pattern Detection
- Computer Vision for Defect Recognition
- Smart Glass Integration
- Offline Voice Processing
- Multi-language Support
- Mobile & Tablet Optimisation

---

# 🤝 Contributing

Contributions are welcome.

Please open an issue before submitting major feature requests or pull requests.

---

# 📄 License

This project is licensed under the MIT License.

---

# 👨‍💻 Developed By

**AI Maintenance Voice Copilot**

Built with:

- Python
- Flask
- SAP HANA Cloud
- SAP AI Core / Generative AI Hub
- Piper
- ReportLab
