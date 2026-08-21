# Vera Engagement Bot

A small deterministic API for the magicpin AI challenge. It accepts versioned category, merchant, customer, and trigger context; joins those records safely; and creates a concise action only when the required facts are available.

The first version deliberately uses rules rather than an LLM. That keeps the output reproducible, makes every fact traceable to supplied context, and avoids fabricated offers, metrics, or customer details.

## What is implemented

- `POST /v1/context` — stores a new context or replaces it with a higher version
- `POST /v1/tick` — ranks available triggers and returns up to 20 actions
- `POST /v1/reply` — handles commitment, delay, auto-replies, questions, and opt-out
- `GET /v1/healthz` — reports uptime and live context counts
- `GET /v1/metadata` — reports submission metadata and approach

The proactive flow is:

```text
trigger id
  -> stored trigger
  -> merchant
  -> category
  -> customer + consent (customer-scoped triggers only)
  -> deterministic formatter
  -> suppression reservation
  -> action
```

If a required join, consent, expiry, or supported payload is missing, the service sends nothing. That is safer than producing plausible but unsupported copy.

## Run locally

Requirements: Python 3.13 or Docker.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/docs` for the generated API explorer.

## Configuration

Metadata can be changed without editing code:

| Variable | Default |
|---|---|
| `TEAM_NAME` | `magicpin` |
| `TEAM_MEMBER` | `magicpin` |
| `CONTACT_EMAIL` | `vera@magicpin.com` |
| `SUBMITTED_AT` | `2026-08-21T00:00:00Z` |
| `PORT` | `8000` in Docker |

Before submission, replace the defaults with your real details. Do not commit secrets; this service needs no API key.

## Key behavior

- Context keys are `(scope, context_id)`.
- A higher version replaces the complete old payload.
- An equal or lower version returns HTTP `409` and leaves state unchanged.
- Trigger priority is urgency descending, expiry ascending, then ID ascending.
- Expired, duplicate, placeholder, malformed, or unsupported triggers are skipped.
- Customer outreach requires a reachable channel, reminder opt-in, and consent appropriate to the trigger.
- First-touch actions include `template_name` and `template_params`.
- Bodies contain no URLs and use only supplied facts.
- Duplicate suppression and conversation creation happen atomically.
- Merchant opt-out blocks later Vera outreach to that merchant; customer opt-out suppresses only that customer.

## Project structure

```text
app/
  main.py       HTTP models and the five challenge endpoints
  store.py      versioned in-memory state and atomic suppression
  composer.py   joins, eligibility rules, ranking, and copy formatters
  replies.py    deterministic multi-turn state machine
Dockerfile      single-worker production image
requirements.txt
README.md
```

## Trade-offs

State is in memory because the evaluator warms the bot before ticking it and the challenge permits this approach. The deployment must therefore use exactly one worker and must not restart during evaluation. For a multi-instance production service, the same store interface should be backed by Redis or a transactional database.

The formatter supports every trigger kind in the supplied seed data. Unknown kinds are suppressed rather than guessed. This is an intentional first-flow boundary, not an error.
