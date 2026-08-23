from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.composer import compose_tick
from app.replies import handle_reply
from app.store import SCOPES, store


APP_VERSION = "1.0.0"
SUBMITTED_AT = os.getenv("SUBMITTED_AT", "2026-08-21T00:00:00Z")

app = FastAPI(
    title="Vera Deterministic Engagement Bot",
    version=APP_VERSION,
    docs_url="/docs",
    redoc_url=None,
)

LANDING_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Vera Engagement Bot</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px; color: #f7f7fb; background: radial-gradient(circle at top, #43216f 0, #17121f 42%, #0c0a0f 100%); }
    main { width: min(720px, 100%); padding: 42px; border: 1px solid #ffffff24; border-radius: 22px; background: #151119dd; box-shadow: 0 24px 70px #0008; }
    .status { display: inline-flex; align-items: center; gap: 8px; padding: 7px 11px; border: 1px solid #64d99155; border-radius: 999px; color: #8af0ad; background: #16332288; font-size: 14px; }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: #63e693; box-shadow: 0 0 14px #63e693; }
    h1 { margin: 24px 0 10px; font-size: clamp(34px, 7vw, 56px); line-height: 1; letter-spacing: -0.04em; }
    p { color: #c8c1ce; font-size: 17px; line-height: 1.65; }
    .links { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 28px; }
    a { padding: 12px 16px; border-radius: 10px; color: white; text-decoration: none; font-weight: 650; background: #7c3aed; }
    a.secondary { border: 1px solid #ffffff25; background: #ffffff0c; }
    .meta { margin-top: 30px; padding-top: 20px; border-top: 1px solid #ffffff18; color: #8f8798; font-size: 13px; }
  </style>
</head>
<body>
  <main>
    <div class="status"><span class="dot"></span>Service online</div>
    <h1>Vera Engagement Bot</h1>
    <p>A deterministic engagement service that turns versioned category, merchant, customer, and trigger context into safe, relevant actions.</p>
    <div class="links">
      <a href="/docs">Explore API</a>
      <a class="secondary" href="/v1/healthz">Health status</a>
      <a class="secondary" href="/v1/metadata">Metadata</a>
    </div>
    <div class="meta">API version 1.0.0 &middot; Built for reliable context-driven engagement</div>
  </main>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def landing_page():
    return LANDING_PAGE


class ContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str
    context_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    payload: dict[str, Any]
    delivered_at: datetime


class TickRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    now: datetime
    available_triggers: list[str] = Field(default_factory=list)


class ReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(min_length=1)
    merchant_id: str | None = None
    customer_id: str | None = None
    from_role: Literal["merchant", "customer"]
    message: str = Field(min_length=1)
    received_at: datetime
    turn_number: int = Field(ge=1)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@app.post("/v1/context")
def receive_context(request: ContextRequest):
    if request.scope not in SCOPES:
        return JSONResponse(
            status_code=400,
            content={
                "accepted": False,
                "reason": "invalid_scope",
                "details": f"scope must be one of: {', '.join(SCOPES)}",
            },
        )

    accepted, current_version = store.put_context(
        request.scope, request.context_id, request.version, request.payload
    )
    if not accepted:
        return JSONResponse(
            status_code=409,
            content={
                "accepted": False,
                "reason": "stale_version",
                "current_version": current_version,
            },
        )

    ack_material = f"{request.scope}:{request.context_id}:{request.version}"
    ack_id = "ack_" + hashlib.sha256(ack_material.encode("utf-8")).hexdigest()[:16]
    return {
        "accepted": True,
        "ack_id": ack_id,
        "stored_at": _utc_now().isoformat().replace("+00:00", "Z"),
    }


@app.post("/v1/tick")
def tick(request: TickRequest):
    now = request.now if request.now.tzinfo else request.now.replace(tzinfo=timezone.utc)
    return {"actions": compose_tick(store, now, request.available_triggers)}


@app.post("/v1/reply")
def reply(request: ReplyRequest):
    return handle_reply(
        store=store,
        conversation_id=request.conversation_id,
        merchant_id=request.merchant_id,
        customer_id=request.customer_id,
        from_role=request.from_role,
        message=request.message,
    )


@app.get("/v1/healthz")
def healthz():
    uptime = max(0, int((_utc_now() - store.started_at).total_seconds()))
    return {
        "status": "ok",
        "uptime_seconds": uptime,
        "contexts_loaded": store.counts(),
    }


@app.get("/v1/metadata")
def metadata():
    return {
        "team_name": os.getenv("TEAM_NAME", "magicpin"),
        "team_members": [os.getenv("TEAM_MEMBER", "magicpin")],
        "model": "deterministic-rules-v1",
        "approach": "versioned context store + safe entity joins + trigger templates + reply state machine",
        "contact_email": os.getenv("CONTACT_EMAIL", "vera@magicpin.com"),
        "version": APP_VERSION,
        "submitted_at": SUBMITTED_AT,
    }
