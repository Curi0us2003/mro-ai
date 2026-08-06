"""
==============================================================
AI Maintenance Voice Assistant
Supervisor Assistant
--------------------------------------------------------------

Purpose
-------
The chat surface a supervisor talks to while reviewing findings.
Two jobs:

  1. Answer questions about the record they are currently looking
     at ("is this severity reasonable?", "what does the manual say
     about this actuator?"). The open record is injected as context
     each turn, so "this" and "it" resolve without the supervisor
     having to restate which finding they mean.

  2. Answer general aircraft-maintenance questions from the
     ingested manuals when no record is open.

How this differs from backend.agent.MaintenanceAgent
----------------------------------------------------
MaintenanceAgent *writes*: it interviews a technician and creates
and updates maintenance records. This assistant is strictly
read-only. A supervisor reviewing evidence must not be able to
edit the evidence through a chat box - even by accident, even by
asking nicely - so no mutating tool is exposed here at all. The
model cannot alter a record because it has no means to.

It can, however, query across records ("how many open majors on
VT-ABC?"), which is exactly the oversight question a supervisor
has and which the read-only listing already supports.

IMPORTANT
---------
This module never reads environment variables directly.
All settings come from backend.config.
==============================================================
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from backend.config import LOG_LEVEL, TOP_K_RESULTS
from backend.agent import (
    _chat_completion_stream,
    search_maintenance_knowledge,
)
from backend.database import (
    list_maintenance_records,
    get_maintenance_record,
    list_record_photos,
)

logger = logging.getLogger("mro_copilot.assistant")
logger.setLevel(LOG_LEVEL)


SUPERVISOR_SYSTEM_PROMPT = """\
You are the maintenance assistant assisting a MAINTENANCE SUPERVISOR who is
reviewing findings logged by technicians.

Who you are talking to:
A supervisor reviewing work, not a technician logging it. They want to judge
whether a finding is complete, correctly classified, and correctly actioned -
and to look things up in the manuals to check a technician's call.

Your job:
1. When a finding is open in front of them, answer about THAT finding. Its
   full contents are given to you below. "This", "it" and "the finding" all
   refer to it - never ask them which record they mean when one is open.
2. For anything technical - torque values, inspection intervals, part numbers,
   procedures, allowable damage limits - call `search_maintenance_knowledge`
   and answer only from what comes back. Cite as [file, p.N] and quote figures
   verbatim. If the manuals do not cover it, say so plainly and say what would
   be needed; never fill the gap from general knowledge.
3. For questions spanning findings ("how many open majors", "anything else on
   this aircraft", "what has this technician logged"), call
   `search_maintenance_records` and answer from the rows it returns.
4. You may be asked for judgement - whether a severity looks right, whether a
   recommended action is adequate, what is missing. Give it, grounded in the
   record and the manuals, and flag clearly when something is a judgement call
   rather than a documented requirement.

What you cannot do:
You have no ability to create, edit, close or delete a record, and no tool for
it. If asked to change something, say plainly that you can only read records
and that the change has to be made in the record itself.

Style:
This is read on screen, not spoken aloud - so structure is welcome. Be concise
and concrete: short paragraphs, or a short list when comparing several things.
Lead with the answer, then the evidence for it.
"""


SUPERVISOR_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_maintenance_knowledge",
            "description": (
                "Search the ingested aircraft manuals (AMM, IPC, CMM, etc.) for "
                "torque specs, inspection intervals, part numbers, procedures, "
                "damage limits or troubleshooting steps."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What needs looking up, as a short search query.",
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
            "name": "search_maintenance_records",
            "description": (
                "Search maintenance findings logged by technicians. Use for "
                "questions that span more than the one open record - counts, "
                "history for an aircraft, another technician's findings, or "
                "everything at a given severity or status."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "aircraft_reg": {"type": "string", "description": "e.g. VT-ABC."},
                    "component": {"type": "string", "description": "Exact component name."},
                    "severity": {"type": "string", "description": "e.g. Minor, Major, Critical, AOG."},
                    "status": {"type": "string", "description": "OPEN, COMPLETE or CLOSED."},
                    "technician": {"type": "string", "description": "Technician's full name."},
                    "search": {
                        "type": "string",
                        "description": (
                            "Free text matched against aircraft, component, "
                            "location and the finding description."
                        ),
                    },
                    "limit": {"type": "integer", "description": "Max rows (default 25)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_record_details",
            "description": (
                "Fetch one maintenance record in full by its record id, "
                "including how many photos are attached. Use when the "
                "supervisor asks about a record other than the open one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "record_id": {"type": "string", "description": "The RECORD_ID to fetch."},
                },
                "required": ["record_id"],
            },
        },
    },
]


# Columns worth showing the model. The rest (internal ids, embeddings)
# would only burn context.
_RECORD_SUMMARY_FIELDS = (
    "RECORD_ID", "AIRCRAFT_REG", "COMPONENT", "FINDING", "SEVERITY",
    "LOCATION", "RECOMMENDED_ACTION", "TECHNICIAN", "STATUS",
    "INSPECTION_TS", "CREATED_AT",
)


def _summarise(record: dict) -> dict:
    return {k: record.get(k) for k in _RECORD_SUMMARY_FIELDS if record.get(k)}


def _describe_open_record(record: dict, photo_count: int = 0) -> str:
    """Render the open record as the context block for one turn."""
    lines = [
        "The supervisor currently has this maintenance record open on screen. "
        "Treat it as the subject of any question that does not name another record.",
        "",
    ]
    labels = {
        "RECORD_ID": "Record id",
        "AIRCRAFT_REG": "Aircraft",
        "COMPONENT": "Component",
        "LOCATION": "Location",
        "SEVERITY": "Severity",
        "STATUS": "Status",
        "TECHNICIAN": "Logged by",
        "INSPECTION_TS": "Inspected",
        "FINDING": "Finding",
        "RECOMMENDED_ACTION": "Recommended action",
    }
    for key, label in labels.items():
        value = record.get(key)
        if value:
            lines.append(f"{label}: {value}")

    missing = [
        label
        for key, label in labels.items()
        if key not in ("RECORD_ID", "INSPECTION_TS", "STATUS") and not record.get(key)
    ]
    if missing:
        lines.append("")
        lines.append(
            "Fields still empty on this record: " + ", ".join(missing) +
            ". Mention these if the supervisor asks whether it is complete."
        )

    lines.append("")
    lines.append(
        f"Damage photos attached: {photo_count}."
        if photo_count
        else "Damage photos attached: none."
    )
    return "\n".join(lines)


class SupervisorAssistant:
    """
    One instance = one supervisor's ongoing chat.

    Held per signed-in supervisor by the Flask app. The open record is
    passed in per turn rather than stored, because the supervisor clicks
    between findings freely and the assistant should always be talking
    about whatever is on screen right now.
    """

    # Keep the history bounded: a supervisor may keep this open all
    # shift, and every turn resends the whole thing to the model.
    MAX_HISTORY_MESSAGES = 40

    def __init__(self, supervisor_name: Optional[str] = None):
        self.supervisor_name = supervisor_name
        self.messages: list[dict] = [
            {"role": "system", "content": SUPERVISOR_SYSTEM_PROMPT}
        ]
        if supervisor_name:
            self.messages.append(
                {
                    "role": "system",
                    "content": f"The supervisor's name is {supervisor_name}.",
                }
            )
        self._preamble_length = len(self.messages)

    # ------------------------------------------------------
    # Public API
    # ------------------------------------------------------

    def ask_stream(
        self,
        question: str,
        record_id: Optional[str] = None,
        max_tool_iterations: int = 4,
    ):
        """
        Answer one supervisor question, yielding as it is generated:

            {"type": "content", "text": ...}   per token/chunk
            {"type": "tool",    "name": ...}   a lookup started (for the UI)
            {"type": "done",    "reply": ...}  once, with the full text

        `record_id` is whichever finding is open in the UI, or None.
        """
        context_note = self._build_context(record_id)
        if context_note:
            self.messages.append({"role": "system", "content": context_note})

        self.messages.append({"role": "user", "content": question})

        for _ in range(max_tool_iterations):
            stream = _chat_completion_stream(self.messages, tools=SUPERVISOR_TOOLS)

            content_parts: list[str] = []
            tool_calls: dict[int, dict] = {}

            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                if getattr(delta, "tool_calls", None):
                    for tc in delta.tool_calls:
                        slot = tool_calls.setdefault(
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

            if tool_calls:
                ordered = [tool_calls[i] for i in sorted(tool_calls)]
                self.messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {
                                    "name": tc["name"],
                                    "arguments": tc["arguments"],
                                },
                            }
                            for tc in ordered
                        ],
                    }
                )
                for tc in ordered:
                    # Let the UI show "Searching the manuals…" rather than
                    # sitting silent through a vector search.
                    yield {"type": "tool", "name": tc["name"]}
                    result = self._execute_tool(tc["name"], tc["arguments"])
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": json.dumps(result, default=str),
                        }
                    )
                continue  # next pass produces the answer

            reply = "".join(content_parts)
            self.messages.append({"role": "assistant", "content": reply})
            self._trim_history()
            yield {"type": "done", "reply": reply}
            return

        logger.warning("Supervisor assistant hit the tool-iteration ceiling")
        fallback = (
            "I looked a few things up but couldn't settle that one. "
            "Try narrowing the question?"
        )
        self.messages.append({"role": "assistant", "content": fallback})
        self._trim_history()
        yield {"type": "done", "reply": fallback}

    def reset(self) -> None:
        """Start a fresh conversation, keeping the system preamble."""
        del self.messages[self._preamble_length:]

    # ------------------------------------------------------
    # Internals
    # ------------------------------------------------------

    def _build_context(self, record_id: Optional[str]) -> Optional[str]:
        if not record_id:
            return None

        record = get_maintenance_record(record_id)
        if not record:
            return None

        try:
            photo_count = len(list_record_photos(record_id))
        except Exception:  # noqa: BLE001 - context is a nicety, not the answer
            photo_count = 0

        return _describe_open_record(record, photo_count)

    def _trim_history(self) -> None:
        """
        Drop the oldest turns once the history grows past the cap, always
        keeping the system preamble.

        A tool result must never outlive the assistant message whose
        tool_calls it answers, or the next request is malformed - so the
        window is advanced to start at a clean user message.
        """
        overflow = len(self.messages) - self._preamble_length - self.MAX_HISTORY_MESSAGES
        if overflow <= 0:
            return

        cut = self._preamble_length + overflow
        while cut < len(self.messages) and self.messages[cut].get("role") != "user":
            cut += 1

        if cut < len(self.messages):
            del self.messages[self._preamble_length:cut]

    def _execute_tool(self, name: str, raw_arguments: str) -> dict[str, Any]:
        try:
            arguments = json.loads(raw_arguments) if raw_arguments else {}
        except json.JSONDecodeError:
            logger.warning("Unparseable tool arguments for %s: %r", name, raw_arguments)
            arguments = {}

        logger.info("Supervisor assistant tool '%s' args=%s", name, arguments)

        if name == "search_maintenance_knowledge":
            return search_maintenance_knowledge(arguments)
        if name == "search_maintenance_records":
            return self._tool_search_records(arguments)
        if name == "get_record_details":
            return self._tool_get_record(arguments)

        return {"error": f"Unknown tool '{name}'"}

    def _tool_search_records(self, arguments: dict) -> dict:
        limit = min(int(arguments.get("limit", 25) or 25), 100)
        try:
            records = list_maintenance_records(
                aircraft_reg=arguments.get("aircraft_reg"),
                component=arguments.get("component"),
                severity=arguments.get("severity"),
                status=arguments.get("status"),
                technician=arguments.get("technician"),
                search=arguments.get("search"),
                limit=limit,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Record search failed")
            return {"error": f"Record search is unavailable right now: {exc}"}

        return {
            "count": len(records),
            "records": [_summarise(r) for r in records],
        }

    def _tool_get_record(self, arguments: dict) -> dict:
        record_id = arguments.get("record_id")
        if not record_id:
            return {"error": "No record_id provided"}

        record = get_maintenance_record(record_id)
        if not record:
            return {"record": None, "note": f"No record with id '{record_id}'."}

        return {
            "record": _summarise(record),
            "photo_count": len(list_record_photos(record_id)),
        }
