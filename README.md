# ✈️ AI Maintenance Voice Assistant

> **A Voice-First AI Assistant for Aircraft Maintenance Technicians**
>
> AI Maintenance Voice Assistant is an intelligent, voice-first maintenance assistant that enables aircraft technicians to perform inspections naturally through conversation while automatically generating structured maintenance records, answering technical questions from maintenance manuals, and creating professional maintenance reports.
>
> The solution runs on **SAP HANA Cloud** and **SAP AI Core / Generative AI Hub**, providing an enterprise-ready AI assistant for modern aircraft maintenance operations.

---

# 📖 Table of Contents

- Project Vision
- Problem Statement
- Solution Overview
- Key Features
- System Architecture
- AI Agents
- Record Lifecycle
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
- Cleaning Up Test Data
- Posting to SAP
- Future Roadmap
- Contributing
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

The AI Maintenance Voice Assistant transforms this workflow by allowing technicians to simply speak naturally while working.

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

The AI Maintenance Voice Assistant enables technicians to:

- Speak naturally during inspections
- Record maintenance findings using voice
- Receive spoken AI responses
- Ask technical questions
- Search maintenance manuals
- Attach damage photos as evidence
- Automatically generate structured maintenance records
- Resume a half-finished finding later, with the assistant picking the conversation back up
- Generate professional PDF reports
- Store maintenance history inside SAP HANA Cloud

Supervisors get a separate oversight view across every technician's records: filter and search them, read the transcript and photos behind each one, correct fields the technician left thin, change a severity level, discard a finding that should never have been filed, and post a completed one to SAP. They also get their own read-only AI assistant for questions about the finding on screen or about the manuals.

---

# ✨ Key Features

## 🎤 Voice-First Maintenance

The technician speaks; the assistant asks short, targeted follow-up questions until a finding is complete - aircraft registration, component, finding, severity, location, recommended action.

## 🤖 Intelligent AI Conversation

The AI:

- Understands context across the whole session
- Asks one natural follow-up question at a time
- Saves each field as soon as it is captured, not just once the finding is complete
- Confirms the finding back to the technician when done

## 📚 Technical Knowledge Assistant

The technician can ask questions such as torque specs, part numbers, inspection intervals or procedures. The assistant answers **only** from passages retrieved from the ingested aircraft manuals, and cites them as `[file, p.N]` - if nothing relevant is found, it says so rather than guessing.

## 📝 Structured Maintenance Records

Each finding captures: aircraft registration, component, finding, severity, location, recommended action, technician, timestamp and status. Severity is a fixed vocabulary — **Minor, Moderate, Major, Critical, AOG** — and the assistant maps what the technician actually said onto it ("hairline, keep an eye on it" → Minor; "don't sign it off" → Critical), naming the level it chose so they can correct it in a word.

## 📷 Damage Photos

A technician can attach photos to a finding — the camera button opens the rear camera on a phone or tablet, and a file picker on a laptop. Photos are downscaled and re-encoded server-side, stored as BLOBs alongside the record, embedded in the PDF report, and visible to the supervisor. Entirely optional, but the assistant points out when a finding has none.

## ⏸️ Resume a Finding

A finding left half-done stays `OPEN` and can be picked back up from the technician's start page. On resume the earlier transcript is replayed into the conversation *and* into the model's context, and the assistant opens the turn itself — "you had VT-ABC's flap track down as cracked; what recommended action do you want on it?" — rather than leaving the technician to remember where they got to.

## 🧑‍✈️ Supervisor Oversight

Filter and search every technician's findings, expand one in place to read the record next to the transcript it came from, correct fields, change the severity from a dedicated picker, discard a bad record (transcript and photos with it), and post a completed one to SAP.

## 💬 Supervisor Assistant

A separate, strictly **read-only** chat for supervisors. It answers questions about the finding currently on screen ("is this severity reasonable?") and across the record set ("how many open majors on VT-ABC?"), and searches the manuals. It exposes no mutating tool at all — a supervisor reviewing evidence cannot alter the evidence through a chat box, even by asking.

## 📄 Automatic PDF Generation

A professional report is generated on demand for any record, including the inspection details table, the attached photos and the full conversation transcript.

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
        ┌─────────────────┬───────┴────────┬─────────────────┐
        │                 │                │                 │
 backend/agent.py  backend/assistant  backend/speech_  backend/text_to_
 MaintenanceAgent      .py             to_text.py       speech.py
 (writes records)  SupervisorAssistant (Gemini STT)   (Piper TTS, local)
        │           (read-only chat)
        │                 │
        ├── backend/embeddings.py ──┐
        │                           │
        └── backend/database.py ────┼── SAP HANA Cloud
                    │                │   (users, manuals,
                    │                │    manual_chunks + vectors,
        SAP AI Core / Generative ────┘    maintenance_records,
        AI Hub                            conversations, record_photos)
        (gpt-4.1 chat,
         text-embedding-3-large,
         gemini-2.5-flash STT)

 backend/photo_service.py  validates + downscales uploaded damage photos
 backend/pdf_service.py    (ReportLab) renders records as PDF reports
 backend/sftp_service.py   uploads a posted report to Azure Blob for SAP
```

There is no separate frontend build step and no separate frontend server: Flask serves `frontend/index.html`, `style.css` and `app.js` directly as static files, and the browser talks to the same origin's `/api/...` routes.

---

# 🤖 AI Agents

There are two, and the split between them is deliberate: **one writes, one cannot.**

## `MaintenanceAgent` — [`backend/agent.py`](backend/agent.py)

The technician's assistant. One instance per active inspection session, kept in an in-memory dict keyed by `session_id`. It drives a tool-calling loop against a chat model deployed in SAP AI Core (`gpt-4.1` by default) with four tools:

| Tool | Purpose |
|------|---------|
| `search_maintenance_knowledge` | Embeds the technician's question and runs semantic search over ingested manual chunks in HANA. Only returns/cites what was actually retrieved. |
| `create_or_update_maintenance_record` | Creates the record on first call, updates whichever fields changed on later calls. Invoked every time new information is learned, even partial. `severity` is constrained to the fixed vocabulary. |
| `get_current_record` | Reads back the record state built so far in this conversation, including its photo count. |
| `start_new_finding` | Detaches the session from the current record so the next utterance starts a fresh finding. Refuses while a technician-mandatory field is still missing. |

Three entry points exist for a turn:

- `send()` — blocks until the full reply is ready (used by the non-streaming `/message` and `/voice` routes, useful for testing).
- `send_stream()` — yields the reply token-by-token as it's generated (used by the `/message/stream` and `/voice/stream` routes the frontend actually calls).
- `resume_stream()` — the assistant's *opening* turn when a finding is reopened, driven by the record's own state rather than by anything the technician said. Backs `/opening/stream`.

The Flask layer additionally splits a streamed reply into sentences as they complete and synthesizes each one to speech immediately, so audio playback starts after the first sentence rather than after the whole reply.

## `SupervisorAssistant` — [`backend/assistant.py`](backend/assistant.py)

The supervisor's chat, one instance per signed-in supervisor (keyed by user id, so it survives clicking between records and a page refresh). Whichever finding is expanded on screen is injected as context each turn, so "this" and "it" resolve without restating which record is meant.

| Tool | Purpose |
|------|---------|
| `search_maintenance_knowledge` | The same manual search the technician's assistant uses. |
| `search_maintenance_records` | Queries across findings — "how many open majors on VT-ABC?" |
| `get_record_details` | Full detail of one record, including its photos. |

**Strictly read-only.** No mutating tool is exposed to it at all, so the model has no means to alter a record — not by accident, not by being asked nicely. A supervisor reviewing evidence must not be able to edit that evidence through a chat box.

---

# 🔄 Record Lifecycle

A record is created the moment the technician first names an aircraft and a component — not at the end — so nothing is lost if a session is interrupted.

```
   OPEN  ──────────────▶  COMPLETE  ──────────────▶  CLOSED
     │                        │                         │
 every field           supervisor posts            immutable:
 captured, or a          it to SAP and             the audit trail
 supervisor fills      the upload succeeds
 the gaps in
```

| Status | What it means | What is still possible |
|--------|---------------|------------------------|
| `OPEN` | Still being worked | Resume it, edit any field, attach photos, discard it |
| `COMPLETE` | Every required field captured | Edit fields, change severity, **attach photos**, discard, post to SAP |
| `CLOSED` | Posted to SAP | Nothing — read and report only |

Completing a finding is **not** a lock. A photo can still be attached and a supervisor can still correct any field; only posting to SAP makes a record immutable. Deletion goes through `delete_maintenance_record()`, which takes the transcript and photos with it — HANA has no `ON DELETE CASCADE` here, so deleting rows straight from the table orphans their children.

**Required fields differ by who is finishing the job.** A technician must capture aircraft registration, finding, component and location before moving to a new finding; severity and recommended action may be left for the supervisor, who must supply severity before marking a record `COMPLETE`.

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
│   ├── agent.py                  MaintenanceAgent - the technician's assistant (writes)
│   ├── assistant.py              SupervisorAssistant - read-only oversight chat
│   ├── auth.py                   Login, sessions, password hashing, role decorators
│   ├── config.py                 Loads .env - the only os.getenv() call site
│   ├── database.py               All SAP HANA Cloud access (CRUD + semantic search)
│   ├── embeddings.py             Text -> vector, via SAP AI Core
│   ├── speech_to_text.py         Audio -> text, via Gemini (SAP AI Core)
│   ├── text_to_speech.py         Text -> speech, via local Piper
│   ├── photo_service.py          Validate, downscale and re-encode damage photos
│   ├── pdf_service.py            Maintenance record -> PDF report (ReportLab)
│   ├── sftp_service.py           Upload a posted report to Azure Blob for SAP
│   └── scripts/
│       ├── ingest_manuals.py     Chunk + embed PDFs in manuals/ into HANA
│       ├── chat_manuals.py       Standalone CLI to query ingested manuals
│       ├── manage_users.py       Create/list/disable user accounts (no sign-up API)
│       └── cleanup_data.py       Delete test findings / orphan rows / throwaway accounts
│
├── frontend/
│   ├── index.html                Login, technician workspace, supervisor view
│   ├── app.js                    All frontend logic (auth, voice, streaming, tables)
│   └── style.css
│
├── docs/
│   └── sap/                       OData test payloads for the SAP-side Z table
│
├── manuals/                       Source PDF manuals to ingest
├── uploads/                        Technician voice recordings (gitignored)
├── generated_reports/              Generated PDF reports (gitignored)
├── audio_output/                   Synthesized reply audio (gitignored)
├── voices/                         Piper voice model files (gitignored)
├── certs/                          Self-signed dev certificate (gitignored)
├── logs/                           Application logs (gitignored)
│
├── schema_record_photos.sql        DDL for the optional RECORD_PHOTOS table
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
        ├─── Start a new session ────────┐
        │    (POST /api/sessions)        │
        │                                │
        └─── Resume an open finding ─────┤
             (POST /api/sessions         │
              with record_id)            │
                    │                    │
                    ▼                    │
       earlier transcript replayed       │
       into the model's context;         │
       assistant opens the turn itself     │
       (POST /opening/stream)            │
                    │                    │
                    └────────┬───────────┘
                             ▼
              🎤 Voice recording (browser MediaRecorder)
                             │
                             ▼
              Speech-to-Text (Gemini, via SAP AI Core)
                             │
                             ▼
              MaintenanceAgent.send_stream()
                             │
        ├──> search_maintenance_knowledge     (semantic search over manuals)
        ├──> create_or_update_maintenance_record  (saved incrementally)
        └──> start_new_finding                (move on to a separate defect)
                             │
                             ▼
              Reply streamed back sentence-by-sentence,
              each sentence synthesized to speech (Piper) as it completes
                             │
                  📷 optional damage photos attached to the finding
                             │
                             ▼
              Record auto-completes once every required field is captured
                             │
                             ▼
   ┌─────────────────────────┴─────────────────────────┐
   │                                                   │
   ▼                                                   ▼
Technician downloads                    Supervisor reviews: corrects fields,
the PDF report                          changes severity, discards a bad
                                        record, or posts it to SAP
                                                       │
                                                       ▼
                                        PDF uploaded to Azure Blob;
                                        record becomes CLOSED
```

---

# 🔌 Backend Modules

| Module | Responsibility |
|--------|-----------------|
| `app.py` | HTTP routes, session lifecycle, streaming responses, error handling |
| `agent.py` | Technician conversation state, tool schema, tool-calling loop, severity vocabulary |
| `assistant.py` | Supervisor chat — read-only tools only |
| `auth.py` | Login/logout, password hashing, `login_required` / `role_required` decorators |
| `config.py` | Loads `.env` — the only `os.getenv()` call site in the project |
| `database.py` | All SAP HANA Cloud reads/writes, semantic search, cascade deletes |
| `embeddings.py` | Embedding calls (batched, with retry) |
| `speech_to_text.py` | Audio validation + transcription |
| `text_to_speech.py` | Speech normalisation (spelling out part numbers, units, abbreviations) + synthesis |
| `photo_service.py` | Damage-photo validation, EXIF-aware downscaling, re-encoding |
| `pdf_service.py` | PDF report rendering, including embedded photos |
| `sftp_service.py` | Uploads a posted report to the Azure Blob container SAP reads from |

---

# 🌐 API Endpoints

### Auth

| Endpoint | Method(s) | Access | Description |
|----------|-----------|--------|-------------|
| `/api/health` | GET | Public | Liveness check |
| `/api/auth/login` | POST | Public | Sign in |
| `/api/auth/logout` | POST | Any signed-in user | Sign out |
| `/api/auth/me` | GET | Any signed-in user | Restore session on page load |

### Inspection sessions (technicians)

| Endpoint | Method(s) | Access | Description |
|----------|-----------|--------|-------------|
| `/api/sessions` | POST | Technician | Start a session; pass `record_id` to resume an `OPEN` finding |
| `/api/sessions/<id>` | GET, DELETE | Own session | Session status / end session |
| `/api/sessions/<id>/new-record` | POST | Own session | Detach from the current finding and start a fresh one |
| `/api/sessions/<id>/message` | POST | Own session | Text turn (blocking) |
| `/api/sessions/<id>/message/stream` | POST | Own session | Text turn (NDJSON streaming) |
| `/api/sessions/<id>/voice` | POST | Own session | Voice turn (blocking) |
| `/api/sessions/<id>/voice/stream` | POST | Own session | Voice turn (NDJSON streaming) |
| `/api/sessions/<id>/opening/stream` | POST | Own session | The assistant speaks first on a resumed finding (409 if none) |
| `/api/sessions/<id>/speak` | POST | Own session | Synthesize arbitrary text to speech |
| `/api/audio/<file_name>` | GET | Any signed-in user | Fetch a synthesized audio clip |

### Damage photos

| Endpoint | Method(s) | Access | Description |
|----------|-----------|--------|-------------|
| `/api/sessions/<id>/photos` | POST | Own session | Attach a photo to the finding in progress (refused once `CLOSED`) |
| `/api/records/<id>/photos` | GET | Owner or supervisor | Photo metadata for a record (no bytes) |
| `/api/photos/<photo_id>` | GET | Owner or supervisor | Serve one image; access decided by its record, not its id |
| `/api/photos/<photo_id>` | DELETE | Owner or supervisor | Remove a photo |

### Maintenance records

| Endpoint | Method(s) | Access | Description |
|----------|-----------|--------|-------------|
| `/api/records` | GET | Any signed-in user | List records — own only for technicians, all for supervisors |
| `/api/records/filters` | GET | Any signed-in user | Filter dropdown values, severity vocabulary, photo availability |
| `/api/records/<id>` | GET | Owner or supervisor | Record detail + conversation transcript |
| `/api/records/<id>` | PATCH | Supervisor | Edit fields and/or mark `COMPLETE` (refused once `CLOSED`) |
| `/api/records/<id>` | DELETE | Supervisor | Discard a record + its transcript and photos (refused once `CLOSED`) |
| `/api/records/<id>/report` | GET, POST | Owner or supervisor | Generate/download the PDF report |
| `/api/records/<id>/post-to-sap` | POST | Supervisor | Upload the report to SAP; record becomes `CLOSED` on success |

### Supervisor assistant & knowledge base

| Endpoint | Method(s) | Access | Description |
|----------|-----------|--------|-------------|
| `/api/assistant/chat` | POST | Supervisor | Ask the read-only assistant (NDJSON streaming) |
| `/api/assistant/reset` | POST | Supervisor | Clear the assistant's chat history |
| `/api/manuals` | GET | Supervisor | Ingested manual list + chunk count |

---

# 🔐 Access Model

There is **no sign-up endpoint**. Accounts are provisioned out of band by an administrator:

```bash
python -m backend.scripts.manage_users add \
    --username jsmith --role technician --full-name "J. Smith"
```

- **TECHNICIAN** — runs inspection sessions, attaches photos, and sees only their own records. Cannot edit, delete or post anything.
- **SUPERVISOR** — sees every record and can correct fields, change severity, mark complete, discard, and post to SAP. Cannot run inspection sessions. Their AI assistant is read-only even though they are not.

Scope is always decided server-side from the session, never from a client-supplied parameter: a technician's listing, filter dropdowns and photo access are all restricted to their own findings.

Sessions are Flask's signed-cookie session (`SECRET_KEY`); every request re-reads the account from the database, so disabling a user takes effect on their very next request rather than whenever their cookie expires.

---

# ⚙️ Environment Variables

Copy [`.env.example`](.env.example) to `.env` in the project root and fill in real values:

```bash
cp .env.example .env
```

`.env` is loaded once, in `backend/config.py`, which also fails fast at startup if any required variable (`HANA_*`, `SECRET_KEY`, `AICORE_*`) is missing. See `.env.example` for the full list with explanations, grouped as:

- Application (`SECRET_KEY`, `DEBUG`, `PORT`, session settings, `HTTPS_ADHOC`, `WARMUP_ON_STARTUP`)
- SAP HANA Cloud connection (`HANA_*`, including `HANA_POOL_SIZE`)
- SAP AI Core / Generative AI Hub credentials (`AICORE_*`)
- Model selection (chat, embeddings, speech-to-text)
- Embedding dimension/batching (`EMBEDDING_DIM`, `EMBEDDING_BATCH_SIZE`, `MIN_RELEVANCE_SCORE`)
- Piper text-to-speech settings (`PIPER_*`)
- Damage photos (`PHOTO_MAX_UPLOAD_BYTES`, `PHOTO_MAX_DIMENSION`, `PHOTO_JPEG_QUALITY`, `PHOTO_MAX_PER_RECORD`)
- SAP posting via Azure Blob (`AZURE_STORAGE_ACCOUNT_NAME`, `AZURE_STORAGE_CONTAINER`, `AZURE_STORAGE_SAS_TOKEN`, `AZURE_STORAGE_SAP_PREFIX`)

The photo and Azure groups are optional: without the Azure settings, "Post to SAP" returns a clear *not configured* error instead of failing obscurely, and everything else works unchanged.

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

The database schema (`USERS`, `MANUALS`, `MANUAL_CHUNKS`, `MAINTENANCE_RECORDS`, `CONVERSATIONS`) is assumed to already exist in your HANA schema - this app's runtime user only has DML rights and cannot create tables. If you're pointing at a fresh schema, create those tables first via a privileged connection (e.g. SAP HANA Database Explorer). The app verifies they are present at startup and gives an actionable error if not.

Damage photos need one more table, `RECORD_PHOTOS`, and it is optional. Run [`schema_record_photos.sql`](schema_record_photos.sql) through the same privileged connection to enable them. Until that table exists the photo feature hides itself in the UI rather than erroring, so you can skip it now and add it later — `/api/records/filters` reports whether it is available.

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

Chunks are stored page-by-page so the assistant can cite `[file.pdf, p.147]` accurately. Manuals are skipped on re-run if their SHA-256 hash is unchanged.

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

For a real person, `disable` beats deleting: it revokes access on their very
next request and keeps their findings attributed. Deleting an account is only
for one that should never have existed — see below.

---

# 🧹 Cleaning Up Test Data

A maintenance record is written the moment a technician first names an aircraft
and a component, so anything that drives the technician flow — including an
end-to-end test run — leaves real rows in `MAINTENANCE_RECORDS`. Deleting them
straight from the table in HANA Database Explorer removes the finding but
silently leaves its `CONVERSATIONS` turns and `RECORD_PHOTOS` blobs pointing at
a record id that no longer resolves; there is no `ON DELETE CASCADE`.

Use these instead — they take the child rows with them:

```bash
# What test / orphan data is in the schema? (read-only)
python -m backend.scripts.cleanup_data report

# Delete one finding, with its transcript and photos
python -m backend.scripts.cleanup_data record --record-id <uuid> --yes

# Delete every finding filed by a throwaway account, then the account
python -m backend.scripts.cleanup_data user --username verify.tech1 --yes

# Sweep up child rows whose record is already gone
python -m backend.scripts.cleanup_data orphans --yes
```

Nothing is deleted without `--yes`; every command prints what it *would* do
first. A `CLOSED` record has been posted to SAP and is refused everywhere.

A supervisor can do the same thing for a single finding from the UI — **Discard
record** in the expanded record row (`DELETE /api/records/<id>`).

---

# 📮 Posting to SAP

Once a record is `COMPLETE`, a supervisor can post it. The app generates the PDF
report and uploads it to the Azure Blob Storage container SAP reads from
([`backend/sftp_service.py`](backend/sftp_service.py) — named for the loader
pattern it follows, though the transport is the Azure Blob SDK, not SFTP). The
record becomes `CLOSED` **only if the upload actually succeeds**, so a failed
post leaves it editable and retryable rather than stranded.

Configure it with `AZURE_STORAGE_ACCOUNT_NAME`, `AZURE_STORAGE_CONTAINER`,
`AZURE_STORAGE_SAS_TOKEN` and `AZURE_STORAGE_SAP_PREFIX`. Without them the
button returns a clear *not configured* error.

## Structured payloads for a Z table

Today the posting is **the PDF only** — there is no structured field feed. If the
SAP side needs a Z table populated, [`docs/sap/`](docs/sap/) carries ready-to-post
OData bodies built from real records, plus the field mapping, the CSRF flow and a
`$batch` example.

Two things that matter when mapping the fields:

- `INSPECTION_TS` and `CREATED_AT` are HANA `TIMESTAMP(7)`, written as **UTC**.
  Map them to `UTCLONG` (or `TIMESTAMPL`, `DEC 21,7`) — never to a second-precision
  type, and never re-interpret them in local time.
- Send those two as **JSON strings**, not numbers. A 21-digit decimal does not fit
  in an IEEE-754 double and gets silently rounded.

There is deliberately **no `STATUS` field** in the Z table: only `COMPLETE` records
are ever posted, so the column would hold one constant value.

---

# 📅 Future Roadmap

- SAP S/4HANA Integration (structured field posting, beyond today's PDF drop)
- SAP iMRO Integration
- SAP Knowledge Graph Integration
- SAP Joule Integration
- Predictive Maintenance
- AI Failure Pattern Detection
- Computer Vision for Defect Recognition — automatic severity from the attached photo
- Smart Glass Integration
- Offline Voice Processing
- Multi-language Support
- A shared session store (Redis), so the app can run more than one worker

---

# 🤝 Contributing

Contributions are welcome.

Please open an issue before submitting major feature requests or pull requests.

---

# 📄 License

This project is licensed under the MIT License.

---

# 👨‍💻 Developed By

**AI Maintenance Voice Assistant**

Built with:

- Python
- Flask
- SAP HANA Cloud
- SAP AI Core / Generative AI Hub
- Piper
- ReportLab
