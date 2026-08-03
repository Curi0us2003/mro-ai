"""
==============================================================
AI Maintenance Voice Copilot
Scripts Package
--------------------------------------------------------------

One-off / operational scripts that are run manually or via a
scheduler, as opposed to code that runs inside the Flask app
request lifecycle.

Contains:
    ingest_manuals.py   Chunk + embed aircraft manuals into
                        SAP HANA Cloud for semantic search.
    manage_users.py     Create and manage login accounts. This
                        is the only way an account is created -
                        the HTTP API has no sign-up endpoint.
==============================================================
"""