"""A stand-in for the downstream order service, with switchable failure modes for fault injection.

Modes (POST /admin/mode {"mode": ...}):
  normal   - accept documents (HTTP 201)
  slow     - sleep longer than IntegrX's timeout (causes DOWNSTREAM_TIMEOUT)
  error500 - return HTTP 500 (DOWNSTREAM_SERVER_ERROR)
  auth401  - return HTTP 401 (DOWNSTREAM_AUTH_FAILURE)
"""
from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

app = FastAPI(title="order-service (stub)")
STATE = {"mode": os.environ.get("ORDER_SERVICE_MODE", "normal"), "received": 0}
SLOW_SECONDS = float(os.environ.get("ORDER_SERVICE_SLOW_SECONDS", "35"))
TOKEN = os.environ.get("ORDER_SERVICE_TOKEN", "demo-downstream-token")
SEEN: set[str] = set()


class Mode(BaseModel):
    mode: str


@app.post("/admin/mode")
def set_mode(m: Mode):
    if m.mode not in ("normal", "slow", "error500", "auth401"):
        raise HTTPException(400, "mode must be normal, slow, error500 or auth401")
    STATE["mode"] = m.mode
    return STATE


@app.get("/health")
def health():
    return {"status": "ok" if STATE["mode"] == "normal" else f"degraded ({STATE['mode']})", **STATE}


@app.post("/api/documents", status_code=201)
async def receive(request: Request, authorization: str = Header(default=""), idempotency_key: str = Header(default="")):
    mode = STATE["mode"]
    if mode == "slow":
        await asyncio.sleep(SLOW_SECONDS)
    if mode == "error500":
        raise HTTPException(500, "order-service internal error: database connection pool exhausted")
    if mode == "auth401" or authorization != f"Bearer {TOKEN}":
        raise HTTPException(401, "invalid token")
    doc = await request.json()
    duplicate = idempotency_key in SEEN
    SEEN.add(idempotency_key)
    STATE["received"] += 1
    return {"accepted": True, "duplicate": duplicate, "doc_type": doc.get("doc_type")}
