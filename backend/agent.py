"""
==============================================================
AI Maintenance Voice Copilot
AI Reasoning Layer (Agent)
--------------------------------------------------------------

Purpose
-------
This module implements the conversational "co-pilot" that talks
to the technician. It is powered by Azure OpenAI (GPT-4.1) and
uses function/tool calling to:

    • Ask contextual follow-up questions until a maintenance
      finding is complete (aircraft, component, finding,
      severity, location, recommended action, technician).
    • Answer maintenance knowledge questions (torque specs,
      inspection intervals, procedures, ...) by retrieving
      relevant passages from ingested manuals via semantic
      search over SAP HANA Cloud.
    • Persist every conversation turn and every structured
      maintenance record it creates/updates back to the
      database, so nothing spoken by the technician is lost.

Responsibilities
----------------
• Maintain per-session conversation state
• Define the tool schema exposed to the model
• Execute tool calls against backend.database
• Drive the tool-calling loop until a final natural-language
  reply is ready to be spoken back to the technician

IMPORTANT
---------
This module never reads environment variables directly.
All settings come from backend.config.

Example
-------
    from backend.agent import MaintenanceAgent

    agent = MaintenanceAgent(technician="J. Smith")
    reply = agent.send("Found corrosion on the left turbine blade.")
    print(reply)
==============================================================
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

from openai import AzureOpenAI

from backend.config import (
    AZURE_OPENAI_URL,
    AZURE_API_KEY,
    AZURE_API_VERSION,
    AZURE_CHAT_MODEL,
    AZURE_EMBEDDING_MODEL,
    TOP_K_RESULTS,
    LOG_LEVEL,
)
from backend.database import (
    semantic_search,
    insert_maintenance_record,
    update_maintenance_record,
    get_maintenance_record,
    insert_conversation_message,
)

logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger("mro_copilot.agent")

# ==========================================================
# System Prompt
# ==========================================================

SYSTEM_PROMPT = """\
You are the AI Maintenance Voice Copilot: a hands-free assistant that helps
aircraft maintenance technicians document inspections while they work.

Your job:
1. Listen to the technician's spoken observations and identify maintenance
   findings (damage, wear, corrosion, leaks, faults, etc.).
2. A complete maintenance record needs: aircraft registration, component,
   finding (description), severity, location on the aircraft/component,
   recommended action, and the technician's name. Ask ONE short, natural
   follow-up question at a time for whatever is still missing. Do not ask
   for information the technician has already given you.
3. Whenever you learn or confirm a new piece of information about the
   current finding, call the `create_or_update_maintenance_record` tool
   immediately so nothing is lost, even if the record is not complete yet.
4. If the technician asks a knowledge question (torque values, inspection
   intervals, part numbers, procedures, prior repair history, troubleshooting),
   call `search_maintenance_knowledge` to look it up in the ingested aircraft
   manuals before answering. Only answer from what the search returns -
   if nothing relevant is found, say so honestly instead of guessing.
5. Keep replies short, spoken-style, and technical-but-plain - this is read
   aloud to someone wearing gloves and possibly holding a tool, not read on
   a screen.
6. Once every required field is captured, confirm the finding back to the
   technician in one sentence and let them know the record has been saved.

Never fabricate torque specs, part numbers, or procedures - only state facts
that came from a `search_maintenance_knowledge` result.
"""

REQUIRED_FIELDS = [
    "aircraft_reg",
    "component",
    "finding",
    "severity",
    "location",
    "recommended_action",
    "technician",
]

# ==========================================================
# Tool Schema (OpenAI / Azure OpenAI function-calling format)
# ==========================================================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_maintenance_knowledge",
            "description": (
                "Search the ingested aircraft manuals (AMM, IPC, CMM, etc.) "
                "for information relevant to a technician's question - torque "
                "specs, inspection intervals, part numbers, procedures, "
                "troubleshooting steps, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The technician's question, or a short search query capturing what they need to know.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "How many manual passages to retrieve.",
                        "default": TOP_K_RESULTS,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_or_update_maintenance_record",
            "description": (
                "Create the maintenance record for the current finding if one "
                "doesn't exist yet, or update it with any newly captured field. "
                "Call this every time you learn something new, even partial "
                "information - do not wait until the record is complete."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "aircraft_reg": {"type": "string", "description": "Aircraft registration, e.g. VT-ABC."},
                    "component": {"type": "string", "description": "The component or system affected."},
                    "finding": {"type": "string", "description": "Description of the defect/observation."},
                    "severity": {"type": "string", "description": "e.g. Minor, Major, Critical, AOG."},
                    "location": {"type": "string", "description": "Where on the aircraft/component."},
                    "recommended_action": {"type": "string", "description": "What should be done about it."},
                    "technician": {"type": "string", "description": "Name of the technician reporting."},
                    "status": {
                        "type": "string",
                        "description": "Record status.",
                        "enum": ["OPEN", "COMPLETE", "CLOSED"],
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_record",
            "description": "Retrieve the current state of the maintenance record being built in this conversation.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


# ==========================================================
# Agent
# ==========================================================

class MaintenanceAgent:
    """
    One instance = one ongoing conversation / inspection session
    with a single technician.

    Create a new instance per voice session (e.g. keyed by a
    session id in your Flask app), and keep calling `.send()`
    with each new transcribed technician utterance.
    """

    def __init__(
        self,
        technician: Optional[str] = None,
        session_id: Optional[str] = None,
        record_id: Optional[str] = None,
    ):
        self.session_id = session_id or str(uuid.uuid4())
        self.technician = technician
        self.record_id = record_id

        self.client = AzureOpenAI(
            azure_endpoint=AZURE_OPENAI_URL,
            api_key=AZURE_API_KEY,
            api_version=AZURE_API_VERSION,
        )

        self.messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

        if technician:
            self.messages.append(
                {"role": "system", "content": f"The technician's name is {technician}."}
            )

    # ------------------------------------------------------
    # Public API
    # ------------------------------------------------------

    def send(self, technician_utterance: str, max_tool_iterations: int = 5) -> str:
        """
        Send one technician utterance to the agent and return the
        assistant's natural-language reply (ready for text-to-speech).
        """
        insert_conversation_message("technician", technician_utterance, self.record_id)
        self.messages.append({"role": "user", "content": technician_utterance})

        for _ in range(max_tool_iterations):
            response = self.client.chat.completions.create(
                model=AZURE_CHAT_MODEL,
                messages=self.messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.2,
            )

            choice = response.choices[0]
            message = choice.message

            if not message.tool_calls:
                reply = message.content or ""
                self.messages.append({"role": "assistant", "content": reply})
                insert_conversation_message("assistant", reply, self.record_id)
                return reply

            # The model wants to call one or more tools first.
            self.messages.append(
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in message.tool_calls
                    ],
                }
            )

            for tool_call in message.tool_calls:
                result = self._execute_tool(tool_call.function.name, tool_call.function.arguments)
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result, default=str),
                    }
                )

        # Safety valve: if the model keeps calling tools without
        # ever producing a final answer, surface something sane.
        logger.warning("Max tool iterations reached for session %s", self.session_id)
        fallback = "I've saved what you've told me so far - could you repeat or clarify your last point?"
        insert_conversation_message("assistant", fallback, self.record_id)
        return fallback

    def is_record_complete(self) -> bool:
        """Check whether every required field has been captured."""
        if not self.record_id:
            return False
        record = get_maintenance_record(self.record_id)
        if not record:
            return False
        return all(record.get(field.upper()) for field in REQUIRED_FIELDS)

    # ------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------

    def _execute_tool(self, name: str, raw_arguments: str) -> dict[str, Any]:
        try:
            arguments = json.loads(raw_arguments) if raw_arguments else {}
        except json.JSONDecodeError:
            logger.warning("Could not parse tool arguments for %s: %r", name, raw_arguments)
            arguments = {}

        logger.info("Executing tool '%s' with args: %s", name, arguments)

        if name == "search_maintenance_knowledge":
            return self._tool_search_maintenance_knowledge(arguments)
        if name == "create_or_update_maintenance_record":
            return self._tool_create_or_update_record(arguments)
        if name == "get_current_record":
            return self._tool_get_current_record()

        return {"error": f"Unknown tool '{name}'"}

    def _tool_search_maintenance_knowledge(self, arguments: dict) -> dict:
        query = arguments.get("query", "")
        top_k = int(arguments.get("top_k", TOP_K_RESULTS))

        if not query:
            return {"error": "No query provided"}

        embedding_response = self.client.embeddings.create(
            model=AZURE_EMBEDDING_MODEL,
            input=[query],
        )
        query_embedding = embedding_response.data[0].embedding

        results = semantic_search(query_embedding, top_k=top_k)
        return {
            "results": [
                {
                    "source": r["FILE_NAME"],
                    "content": r["CONTENT"],
                    "relevance_score": round(float(r["SCORE"]), 4),
                }
                for r in results
            ]
        }

    def _tool_create_or_update_record(self, arguments: dict) -> dict:
        if arguments.get("technician") is None and self.technician:
            arguments["technician"] = self.technician

        if not self.record_id:
            self.record_id = insert_maintenance_record(**arguments)
            return {"record_id": self.record_id, "created": True}

        update_maintenance_record(self.record_id, **arguments)
        return {"record_id": self.record_id, "created": False, "updated_fields": list(arguments.keys())}

    def _tool_get_current_record(self) -> dict:
        if not self.record_id:
            return {"record": None}
        return {"record": get_maintenance_record(self.record_id)}
