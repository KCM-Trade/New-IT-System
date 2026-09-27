"""AI analyst agent — the code that runs INSIDE the `ai-agent` container.

Contract: docs/ai-agent/02-contracts.md. The main API (routes/ai.py) owns the
module gate, audit and quota; this package owns the model harness and the three
certified tools. Nothing here reads users.db or trusts anything but the
`caller` / `scope` the main API forwards on each request.

Layout:
    server.py   FastAPI app on :8010 — X-Internal-Token, SSE relay
    harness.py  Microsoft Agent Framework wiring (the only MAF import site)
    prompt.py   analyst system prompt + per-tool manuals
    tools/      the certified tools; framework-free, testable without MAF
"""
