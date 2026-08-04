"""
==============================================================
AI Maintenance Voice Copilot
AI Reasoning Layer (Agent)
--------------------------------------------------------------

Purpose
-------
This module implements the conversational "co-pilot" that talks
to the technician. It is powered by a chat model deployed in SAP
AI Core (gpt-4.1 by default), reached through the Generative AI
Hub proxy, and uses function/tool calling to:

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
All settings come from backend.config. Embeddings come from
backend.embeddings - the agent does not call a model provider
itself for anything except chat completion.

Example
-------
    from backend.agent import MaintenanceAgent

    agent = MaintenanceAgent(technician="J. Smith", user_id="...")
    reply = agent.send("Found corrosion on the left turbine blade.")
    print(reply)
==============================================================
"""

from __future__ import annotations

import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from backend.config import (
    AICORE_CHAT_MODEL,
    AICORE_CHAT_DEPLOYMENT_ID,
    TOP_K_RESULTS,
    MIN_RELEVANCE_SCORE,
    LOG_LEVEL,
)
from backend.embeddings import embed_query
from backend.database import (
    semantic_search,
    insert_maintenance_record,
    update_maintenance_record,
    get_maintenance_record,
    insert_conversation_message,
)

logger = logging.getLogger("mro_copilot.agent")
logger.setLevel(LOG_LEVEL)

# ==========================================================
# Off-thread conversation logging
# --------------------------------------------------------
# Every turn writes the technician's utterance to CONVERSATIONS
# before the model is called, and the reply after. Each write is a
# round trip to HANA Cloud (~0.5s from here), and nothing in the
# turn depends on the result - so waiting for them just delayed the
# reply the technician is standing there waiting to hear.
#
# They are handed to a single background worker instead. One worker,
# not a pool: submissions stay FIFO, so the transcript keeps its
# order. Failures are logged rather than raised, since losing an
# audit row must not break a live inspection.
# ==========================================================

_log_writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="convlog")


def _log_message_async(role: str, message: str, record_id: Optional[str]) -> None:
    """Persist one conversation turn without blocking the caller."""

    def _report(future):
        exc = future.exception()
        if exc:
            logger.warning("Could not persist a %s turn: %s", role, exc)

    _log_writer.submit(
        insert_conversation_message, role, message, record_id
    ).add_done_callback(_report)

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
   if nothing relevant is found, say so honestly instead of guessing, and say
   what would be needed to answer it.
5. Cite the source of any manual-derived fact as [file, p.N]. Quote exact
   figures, torque values, part numbers and step numbers verbatim rather
   than paraphrasing them.
6. Keep replies short, spoken-style, and technical-but-plain - this is read
   aloud to someone wearing gloves and possibly holding a tool, not read on
   a screen. Two or three sentences is usually right.
7. Once every required field is captured, confirm the finding back to the
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
# Tool Schema (OpenAI-compatible function-calling format)
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
                        "description": (
                            "The technician's question, or a short search query "
                            "capturing what they need to know."
                        ),
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
            "description": (
                "Retrieve the current state of the maintenance record being "
                "built in this conversation."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


# ==========================================================
# Chat completion through the AI Core proxy
# ==========================================================

def _chat_completion(messages: list[dict], tools: Optional[list] = None, temperature: float = 0.2):
    from gen_ai_hub.proxy.native.openai import chat

    kwargs: dict = {"messages": messages, "temperature": temperature}

    if AICORE_CHAT_DEPLOYMENT_ID:
        kwargs["deployment_id"] = AICORE_CHAT_DEPLOYMENT_ID
    else:
        kwargs["model_name"] = AICORE_CHAT_MODEL

    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    return chat.completions.create(**kwargs)


def _chat_completion_stream(messages: list[dict], tools: Optional[list] = None, temperature: float = 0.2):
    """Same call as _chat_completion, but returns a token-by-token stream."""
    from gen_ai_hub.proxy.native.openai import chat

    kwargs: dict = {"messages": messages, "temperature": temperature, "stream": True}

    if AICORE_CHAT_DEPLOYMENT_ID:
        kwargs["deployment_id"] = AICORE_CHAT_DEPLOYMENT_ID
    else:
        kwargs["model_name"] = AICORE_CHAT_MODEL

    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    return chat.completions.create(**kwargs)


# ==========================================================
# Shared tool: manual knowledge search
# --------------------------------------------------------
# Module level because both conversational surfaces need it - the
# technician's MaintenanceAgent and the supervisor's assistant in
# backend.assistant - and neither should own the other's copy.
# ==========================================================

def search_maintenance_knowledge(arguments: dict) -> dict:
    """
    Semantic search over the ingested manuals.

    Returns either {"results": [...]} or {"results": [], "note": ...}
    where the note tells the model to admit the manuals do not cover
    the question rather than answering from general knowledge.
    """
    query = arguments.get("query", "")
    top_k = int(arguments.get("top_k", TOP_K_RESULTS))

    if not query:
        return {"error": "No query provided"}

    try:
        query_embedding = embed_query(query)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Embedding the search query failed")
        return {"error": f"Manual search is unavailable right now: {exc}"}

    results = semantic_search(
        query_embedding,
        top_k=top_k,
        min_score=MIN_RELEVANCE_SCORE,
    )

    if not results:
        return {
            "results": [],
            "note": (
                "No passage in the ingested manuals is relevant to this "
                "question. Say the manuals do not cover it rather than "
                "answering from general knowledge."
            ),
        }

    return {
        "results": [
            {
                "source": r["FILE_NAME"],
                "page": r.get("PAGE_NUMBER"),
                "content": r["CONTENT"],
                "relevance_score": round(float(r["SCORE"]), 4),
            }
            for r in results
        ]
    }


# ==========================================================
# Agent
# ==========================================================

class MaintenanceAgent:
    """
    One instance = one ongoing conversation / inspection session
    with a single technician.

    Create a new instance per voice session (keyed by a session id
    in the Flask app), and keep calling `.send()` with each new
    transcribed technician utterance.
    """

    def __init__(
        self,
        technician: Optional[str] = None,
        session_id: Optional[str] = None,
        record_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ):
        self.session_id = session_id or str(uuid.uuid4())
        self.technician = technician
        self.record_id = record_id
        self.user_id = user_id

        self.messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

        if technician:
            self.messages.append(
                {
                    "role": "system",
                    "content": (
                        f"The technician's name is {technician}. It is already "
                        "known - never ask for it, and pass it as the "
                        "`technician` field when saving the record."
                    ),
                }
            )

    # ------------------------------------------------------
    # Public API
    # ------------------------------------------------------

    def send(self, technician_utterance: str, max_tool_iterations: int = 5) -> str:
        """
        Send one technician utterance to the agent and return the
        assistant's natural-language reply (ready for text-to-speech).
        """
        _log_message_async("technician", technician_utterance, self.record_id)
        self.messages.append({"role": "user", "content": technician_utterance})

        for _ in range(max_tool_iterations):
            response = _chat_completion(self.messages, tools=TOOLS)

            message = response.choices[0].message

            if not message.tool_calls:
                reply = message.content or ""
                self.messages.append({"role": "assistant", "content": reply})
                _log_message_async("assistant", reply, self.record_id)
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
                result = self._execute_tool(
                    tool_call.function.name, tool_call.function.arguments
                )
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
        fallback = (
            "I've saved what you've told me so far - could you repeat or "
            "clarify your last point?"
        )
        insert_conversation_message("assistant", fallback, self.record_id)
        return fallback

    def send_stream(self, technician_utterance: str, max_tool_iterations: int = 5):
        """
        Same conversation turn as send(), but yields the reply as it is
        generated instead of waiting for the whole thing:

            {"type": "content", "text": "..."}   one per token/chunk of the
                                                  final reply, in order
            {"type": "done", "reply": "..."}      once, with the full text

        Tool-calling turns (the model deciding to search the manuals or
        save a record) are never streamed to the caller - only the final
        natural-language reply is, exactly like send() only returns that.
        This lets the caller (the Flask route) forward each piece to
        text-to-speech and to the browser as soon as it exists, instead
        of after the entire reply is ready.
        """
        _log_message_async("technician", technician_utterance, self.record_id)
        self.messages.append({"role": "user", "content": technician_utterance})

        for _ in range(max_tool_iterations):
            stream = _chat_completion_stream(self.messages, tools=TOOLS)

            content_parts: list[str] = []
            tool_calls_acc: dict[int, dict] = {}

            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                if getattr(delta, "tool_calls", None):
                    for tc in delta.tool_calls:
                        slot = tool_calls_acc.setdefault(
                            tc.index, {"id": None, "name": None, "arguments": ""}
                        )
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function and tc.function.name:
                            slot["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            slot["arguments"] += tc.function.arguments
                    continue

                if getattr(delta, "content", None):
                    content_parts.append(delta.content)
                    yield {"type": "content", "text": delta.content}

            if tool_calls_acc:
                ordered = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
                self.messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {"name": tc["name"], "arguments": tc["arguments"]},
                            }
                            for tc in ordered
                        ],
                    }
                )
                for tc in ordered:
                    result = self._execute_tool(tc["name"], tc["arguments"])
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": json.dumps(result, default=str),
                        }
                    )
                continue  # the next iteration should produce the final reply

            reply = "".join(content_parts)
            self.messages.append({"role": "assistant", "content": reply})
            _log_message_async("assistant", reply, self.record_id)
            yield {"type": "done", "reply": reply}
            return

        logger.warning("Max tool iterations reached for session %s", self.session_id)
        fallback = (
            "I've saved what you've told me so far - could you repeat or "
            "clarify your last point?"
        )
        insert_conversation_message("assistant", fallback, self.record_id)
        yield {"type": "done", "reply": fallback}

    @staticmethod
    def record_is_complete(record: Optional[dict]) -> bool:
        """
        Whether an already-fetched record has every required field.

        Split out from is_record_complete() so a caller that has just
        read the record can judge completeness without paying for a
        second round trip to HANA for the same row.
        """
        if not record:
            return False
        return all(record.get(field.upper()) for field in REQUIRED_FIELDS)

    def is_record_complete(self) -> bool:
        """Check whether every required field has been captured."""
        if not self.record_id:
            return False
        return self.record_is_complete(get_maintenance_record(self.record_id))

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
        return search_maintenance_knowledge(arguments)

    def _tool_create_or_update_record(self, arguments: dict) -> dict:
        if arguments.get("technician") is None and self.technician:
            arguments["technician"] = self.technician

        if not self.record_id:
            self.record_id = insert_maintenance_record(
                technician_user_id=self.user_id, **arguments
            )
            return {"record_id": self.record_id, "created": True}

        update_maintenance_record(self.record_id, **arguments)
        return {
            "record_id": self.record_id,
            "created": False,
            "updated_fields": list(arguments.keys()),
        }

    def _tool_get_current_record(self) -> dict:
        if not self.record_id:
            return {"record": None}
        return {"record": get_maintenance_record(self.record_id)}