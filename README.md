# ✈️ AI Maintenance Voice Copilot

> **A Voice-First AI Assistant for Aircraft Maintenance Technicians**
>
> AI Maintenance Voice Copilot enables aircraft technicians to perform inspections entirely through natural conversation while automatically generating structured maintenance records, retrieving technical knowledge from manuals, and producing professional maintenance reports.

---

# 🚀 Project Vision

Aircraft maintenance technicians often work in environments where stopping to use a computer is impractical.

Traditional maintenance software requires technicians to:

- Remove gloves
- Walk to a workstation
- Search through lengthy manuals
- Enter maintenance records manually
- Complete multiple forms
- Remember findings after finishing the inspection

This leads to:

- Delayed documentation
- Human errors
- Missing inspection details
- Loss of institutional knowledge
- Reduced maintenance efficiency

The AI Maintenance Voice Copilot solves this by allowing technicians to simply **talk naturally** while working.

The AI:

- Listens continuously
- Understands maintenance conversations
- Asks intelligent follow-up questions
- Retrieves information from manuals
- Generates structured maintenance records
- Produces professional PDF reports
- Preserves organisational knowledge

---

# 🎯 Objectives

The application aims to:

- Enable completely voice-based aircraft inspections
- Minimise manual data entry
- Improve maintenance record quality
- Reduce technician workload
- Preserve expert knowledge
- Integrate with SAP ecosystems
- Support future predictive maintenance

---

# ✨ Key Features

## 🎤 Voice First Experience

- Continuous speech recognition
- Natural language conversations
- Live transcription
- Voice responses
- Hands-free workflow

---

## 🤖 Intelligent AI Conversation

Instead of filling forms manually, technicians simply speak naturally.

Example:

```
Technician

Found corrosion on the left engine turbine blade.

AI

Which aircraft are you inspecting?

Technician

VT-AAB

AI

Which stage?

Technician

Stage 2

AI

Severity?

Technician

Moderate.
```

The AI automatically builds the maintenance record.

---

## 📚 Technical Knowledge Assistant

The AI can answer questions like:

- What torque should I use?
- Show previous failures.
- When was this part replaced?
- Show inspection interval.
- Display maintenance history.

Knowledge comes from:

- Aircraft manuals
- Maintenance records
- Historical inspections
- SAP HANA Cloud
- Vector search

---

## 📝 Automatic Maintenance Record Generation

The AI automatically creates structured maintenance records including:

- Aircraft
- Registration
- Engine
- Component
- Part Number
- Finding
- Severity
- Recommendation
- Technician
- Date
- AI Summary

---

## 📄 PDF Report Generation

Professional reports include:

- Company logo
- Aircraft information
- Findings
- Severity
- Recommendations
- AI Summary
- Signature section

---

## 🧠 Knowledge Preservation

Every conversation becomes organisational knowledge.

The system stores:

- Conversations
- Maintenance findings
- Technician notes
- Recommendations
- Previous inspections
- Historical failures

---

# 🏗 System Architecture

```
                     React Frontend
                             │
               Voice + Text User Interface
                             │
                  WebSocket + REST API
                             │
                       FastAPI Backend
                             │
                LangGraph Agent Orchestrator
                             │
     ┌──────────────┬──────────────┬──────────────┐
     │              │              │
Conversation   Knowledge     Report Generation
Agent          Agent         Agent
     │              │              │
     └──────────────┼──────────────┘
                    │
              Core Services Layer
                    │
      ┌─────────────┼──────────────┐
      │             │              │
Speech To Text   LLM Service   PDF Service
      │             │              │
      └─────────────┼──────────────┘
                    │
             SAP HANA Cloud Database
```

---

# 🤖 AI Agents

## Conversation Agent

Responsible for:

- Talking naturally
- Asking follow-up questions
- Maintaining conversation flow

---

## Extraction Agent

Converts conversations into structured JSON.

---

## Validation Agent

Validates:

- Aircraft
- Components
- Severity
- Part Numbers

---

## Knowledge Agent

Retrieves:

- Manuals
- Torque specifications
- Previous failures
- Historical maintenance

---

## Report Agent

Generates:

- AI Summary
- PDF Reports
- SAP Ready Maintenance Record

---

# 💻 Technology Stack

## Backend

- Python 3.12
- FastAPI
- SQLAlchemy
- LangGraph
- LangChain
- OpenAI SDK
- Faster Whisper
- ReportLab
- Pydantic

---

## Frontend

- React
- TypeScript
- Vite
- Material UI
- TailwindCSS

---

## Database

SAP HANA Cloud

---

## AI

- GPT Models
- Whisper
- Text-to-Speech

---

# 📂 Project Structure

```
maintenance-ai-copilot/

│
├── backend/
│   ├── api/
│   ├── agents/
│   ├── services/
│   ├── models/
│   ├── prompts/
│   ├── utils/
│   ├── app.py
│   ├── config.py
│   ├── database.py
│   └── dependencies.py
│
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   ├── components/
│   │   ├── hooks/
│   │   ├── services/
│   │   └── assets/
│   └── package.json
│
├── manuals/
├── uploads/
├── generated_reports/
├── requirements.txt
├── .env.example
└── README.md
```

---

# 🔄 Application Workflow

```
Technician

↓

Voice Input

↓

Speech to Text

↓

Conversation Agent

↓

Information Extraction

↓

Validation

↓

Knowledge Retrieval

↓

Follow-up Questions

↓

Maintenance Record

↓

PDF Generation

↓

Save to SAP HANA
```

---

# 📡 Backend Services

| Service | Responsibility |
|----------|---------------|
| Speech To Text | Voice transcription |
| Text To Speech | AI voice responses |
| LLM Service | AI reasoning |
| HANA Service | Database access |
| Vector Service | Manual retrieval |
| PDF Service | Report generation |
| Conversation Memory | Session memory |

---

# 🌐 REST APIs

| Endpoint | Purpose |
|----------|----------|
| /conversation | Voice conversation |
| /maintenance | Maintenance records |
| /manuals | Manual search |
| /reports | PDF reports |
| /auth | Authentication |

---

# 🔐 Environment Variables

Create a `.env` file using `.env.example`.

Example:

```
OPENAI_API_KEY=

HANA_HOST=

HANA_PORT=

HANA_USER=

HANA_PASSWORD=

HANA_SCHEMA=

HANA_ENCRYPT=true
```

---

# ▶ Running Backend

```bash
cd backend

pip install -r requirements.txt

uvicorn app:app --reload
```

---

# ▶ Running Frontend

```bash
cd frontend

npm install

npm run dev
```

---

# 📈 Future Enhancements

- SAP iMRO Integration
- SAP S/4HANA Integration
- SAP Joule Integration
- Predictive Maintenance
- Computer Vision
- Smart Glass Support
- Offline Voice Mode
- Multi-language Support
- Digital Twin Integration

---

# 📜 License

MIT License

---

# 👨‍💻 Author

AI Maintenance Voice Copilot

Built using Python, FastAPI, React, SAP HANA Cloud and Large Language Models.