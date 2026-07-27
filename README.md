# ✈️ AI Maintenance Voice Copilot

> **A Voice-First AI Assistant for Aircraft Maintenance Technicians**
>
> AI Maintenance Voice Copilot is an intelligent, voice-first maintenance assistant that enables aircraft technicians to perform inspections naturally through conversation while automatically generating structured maintenance records, answering technical questions from maintenance manuals, and creating professional maintenance reports.
>
> The solution is designed to integrate with **SAP HANA Cloud** and **Azure OpenAI GPT-4.1**, providing an enterprise-ready AI copilot for modern aircraft maintenance operations.

---

# 📖 Table of Contents

- Project Vision
- Problem Statement
- Solution Overview
- Key Features
- System Architecture
- AI Agent Architecture
- Technology Stack
- Project Structure
- Application Workflow
- Environment Variables
- Installation
- Running the Application
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
- Retrieve previous maintenance history
- Automatically generate structured maintenance records
- Generate professional PDF reports
- Store maintenance history inside SAP HANA Cloud

---

# ✨ Key Features

## 🎤 Voice-First Maintenance

The technician communicates naturally.

Example:

```
Technician

Found corrosion on the turbine blade.

AI

Which aircraft are you inspecting?

Technician

VT-AAB

AI

Which engine?

Technician

Left Engine.

AI

Severity?

Technician

Moderate.
```

No forms.

No typing.

Just conversation.

---

## 🤖 Intelligent AI Conversation

The AI:

- Understands context
- Maintains conversation history
- Asks follow-up questions
- Validates information
- Confirms maintenance records

---

## 📚 Technical Knowledge Assistant

The technician can ask questions such as:

- What torque should I use?
- Show previous failures.
- When was this component replaced?
- Display maintenance history.
- Show inspection interval.

The AI retrieves answers from:

- Aircraft manuals
- Historical maintenance records
- SAP HANA Cloud
- Internal knowledge base

---

## 📝 Structured Maintenance Records

The AI automatically extracts:

- Aircraft
- Registration
- Engine
- Component
- Part Number
- Finding
- Severity
- Recommendation
- Technician
- Timestamp
- AI Summary

---

## 📄 Automatic PDF Generation

Professional maintenance reports include:

- Company Logo
- Aircraft Information
- Technician Information
- Inspection Details
- Findings
- Severity
- Recommendations
- AI Generated Summary
- Signature Section

---

## 🧠 Organisational Knowledge Capture

Every conversation becomes part of the company's maintenance knowledge base.

Stored information includes:

- Technician conversations
- Maintenance findings
- Recommendations
- Previous inspections
- Historical failures
- AI summaries

---

# 🏗️ System Architecture

```
                           React Frontend
                                  │
                  Voice + Text User Interface
                                  │
                    REST API + WebSocket Layer
                                  │
                           FastAPI Backend
                                  │
                      LangGraph Agent Workflow
                                  │
      ┌─────────────────────────────────────────────────┐
      │                                                 │
Conversation Agent                              Knowledge Agent
      │                                                 │
Extraction Agent                               Validation Agent
      │                                                 │
      └─────────────────────────────────────────────────┘
                                  │
                           Service Layer
                                  │
      ┌──────────────┬──────────────┬──────────────┐
      │              │              │
 Speech Service   Azure GPT-4.1   PDF Service
      │              │              │
      └──────────────┼──────────────┘
                     │
             SAP HANA Cloud Database
```

---

# 🤖 AI Agent Architecture

## Conversation Agent

Responsible for:

- Natural conversations
- Collecting maintenance information
- Asking follow-up questions
- Managing inspection sessions

---

## Extraction Agent

Converts natural language into structured maintenance records.

Example:

```json
{
  "aircraft": "VT-AAB",
  "engine": "Left Engine",
  "component": "Turbine Blade",
  "finding": "Corrosion",
  "severity": "Moderate"
}
```

---

## Validation Agent

Validates:

- Aircraft registration
- Component names
- Part numbers
- Severity values
- Missing fields

---

## Knowledge Agent

Retrieves:

- Aircraft manuals
- Torque specifications
- Previous maintenance history
- Inspection procedures
- Historical failures

---

## Report Agent

Responsible for:

- AI Summary
- Maintenance Report
- PDF Generation
- SAP-ready Maintenance Record

---

# 💻 Technology Stack

## Backend

- Python 3.12
- FastAPI
- SQLAlchemy
- SAP HANA Cloud
- Azure OpenAI GPT-4.1
- LangGraph
- Faster Whisper
- ReportLab
- WebSockets
- Pydantic
- python-dotenv

---

## Frontend

- React
- TypeScript
- Vite
- Material UI
- Tailwind CSS
- MediaRecorder API

---

## Database

- SAP HANA Cloud

---

## AI & Voice

- Azure OpenAI GPT-4.1
- Faster Whisper
- OpenAI Text-to-Speech (Azure-compatible implementation)
- LangGraph Multi-Agent Workflow

---

# 📂 Project Structure

```
maintenance-ai-copilot/

│
├── backend/
│   │
│   ├── api/
│   │     auth.py
│   │     conversation.py
│   │     maintenance.py
│   │     manuals.py
│   │     reports.py
│   │     websocket.py
│   │
│   ├── agents/
│   │     conversation_agent.py
│   │     extraction_agent.py
│   │     validation_agent.py
│   │     knowledge_agent.py
│   │     report_agent.py
│   │
│   ├── models/
│   │     aircraft.py
│   │     maintenance.py
│   │     technician.py
│   │
│   ├── services/
│   │     hana_service.py
│   │     llm_service.py
│   │     speech_to_text.py
│   │     text_to_speech.py
│   │     pdf_service.py
│   │     vector_service.py
│   │     conversation_memory.py
│   │
│   ├── utils/
│   │
│   ├── app.py
│   ├── config.py
│   ├── database.py
│   └── dependencies.py
│
├── frontend/
│
├── manuals/
├── uploads/
├── generated_reports/
├── logs/
│
├── requirements.txt
├── .env
├── .env.example
└── README.md
```

---

# 🔄 Application Workflow

```
Technician Starts Inspection

            │

            ▼

🎤 Voice Recording

            │

            ▼

Speech-to-Text (Whisper)

            │

            ▼

Conversation Agent

            │

            ▼

Information Extraction

            │

            ▼

Validation

            │

            ▼

Knowledge Retrieval

            │

            ▼

Follow-up Questions

            │

            ▼

Structured Maintenance Record

            │

            ▼

Generate PDF

            │

            ▼

Store Record in SAP HANA Cloud
```

---

# 🔌 Backend Services

| Service | Responsibility |
|----------|----------------|
| Speech Service | Converts speech to text |
| Text-to-Speech Service | Generates spoken AI responses |
| LLM Service | Azure OpenAI communication |
| HANA Service | SAP HANA Cloud operations |
| Vector Service | Manual & document retrieval |
| PDF Service | Maintenance report generation |
| Conversation Memory | Session memory management |

---

# 🌐 API Endpoints

| Endpoint | Description |
|----------|-------------|
| `/conversation` | Voice conversation |
| `/maintenance` | Maintenance records |
| `/manuals` | Manual search |
| `/reports` | PDF reports |
| `/auth` | Authentication |
| `/ws` | Live WebSocket communication |

---

# ⚙️ Environment Variables

Create a `.env` file in the project root.

```env
# SAP HANA Cloud

HANA_HOST=
HANA_PORT=443
HANA_USER=
HANA_PASSWORD=
HANA_SCHEMA=
HANA_ENCRYPT=true

# Azure OpenAI

AZURE_OPENAI_URL=
AZURE_API_KEY=

# Security

SECRET_KEY=
```

> **Important:** Never commit your `.env` file to GitHub. Add it to `.gitignore` and commit only `.env.example`.

---

# ▶️ Installation

Clone the repository:

```bash
git clone <repository-url>
cd maintenance-ai-copilot
```

Install backend dependencies:

```bash
pip install -r requirements.txt
```

Install frontend dependencies:

```bash
cd frontend
npm install
```

---

# ▶️ Running the Backend

```bash
cd backend

uvicorn app:app --reload
```

Backend URL:

```
http://localhost:8000
```

Swagger API Documentation:

```
http://localhost:8000/docs
```

---

# ▶️ Running the Frontend

```bash
cd frontend

npm run dev
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
- FastAPI
- SAP HANA Cloud
- Azure OpenAI GPT-4.1
- LangGraph
- React
- TypeScript
- Faster Whisper
- ReportLab