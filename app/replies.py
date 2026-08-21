from __future__ import annotations

import re
from typing import Any

from app.store import Conversation, MemoryStore


AUTO_REPLY_PATTERNS = (
    "thank you for contacting",
    "team will respond",
    "will respond shortly",
    "automated assistant",
    "out of office",
    "away message",
)
HOSTILE_OR_OPTOUT = (
    "stop",
    "unsubscribe",
    "do not message",
    "don't message",
    "not interested",
    "useless spam",
)
COMMITMENT_PATTERNS = (
    "yes",
    "go ahead",
    "lets do it",
    "let's do it",
    "confirm",
    "proceed",
    "do it",
    "refill",
    "check",
    "plan",
    "repeat",
    "review",
    "match",
    "fix",
    "milestone",
    "checklist",
    "shelf",
    "verify",
    "cde",
    "compare",
    "resume",
)
DELAY_PATTERNS = ("later", "busy", "tomorrow", "not now", "after some time")


def _normalized(message: str) -> str:
    return re.sub(r"\s+", " ", message.casefold().strip())


def _trigger_artifact(store: MemoryStore, conversation: Conversation) -> str | None:
    trigger_record = store.get("trigger", conversation.trigger_id)
    if not trigger_record:
        return None
    trigger = trigger_record.payload
    kind = str(trigger.get("kind", ""))
    payload = trigger.get("payload", {})

    if kind == "supply_alert":
        batches = ", ".join(str(value) for value in payload.get("affected_batches", []))
        return (
            f"Checklist ready for {payload.get('molecule')}: isolate batches {batches}; compare "
            "shelf and purchase records; document affected stock; then follow the supplied return process."
        )
    if kind == "regulation_change":
        return (
            f"Checklist ready for the {payload.get('deadline_iso')} deadline: verify the referenced "
            "circular, audit the current setup, record gaps, and update the SOP before the deadline."
        )
    if kind in {"research_digest", "cde_opportunity"}:
        return (
            "Next step ready: verify the referenced digest item, compare it with the relevant "
            "merchant cohort or workflow, and record one applicable action before sharing it."
        )
    if kind in {"perf_dip", "perf_spike", "seasonal_perf_dip"}:
        metric = str(payload.get("metric", "metric"))
        return (
            f"Review plan ready for {metric}: confirm the {payload.get('window', 'latest')} change, "
            "compare it with the stored 30-day value, check one likely operational cause, and measure again next week."
        )
    if kind == "renewal_due":
        return (
            f"Renewal checklist ready: confirm the {payload.get('plan')} plan, verify the listed "
            f"{payload.get('days_remaining')} days remaining and ₹{payload.get('renewal_amount')}, then approve or decline."
        )
    if kind == "review_theme_emerged":
        return (
            f"Service-recovery checklist ready for {str(payload.get('theme', '')).replace('_', ' ')}: "
            "verify the cited reviews, identify the recurring handoff, assign one fix, and monitor the next reviews."
        )
    if kind == "active_planning_intent":
        topic = str(payload.get("intent_topic", "program")).replace("_", " ")
        return (
            f"Draft structure ready for {topic}: title, intended audience, schedule, duration, capacity, "
            "fee, and one response CTA. Fill only those confirmed facts, then publish the final copy."
        )
    if kind == "ipl_match_today":
        return (
            f"Post draft: “{payload.get('match')} starts at {str(payload.get('match_time_iso', ''))[11:16]} "
            "today. Message us to check the currently active match-time option.”"
        )
    if kind == "category_seasonal":
        trends = ", ".join(str(value).replace("_", " ") for value in payload.get("trends", []))
        return f"Shelf checklist ready: verify current stock against {trends}; then move only confirmed high-demand items forward."
    if kind == "gbp_unverified":
        return (
            f"Verification checklist ready: confirm business details and supporting records, then use the "
            f"provided {str(payload.get('verification_path', '')).replace('_', ' ')} path."
        )
    if kind in {"recall_due", "trial_followup", "chronic_refill_due", "wedding_package_followup", "customer_lapsed_hard"}:
        return (
            "Confirmed. This request is ready for the merchant team to review against the supplied "
            "slot, service, and consent details; no unsupported booking has been claimed."
        )
    return "Proceeding with the supported next step from this trigger; the draft will use only the facts already supplied."


def _identity_mismatch(
    conversation: Conversation,
    merchant_id: str | None,
    customer_id: str | None,
    from_role: str,
) -> bool:
    expected_role = "customer" if conversation.customer_id else "merchant"
    return bool(
        conversation.merchant_id != merchant_id
        or conversation.customer_id != customer_id
        or expected_role != from_role
    )


def _send_once(conversation: Conversation, body: str, rationale: str) -> dict[str, Any]:
    if body == conversation.last_body:
        conversation.status = "ended"
        return {"action": "end", "rationale": "Stopped instead of repeating an identical body."}
    conversation.last_body = body
    return {"action": "send", "body": body, "cta": "none", "rationale": rationale}


def handle_reply(
    store: MemoryStore,
    conversation_id: str,
    merchant_id: str | None,
    customer_id: str | None,
    from_role: str,
    message: str,
) -> dict[str, Any]:
    existing = store.conversation(conversation_id)
    if existing and _identity_mismatch(existing, merchant_id, customer_id, from_role):
        return {
            "action": "end",
            "rationale": "Reply identity does not match the stored conversation; no state was changed.",
        }

    conversation = existing or store.ensure_conversation(conversation_id, merchant_id, customer_id)
    effective_merchant_id = conversation.merchant_id
    effective_customer_id = conversation.customer_id
    normalized = _normalized(message)

    with store.lock:
        if conversation.status == "ended":
            return {"action": "end", "rationale": "Conversation was already ended."}
        conversation.turn_count += 1

    if any(pattern in normalized for pattern in HOSTILE_OR_OPTOUT):
        if from_role == "customer" and effective_customer_id:
            store.block_customer(effective_customer_id)
            rationale = "Customer opt-out detected; only this customer is suppressed."
        else:
            store.block_merchant(effective_merchant_id)
            rationale = "Merchant opt-out detected; future proactive merchant contact is suppressed."
        with store.lock:
            conversation.status = "ended"
        return {"action": "end", "rationale": rationale}

    if any(pattern in normalized for pattern in AUTO_REPLY_PATTERNS):
        count = store.note_auto_reply(effective_merchant_id)
        if count >= 3:
            with store.lock:
                conversation.status = "ended"
            return {
                "action": "end",
                "rationale": "Repeated automated replies detected; loop ended after three occurrences.",
            }
        wait_seconds = 1800 if count == 1 else 3600
        return {
            "action": "wait",
            "wait_seconds": wait_seconds,
            "rationale": f"Automated reply detected ({count}/3); avoiding a bot-to-bot loop.",
        }

    is_commitment = normalized in {"1", "2"} or any(
        pattern in normalized for pattern in COMMITMENT_PATTERNS
    )
    if is_commitment:
        body = _trigger_artifact(store, conversation)
        if not body:
            body = (
                "Proceeding to the next step. I’ll prepare the context-based draft/checklist "
                "requested, without adding unsupported facts."
            )
        with store.lock:
            return _send_once(
                conversation,
                body,
                "Explicit commitment detected; returned a trigger-specific artifact when context was available.",
            )

    if any(pattern in normalized for pattern in DELAY_PATTERNS):
        return {
            "action": "wait",
            "wait_seconds": 3600,
            "rationale": "The recipient asked to continue later.",
        }

    if normalized.endswith("?") or normalized.startswith(("what", "how", "why", "when")):
        body = _trigger_artifact(store, conversation) or (
            "I can answer from the stored merchant, category and trigger context, but I won’t guess "
            "missing details. Send the specific fact you want checked and I’ll give the next supported step."
        )
        rationale = "Answered with the current trigger artifact when context was available."
    else:
        body = "Noted. Reply YES to continue with the proposed next step, LATER to pause, or STOP to end."
        rationale = "Handled with a deterministic fallback and one low-friction next step."

    with store.lock:
        return _send_once(conversation, body, rationale)
