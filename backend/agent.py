"""
==============================================================
AI Maintenance Voice Assistant
AI Reasoning Layer (Agent)
--------------------------------------------------------------

Purpose
-------
This module implements the conversational assistant that talks
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
    get_conversation,
    list_record_photos,
    record_photos_available,
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
You are the AI Maintenance Voice Assistant: a hands-free assistant that helps
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
7. Severity is rarely stated as a level. Infer it from how the technician
   describes what they are looking at, pick the closest of Minor, Moderate,
   Major, Critical, AOG, and name the level you chose when you confirm the
   finding ("logging that as Major") so they can correct you in one word.
   Never invent a level outside that list, and never leave severity blank
   just because they didn't use the word - but if what they said genuinely
   doesn't imply a level, ask rather than guessing.
8. Once every required field is captured, tell the technician this finding
   is now complete. If `photo_count` (from the record tool's result, or
   from `get_current_record`) is 0, mention in the same breath that no
   photo is attached and ask if they'd like to add one. A photo can still
   be attached after the finding completes - it only becomes impossible
   once a supervisor posts the record to SAP - so do not tell them it is
   their last chance.
9. If the technician indicates they're done with this finding and want to
   log a separate one (e.g. "start a new record", "log another finding",
   "that's a separate issue", "next inspection"), and every field is already
   captured, confirm once before calling `start_new_finding` - and if
   `photo_count` is 0, fold the missing photo into that same question, e.g.
   "This finding's complete, no photo on it - want to add one before I start
   the next inspection?" - then only call the tool once they say to move on.
   If some fields are still missing, no confirmation is needed; just call
   `start_new_finding`. If it reports back `missing_fields`, ask for those
   specific fields first (aircraft registration, finding, component,
   location are mandatory before moving on) rather than calling it again
   immediately.
10. If a record is already preloaded when the conversation begins (resuming
   an existing open finding), call `get_current_record` first, greet the
   technician, and ask only about whatever is still missing - never re-ask
   for fields already captured.

A finding completes itself the moment every field is filled - you never set
this yourself, it happens automatically. Completing it is not a lock: a photo
can still be attached to it and a supervisor can still correct any field.
Only being posted to SAP makes a record immutable. A supervisor's role is
different: they can complete a finding that's missing fields by filling the
gaps themselves, which you have no part in.

Never fabricate torque specs, part numbers, or procedures - only state facts
that came from a `search_maintenance_knowledge` result.
"""

# The severity vocabulary, in ascending order. Ordered so "worse than" and
# "one step up from" comparisons stay meaningful, and shared with the
# supervisor's edit dropdown through /api/records/filters so the two cannot
# drift apart. "Moderate" is in the list because the model was already
# recording it, and a level people reach for naturally is better admitted
# than silently rewritten.
SEVERITY_LEVELS = ["Minor", "Moderate", "Major", "Critical", "AOG"]

# How many of a finding's earlier turns to replay into the model's context
# when a technician reopens it. Enough to remember where the conversation
# actually was; short enough not to crowd the window on a long finding.
HISTORY_TURNS_ON_RESUME = 12

# Spoken names for the record's fields, for the resume greeting - "recommended
# action" reads better in a question than "recommended_action".
FIELD_LABELS = {
    "aircraft_reg": "aircraft registration",
    "component": "component",
    "finding": "the finding itself",
    "severity": "severity",
    "location": "location",
    "recommended_action": "recommended action",
    "technician": "technician",
}

REQUIRED_FIELDS = [
    "aircraft_reg",
    "component",
    "finding",
    "severity",
    "location",
    "recommended_action",
    "technician",
]

# Mandatory before a technician can move on to a new finding (rule: aircraft
# reg / finding / component / location must be captured; severity and
# recommended action may be left for the supervisor).
TECHNICIAN_REQUIRED_FIELDS = ["aircraft_reg", "finding", "component", "location"]

# Mandatory before a supervisor can mark a record COMPLETE. Recommended
# action stays optional even at completion.
SUPERVISOR_REQUIRED_FIELDS = TECHNICIAN_REQUIRED_FIELDS + ["severity"]


def missing_fields(record: Optional[dict], required: list[str]) -> list[str]:
    """Which of `required` (lowercase field names) are unfilled on `record`."""
    record = record or {}
    return [field for field in required if not record.get(field.upper())]

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
                    "severity": {
                        "type": "string",
                        "enum": SEVERITY_LEVELS,
                        "description": (
                            "How serious the finding is. Technicians rarely say a "
                            "level by name - map what they actually said onto one "
                            "of these: 'hairline', 'surface', 'keep an eye on it' "
                            "-> Minor; 'worn but serviceable', 'wants doing at the "
                            "next check' -> Moderate; 'needs fixing before it "
                            "flies again' -> Major; 'deep crack', 'structural', "
                            "'don't sign it off' -> Critical; 'grounded', "
                            "'aircraft on ground', 'unairworthy' -> AOG. Say which "
                            "level you picked when you confirm the finding, so the "
                            "technician can correct you."
                        ),
                    },
                    "location": {"type": "string", "description": "Where on the aircraft/component."},
                    "recommended_action": {"type": "string", "description": "What should be done about it."},
                    "technician": {"type": "string", "description": "Name of the technician reporting."},
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
    {
        "type": "function",
        "function": {
            "name": "start_new_finding",
            "description": (
                "Stop adding to the current finding and start a fresh one, "
                "because the technician wants to log a separate, unrelated "
                "finding. Fails if the current finding is still missing a "
                "mandatory field (aircraft registration, finding, component, "
                "or location) - in that case ask for the missing field(s) "
                "instead of calling this again."
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

        if record_id:
            self._restore_history(record_id)

    def _restore_history(self, record_id: str) -> None:
        """
        Replay this finding's earlier turns into the message list, so a
        technician who comes back to an open finding is genuinely continuing
        the same conversation rather than starting a blank one that happens
        to have some fields pre-filled.

        Only the tail is replayed: the whole transcript of a long finding
        would crowd the context for no benefit, and what matters is where
        they left off. Tool calls are not replayed - the record itself is
        the result of those, and it is read fresh at the start of the turn.
        """
        try:
            turns = get_conversation(record_id)
        except Exception as exc:  # noqa: BLE001 - history is a nicety, not a gate
            logger.warning("Could not restore history for record %s: %s", record_id, exc)
            return

        if not turns:
            return

        for turn in turns[-HISTORY_TURNS_ON_RESUME:]:
            role = "user" if turn.get("ROLE") == "technician" else "assistant"
            message = (turn.get("MESSAGE") or "").strip()
            if message:
                self.messages.append({"role": role, "content": message})

        logger.info(
            "Restored %d earlier turn(s) for resumed record %s",
            min(len(turns), HISTORY_TURNS_ON_RESUME), record_id,
        )

    def _resume_instruction(self) -> str:
        """
        The nudge that produces the opening turn when a finding is reopened.

        Built from the record as it stands right now rather than left to the
        model to look up, so the greeting costs one round trip instead of a
        tool call and a second one.
        """
        record = get_maintenance_record(self.record_id) if self.record_id else None
        missing = missing_fields(record, REQUIRED_FIELDS)

        photo_count = 0
        if self.record_id and record_photos_available():
            try:
                photo_count = len(list_record_photos(self.record_id))
            except Exception:  # noqa: BLE001
                photo_count = 0

        summary = ", ".join(
            f"{label}: {record.get(field.upper())}"
            for field, label in FIELD_LABELS.items()
            if record and record.get(field.upper())
        ) or "nothing captured yet"

        parts = [
            "The technician has just reopened this finding to carry on with it. "
            "This is your opening turn - they have not said anything yet.",
            f"Captured so far - {summary}.",
        ]

        if missing:
            wanted = ", ".join(FIELD_LABELS.get(f, f) for f in missing)
            parts.append(
                f"Still missing: {wanted}. Greet them in one short clause, remind "
                "them which finding this is, then ask for ONE of the missing items "
                "- the first in that list. Never re-ask for something already "
                "captured."
            )
        else:
            parts.append(
                "Every field is captured. Greet them, say the finding is complete, "
                "and ask whether they want to add anything else to it."
            )

        if photo_count == 0:
            parts.append(
                "No photo is attached. If nothing else is missing, offer to add "
                "one now; otherwise leave the photo until the fields are done."
            )
        else:
            parts.append(f"{photo_count} photo(s) already attached - do not ask for one.")

        parts.append(
            "Reply with speech only. Do not call any tool on this turn - nothing "
            "new has been said yet."
        )

        return " ".join(parts)

    # ------------------------------------------------------
    # Public API
    # ------------------------------------------------------

    def send(self, technician_utterance: str, max_tool_iterations: int = 5) -> str:
        """
        Send one technician utterance to the agent and return the
        assistant's natural-language reply (ready for text-to-speech).
        """
        # Logged once the turn is over, not here - a turn's first utterance
        # often *creates* the record (self.record_id is still None right
        # now), and logging immediately would permanently orphan it under
        # RECORD_ID NULL, invisible to anyone pulling up that record's
        # transcript later. By the time the turn ends, self.record_id
        # reflects whatever this turn actually did.
        self.messages.append({"role": "user", "content": technician_utterance})

        for _ in range(max_tool_iterations):
            response = _chat_completion(self.messages, tools=TOOLS)

            message = response.choices[0].message

            if not message.tool_calls:
                reply = message.content or ""
                self.messages.append({"role": "assistant", "content": reply})
                _log_message_async("technician", technician_utterance, self.record_id)
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
        insert_conversation_message("technician", technician_utterance, self.record_id)
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
        # See send() for why this isn't logged until the turn ends - the
        # first utterance in a finding can create the record mid-turn.
        self.messages.append({"role": "user", "content": technician_utterance})
        yield from self._stream_reply(technician_utterance, max_tool_iterations)

    def resume_stream(self, max_tool_iterations: int = 5):
        """
        The assistant's opening turn when a technician reopens a finding.

        Same event shape as send_stream(), but driven by the record's own
        state rather than by something the technician said - so it can pick
        the conversation up ("you had VT-ABC's flap track down as a crack -
        what recommended action do you want on it?") before they have to
        remember where they got to. Only the reply is written to the
        transcript; there is no technician turn to log.
        """
        self.messages.append({"role": "system", "content": self._resume_instruction()})
        yield from self._stream_reply(None, max_tool_iterations)

    def _stream_reply(self, technician_utterance: Optional[str], max_tool_iterations: int):
        """
        The streaming turn itself, shared by send_stream() and
        resume_stream(). `technician_utterance` is None when nothing was
        said - the turn is then logged as an assistant message only.
        """
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
            if technician_utterance is not None:
                _log_message_async("technician", technician_utterance, self.record_id)
            _log_message_async("assistant", reply, self.record_id)
            yield {"type": "done", "reply": reply}
            return

        logger.warning("Max tool iterations reached for session %s", self.session_id)
        fallback = (
            "I've saved what you've told me so far - could you repeat or "
            "clarify your last point?"
        )
        if technician_utterance is not None:
            insert_conversation_message("technician", technician_utterance, self.record_id)
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
        if name == "start_new_finding":
            return self.start_new_finding()

        return {"error": f"Unknown tool '{name}'"}

    def _tool_search_maintenance_knowledge(self, arguments: dict) -> dict:
        return search_maintenance_knowledge(arguments)

    def _tool_create_or_update_record(self, arguments: dict) -> dict:
        # Status is never technician-*chosen* - it's promoted automatically
        # below once every field is in, or later by a supervisor filling
        # gaps the technician left. Neither the LLM nor the SAP posting
        # flow may set it directly through this tool.
        arguments.pop("status", None)

        if arguments.get("technician") is None and self.technician:
            arguments["technician"] = self.technician

        if not self.record_id:
            self.record_id = insert_maintenance_record(
                technician_user_id=self.user_id, **arguments
            )
            created = True
        else:
            update_maintenance_record(self.record_id, **arguments)
            created = False

        # A finding the technician has fully described - all seven fields,
        # including the ones they weren't required to give - needs no
        # supervisor gap-filling, so it completes itself right away rather
        # than waiting on a review step that has nothing left to add.
        record = get_maintenance_record(self.record_id)
        just_completed = False
        if record and record.get("STATUS") == "OPEN" and self.record_is_complete(record):
            update_maintenance_record(self.record_id, status="COMPLETE")
            just_completed = True

        result = {
            "record_id": self.record_id,
            "created": created,
            **({} if created else {"updated_fields": list(arguments.keys())}),
        }
        if just_completed:
            result["status"] = "COMPLETE"
            result["photo_count"] = (
                len(list_record_photos(self.record_id)) if record_photos_available() else 0
            )
        return result

    def _tool_get_current_record(self) -> dict:
        if not self.record_id:
            return {"record": None}
        photo_count = (
            len(list_record_photos(self.record_id)) if record_photos_available() else 0
        )
        return {
            "record": get_maintenance_record(self.record_id),
            "photo_count": photo_count,
        }

    def start_new_finding(self) -> dict:
        """
        Detach from the current record so the next
        create_or_update_maintenance_record call starts a fresh one.

        Refuses while the current finding is missing a technician-mandatory
        field, so a finding can never be abandoned half-identified.
        """
        if self.record_id:
            record = get_maintenance_record(self.record_id)
            missing = missing_fields(record, TECHNICIAN_REQUIRED_FIELDS)
            if missing:
                return {"started": False, "missing_fields": missing}

        self.record_id = None
        return {"started": True}