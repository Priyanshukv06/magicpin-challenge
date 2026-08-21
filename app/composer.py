from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from app.store import Conversation, MemoryStore


CONSENT_BY_KIND = {
    "recall_due": {"recall_reminders", "appointment_reminders"},
    "wedding_package_followup": {"bridal_package_followup"},
    "customer_lapsed_hard": {"winback_offers", "renewal_reminders"},
    "trial_followup": {"kids_program_updates", "program_updates"},
    "chronic_refill_due": {"refill_reminders"},
}


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _percent(value: Any) -> str:
    try:
        return f"{abs(float(value)) * 100:.0f}%"
    except (TypeError, ValueError):
        return ""


def _money(value: Any) -> str:
    try:
        return f"₹{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def _first_name(merchant: dict[str, Any]) -> str:
    identity = merchant.get("identity", {})
    return str(identity.get("owner_first_name") or identity.get("name") or "there")


def _merchant_name(merchant: dict[str, Any]) -> str:
    return str(merchant.get("identity", {}).get("name") or "your business")


def _active_offer(merchant: dict[str, Any]) -> str | None:
    for offer in merchant.get("offers", []):
        if offer.get("status") == "active" and offer.get("title"):
            return str(offer["title"])
    return None


def _digest(category: dict[str, Any], item_id: str | None) -> dict[str, Any] | None:
    return next(
        (item for item in category.get("digest", []) if item.get("id") == item_id),
        None,
    )


def _identity_ok(customer: dict[str, Any]) -> bool:
    identity = customer.get("identity", {})
    preferences = customer.get("preferences", {})
    consent = customer.get("consent", {})
    return bool(
        identity.get("phone_redacted")
        and preferences.get("channel") not in (None, "none_recorded")
        and preferences.get("reminder_opt_in") is True
        and consent.get("opted_in_at")
        and consent.get("scope")
    )


def _has_consent(customer: dict[str, Any], kind: str) -> bool:
    required = CONSENT_BY_KIND.get(kind)
    granted = set(customer.get("consent", {}).get("scope", []))
    return bool(required and required.intersection(granted))


def _customer_greeting(customer: dict[str, Any]) -> str:
    identity = customer.get("identity", {})
    name = str(identity.get("name") or "there")
    language = str(identity.get("language_pref", "english")).casefold()
    if language == "hi" or "hi-en" in language:
        return f"Namaste {name}"
    if "ta-en" in language:
        return f"Vanakkam {name}"
    if "kn-en" in language:
        return f"Namaskara {name}"
    if "te-en" in language:
        return f"Namaskaram {name}"
    return f"Hi {name}"


def _merchant_greeting(merchant: dict[str, Any], category: dict[str, Any]) -> str:
    first_name = _first_name(merchant)
    tone = category.get("voice", {}).get("tone")
    if tone == "peer_clinical":
        return f"Dr. {first_name}"
    return f"Hi {first_name}"


def _slot_labels(payload: dict[str, Any], field: str) -> list[str]:
    return [str(slot["label"]) for slot in payload.get(field, []) if slot.get("label")]


def _compose_customer(
    kind: str,
    payload: dict[str, Any],
    merchant: dict[str, Any],
    customer: dict[str, Any],
) -> tuple[str, str] | None:
    hello = _customer_greeting(customer)
    merchant_name = _merchant_name(merchant)

    if kind == "recall_due":
        slots = _slot_labels(payload, "available_slots")
        if not slots or not payload.get("due_date"):
            return None
        choices = " or ".join(slots[:2])
        return (
            f"{hello}, {merchant_name} here. Your cleaning recall is due on "
            f"{payload['due_date']}. We have {choices}. Reply 1 or 2 to request a slot, "
            "or STOP to opt out.",
            "multi_choice_slot",
        )

    if kind == "wedding_package_followup":
        wedding_date = payload.get("wedding_date")
        trial_date = payload.get("trial_completed")
        if not wedding_date or not trial_date:
            return None
        return (
            f"{hello}, {merchant_name} here. Following up after your bridal trial on "
            f"{trial_date}; your wedding date is {wedding_date}. Reply YES if you want "
            "the next-step plan, or STOP to opt out.",
            "binary_yes_no",
        )

    if kind == "customer_lapsed_hard":
        days = payload.get("days_since_last_visit")
        focus = str(payload.get("previous_focus", "your earlier goal")).replace("_", " ")
        offer = _active_offer(merchant)
        if days is None:
            return None
        offer_text = f" {offer} is currently active." if offer else ""
        return (
            f"{hello}, {merchant_name} here. It has been {days} days since your last visit, "
            f"and your earlier focus was {focus}.{offer_text} Reply YES for a restart call, "
            "or STOP to opt out.",
            "binary_yes_no",
        )

    if kind == "trial_followup":
        slots = _slot_labels(payload, "next_session_options")
        if not payload.get("trial_date") or not slots:
            return None
        return (
            f"{hello}, {merchant_name} here. Following up on the trial from "
            f"{payload['trial_date']}. The next available session is {slots[0]}. "
            "Reply YES to request it, or STOP to opt out.",
            "binary_yes_no",
        )

    if kind == "chronic_refill_due":
        medicines = ", ".join(str(item) for item in payload.get("molecule_list", []))
        runs_out = payload.get("stock_runs_out_iso")
        if not medicines or not runs_out:
            return None
        delivery = " Your delivery address is saved." if payload.get("delivery_address_saved") else ""
        return (
            f"{hello}, {merchant_name} here with your opted-in refill reminder. Your "
            f"{medicines} stock is expected to run out by {str(runs_out)[:10]}.{delivery} "
            "Reply REFILL to request a pharmacist callback, or STOP to opt out.",
            "binary_yes_no",
        )

    return None


def _compose_merchant(
    kind: str,
    payload: dict[str, Any],
    merchant: dict[str, Any],
    category: dict[str, Any],
) -> tuple[str, str] | None:
    hello = _merchant_greeting(merchant, category)
    business = _merchant_name(merchant)
    locality = merchant.get("identity", {}).get("locality")
    performance = merchant.get("performance", {})

    if kind in {"research_digest", "regulation_change"}:
        item = _digest(category, payload.get("top_item_id"))
        if not item:
            return None
        deadline = f" Deadline: {payload['deadline_iso']}." if payload.get("deadline_iso") else ""
        cohort = merchant.get("customer_aggregate", {}).get("high_risk_adult_count")
        relevance = f" You have {cohort} high-risk adults in your current cohort." if cohort else ""
        return (
            f"{hello} — {item.get('title')}. Source: {item.get('source')}."
            f"{deadline}{relevance} Recommended next step: {item.get('actionable')}. "
            "Reply CHECK and I’ll turn this into a short action checklist.",
            "open_ended",
        )

    if kind in {"perf_dip", "perf_spike", "seasonal_perf_dip"}:
        metric = str(payload.get("metric", "metric"))
        change = _percent(payload.get("delta_pct"))
        direction = "down" if float(payload.get("delta_pct", 0)) < 0 else "up"
        current = performance.get(metric)
        current_text = f"; current 30-day {metric}: {current}" if current is not None else ""
        seasonal = " This is marked as an expected seasonal pattern." if payload.get("is_expected_seasonal") else ""
        cta = "PLAN" if direction == "down" else "REPEAT"
        return (
            f"{hello} — {business}'s {metric} are {direction} {change} over "
            f"{payload.get('window', 'the latest window')}{current_text}.{seasonal} "
            f"Reply {cta} for one context-based next step.",
            "open_ended",
        )

    if kind == "renewal_due":
        if payload.get("days_remaining") is None or payload.get("renewal_amount") is None:
            return None
        return (
            f"{hello} — your {payload.get('plan', 'current')} plan for {business} has "
            f"{payload['days_remaining']} days left; the listed renewal amount is "
            f"{_money(payload['renewal_amount'])}. Reply REVIEW for a renewal checklist, "
            "or STOP to close this reminder.",
            "binary_yes_no",
        )

    if kind == "festival_upcoming":
        if not payload.get("festival") or not payload.get("date"):
            return None
        offer = _active_offer(merchant)
        offer_text = f" Your active offer is “{offer}”." if offer else ""
        return (
            f"{hello} — {payload['festival']} is on {payload['date']}, with "
            f"{payload.get('days_until')} days to prepare.{offer_text} Reply PLAN and I’ll "
            "draft a simple campaign using only your active offer.",
            "open_ended",
        )

    if kind == "curious_ask_due":
        return (
            f"{hello} — quick pulse check for {business}: which service had the most "
            "customer enquiries this week? Reply with the service name and I’ll map one "
            "practical follow-up.",
            "open_ended",
        )

    if kind == "winback_eligible":
        return (
            f"{hello} — it has been {payload.get('days_since_expiry')} days since the plan "
            f"expired. Performance is down {_percent(payload.get('perf_dip_pct'))}, and "
            f"{payload.get('lapsed_customers_added_since_expiry')} customers entered the "
            "lapsed segment in that period. Reply REVIEW for a no-assumptions win-back plan.",
            "open_ended",
        )

    if kind == "ipl_match_today":
        offer = _active_offer(merchant)
        if not all(payload.get(key) for key in ("match", "match_time_iso", "city")):
            return None
        offer_text = f" Your active offer is “{offer}”." if offer else ""
        return (
            f"{hello} — {payload['match']} starts at {str(payload['match_time_iso'])[11:16]} "
            f"in {payload['city']} today.{offer_text} Reply MATCH and I’ll draft one concise "
            "match-time post without inventing a new discount.",
            "open_ended",
        )

    if kind == "review_theme_emerged":
        return (
            f"{hello} — “{str(payload.get('theme', '')).replace('_', ' ')}” appeared in "
            f"{payload.get('occurrences_30d')} reviews over 30 days and is marked "
            f"{payload.get('trend')}. Reply FIX for a three-step service recovery checklist.",
            "open_ended",
        )

    if kind == "milestone_reached":
        return (
            f"{hello} — {business} is at {payload.get('value_now')} "
            f"{str(payload.get('metric', 'results')).replace('_', ' ')}, just "
            f"{int(payload.get('milestone_value', 0)) - int(payload.get('value_now', 0))} "
            f"away from {payload.get('milestone_value')}. Reply MILESTONE for a thank-you post draft.",
            "open_ended",
        )

    if kind == "active_planning_intent":
        topic = str(payload.get("intent_topic", "the plan")).replace("_", " ")
        offer = _active_offer(merchant)
        if topic == "corporate bulk thali package" and offer:
            body = (
                f"{hello} — draft direction for the corporate package: use your active “{offer}” "
                "as the base, then state the minimum order and delivery window before publishing. "
                "Reply with those two details and I’ll format the final listing copy."
            )
        else:
            body = (
                f"{hello} — working brief for {topic}: confirm the age group, schedule, duration, "
                "capacity and fee before publishing. Reply with those five facts and I’ll format "
                "the final program copy without guessing."
            )
        return body, "open_ended"

    if kind == "supply_alert":
        batches = ", ".join(str(item) for item in payload.get("affected_batches", []))
        if not payload.get("molecule") or not batches:
            return None
        return (
            f"{hello} — supply alert for {payload['molecule']} from "
            f"{payload.get('manufacturer')}: affected batches {batches}. Check shelf stock and "
            "purchase records first. Reply CHECKLIST for a batch-audit sequence.",
            "open_ended",
        )

    if kind == "category_seasonal":
        trends = ", ".join(str(item).replace("_", " ") for item in payload.get("trends", []))
        if not trends:
            return None
        return (
            f"{hello} — {payload.get('season', 'seasonal')} demand signal: {trends}. "
            "The supplied recommendation is to review shelf placement. Reply SHELF for a "
            "stock-and-placement checklist.",
            "open_ended",
        )

    if kind == "gbp_unverified":
        return (
            f"{hello} — {business}'s Google Business Profile is marked unverified. The available "
            f"path is {str(payload.get('verification_path', '')).replace('_', ' ')}. Reply VERIFY "
            "for the preparation checklist.",
            "open_ended",
        )

    if kind == "cde_opportunity":
        item = _digest(category, payload.get("digest_item_id"))
        if not item:
            return None
        return (
            f"{hello} — {item.get('title')} is scheduled for {str(item.get('date'))[:16]} "
            f"and carries {payload.get('credits')} credits. Fee: "
            f"{str(payload.get('fee', '')).replace('_', ' ')}. Reply CDE for a calendar checklist.",
            "open_ended",
        )

    if kind == "competitor_opened":
        if not payload.get("competitor_name") or payload.get("distance_km") is None:
            return None
        offer = _active_offer(merchant)
        strength = f" Your verified active offer is “{offer}”." if offer else ""
        return (
            f"{hello} — {payload['competitor_name']} opened {payload['distance_km']} km away "
            f"with “{payload.get('their_offer')}”.{strength} Reply COMPARE for a factual profile "
            "differentiation checklist—no price war assumptions.",
            "open_ended",
        )

    if kind == "dormant_with_vera":
        return (
            f"{hello} — checking in after {payload.get('days_since_last_merchant_message')} days. "
            f"The last topic was {str(payload.get('last_topic', '')).replace('_', ' ')}. "
            "Reply RESUME to continue from there, or STOP to close the thread.",
            "binary_yes_no",
        )

    return None


def compose_tick(store: MemoryStore, now: datetime, trigger_ids: list[str]) -> list[dict[str, Any]]:
    candidates: list[tuple[int, datetime, str, dict[str, Any], int]] = []
    fallback_expiry = datetime.max.replace(tzinfo=timezone.utc)

    for trigger_id in set(trigger_ids):
        record = store.get("trigger", trigger_id)
        if not record:
            continue
        trigger = record.payload
        expires_at = _parse_time(trigger.get("expires_at"))
        if expires_at and expires_at <= now:
            continue
        if trigger.get("payload", {}).get("placeholder"):
            continue
        try:
            urgency = int(trigger.get("urgency", 0))
        except (TypeError, ValueError):
            urgency = 0
        candidates.append((-urgency, expires_at or fallback_expiry, trigger_id, trigger, record.version))

    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    actions: list[dict[str, Any]] = []

    for _, _, trigger_id, trigger, trigger_version in candidates:
        if len(actions) >= 20:
            break
        merchant_id = trigger.get("merchant_id")
        merchant_record = store.get("merchant", merchant_id)
        if not merchant_record or store.is_blocked(merchant_id):
            continue
        merchant = merchant_record.payload
        if merchant.get("merchant_id") != merchant_id:
            continue
        category_record = store.get("category", merchant.get("category_slug"))
        if not category_record:
            continue
        category = category_record.payload
        if category.get("slug") != merchant.get("category_slug"):
            continue

        scope = trigger.get("scope")
        kind = str(trigger.get("kind", ""))
        customer_id = trigger.get("customer_id")
        if scope == "customer":
            customer_record = store.get("customer", customer_id)
            if not customer_record:
                continue
            customer = customer_record.payload
            if (
                customer.get("customer_id") != customer_id
                or customer.get("merchant_id") != merchant_id
                or store.is_customer_blocked(customer_id)
                or not _identity_ok(customer)
                or not _has_consent(customer, kind)
            ):
                continue
            composed = _compose_customer(kind, trigger.get("payload", {}), merchant, customer)
            send_as = "merchant_on_behalf"
            template_name = "merchant_customer_reminder_v1"
        elif scope == "merchant":
            composed = _compose_merchant(kind, trigger.get("payload", {}), merchant, category)
            send_as = "vera"
            template_name = "vera_merchant_nudge_v1"
            customer_id = None
        else:
            continue

        if not composed:
            continue
        body, cta = composed
        if not body.strip() or "http://" in body.lower() or "https://" in body.lower():
            continue

        suppression_key = str(trigger.get("suppression_key") or f"trigger:{trigger_id}")
        digest = hashlib.sha256(
            f"{trigger_id}:{trigger_version}:{merchant_record.version}".encode("utf-8")
        ).hexdigest()[:20]
        conversation_id = f"conv_{digest}"
        conversation = Conversation(
            conversation_id=conversation_id,
            merchant_id=merchant_id,
            customer_id=customer_id,
            trigger_id=trigger_id,
            last_body=body,
            turn_count=1,
        )
        if not store.reserve_action(suppression_key, conversation):
            continue

        actions.append(
            {
                "conversation_id": conversation_id,
                "merchant_id": merchant_id,
                "customer_id": customer_id,
                "send_as": send_as,
                "trigger_id": trigger_id,
                "template_name": template_name,
                "template_params": [body],
                "body": body,
                "cta": cta,
                "suppression_key": suppression_key,
                "rationale": (
                    f"Used {kind} trigger v{trigger_version}, merchant v{merchant_record.version}, "
                    f"and category v{category_record.version}; required joins and consent passed."
                ),
            }
        )

    return actions
