"""
==============================================================
AI Maintenance Voice Copilot
Manual Chat CLI (standalone, no login/session required)
--------------------------------------------------------------

Purpose
-------
A quick way to ask questions of the ingested aircraft manuals
from the terminal, without going through the Flask app / login.
Uses the exact same MANUAL_CHUNKS table, embedding model, and
chat model as the main app - so it always reflects what's
actually ingested.

Usage
-----
    # Ingest manuals first if you haven't already:
    python -m backend.scripts.ingest_manuals

    # One-shot question
    python -m backend.scripts.chat_manuals "torque spec for the main gear bolts?"

    # Interactive REPL
    python -m backend.scripts.chat_manuals
==============================================================
"""

from __future__ import annotations

import sys
import textwrap

from backend.config import (
    AICORE_CHAT_MODEL,
    AICORE_CHAT_DEPLOYMENT_ID,
    TOP_K_RESULTS,
    MIN_RELEVANCE_SCORE,
)
from backend.embeddings import embed_query
from backend.database import semantic_search, count_chunks

SYSTEM_PROMPT = """You answer questions using only the excerpts provided.

Rules:
- Ground every statement in the excerpts. Cite as [file, p.N].
- If the excerpts do not contain the answer, say so plainly and say
  what would be needed. Do not fill the gap from general knowledge.
- Quote exact figures, torque values, part numbers and step numbers
  verbatim rather than paraphrasing them.
- Be concise. A technician is reading this while holding a wrench."""


def chat_completion(messages: list[dict]) -> str:
    from gen_ai_hub.proxy.native.openai import chat

    kwargs: dict = {"messages": messages, "temperature": 0.1}
    if AICORE_CHAT_DEPLOYMENT_ID:
        kwargs["deployment_id"] = AICORE_CHAT_DEPLOYMENT_ID
    else:
        kwargs["model_name"] = AICORE_CHAT_MODEL

    response = chat.completions.create(**kwargs)
    return response.choices[0].message.content or ""


def build_context(hits: list[dict]) -> str:
    return "\n\n---\n\n".join(
        f"[{hit['FILE_NAME']}, p.{hit['PAGE_NUMBER']}]\n{hit['CONTENT']}"
        for hit in hits
    )


def ask(question: str, history: list[dict]) -> tuple[str, list[dict]]:
    hits = semantic_search(
        embed_query(question),
        top_k=TOP_K_RESULTS,
        min_score=MIN_RELEVANCE_SCORE,
    )
    if not hits:
        return "Nothing in the ingested manuals is relevant to that question.", []

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history[-6:])   # last 3 exchanges, keeps the prompt small
    messages.append({
        "role": "user",
        "content": f"Excerpts:\n\n{build_context(hits)}\n\nQuestion: {question}",
    })
    return chat_completion(messages), hits


def print_sources(hits: list[dict]) -> None:
    print("Sources:")
    for hit in hits:
        print(f"    {hit['SCORE']:.3f}  {hit['FILE_NAME']} p.{hit['PAGE_NUMBER']}")


def main() -> None:
    if count_chunks() == 0:
        sys.exit(
            "No manuals ingested yet. Run: python -m backend.scripts.ingest_manuals"
        )

    history: list[dict] = []

    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
        reply, hits = ask(question, history)
        print("\n" + textwrap.fill(reply, width=88))
        if hits:
            print()
            print_sources(hits)
        return

    print("Chatting over the ingested manuals. Ctrl-C or 'exit' to quit.\n")
    while True:
        try:
            question = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question:
            continue
        if question.lower() in {"exit", "quit", ":q"}:
            break

        reply, hits = ask(question, history)
        print("\nbot > " + textwrap.fill(reply, width=88, subsequent_indent="      "))
        if hits:
            print_sources(hits)
        print()

        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    main()
