from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
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
