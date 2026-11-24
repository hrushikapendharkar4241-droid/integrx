"""AI triage assistant: drafts a diagnosis and a customer reply. A human must approve before anything is sent.

Governance rules built into this module:
  * Only error metadata is sent to the model (failure code, timeline messages, rule explanations, runbook
    excerpt). Payload contents never leave the platform.
  * The model's output is stored as a DRAFT. Sending the reply or replaying a transaction requires a named
    human approver (see api.py), and every approval is logged.
  * Without GEMINI_API_KEY, or if the call fails, a rule-based draft is produced instead, so the workflow
    never depends on the AI being available.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx

from .classifier import get as get_classification
from .config import get_settings
from .db import Incident
from .logs import log

transport: httpx.BaseTransport | None = None  # tests inject a mock

PROMPT = """You are assisting a B2B integration support engineer. Using ONLY the facts below, draft:
1. summary: one sentence on what failed.
2. likely_root_cause: the most likely cause, citing the evidence.
3. evidence: 2-4 short quotes from the timeline or findings.
4. recommended_actions: 3-5 concrete steps, following the runbook.
5. customer_reply: a short, polite email to the trading partner. No internal jargon, no blame, no promises
   about timelines that are not in the facts.
6. confidence: low, medium or high.
Reply as JSON with exactly these keys. If the facts are not enough, say so in likely_root_cause.

FACTS
{facts}
"""


def build_facts(inc: Incident) -> dict:
    c = get_classification(inc.failure_code)
    txns = inc.transactions[:5]
    timeline = []
    for t in txns[:1]:
        timeline = [f"{e.ts:%H:%M:%S} {e.level} {e.stage}: {e.message}" for e in t.events][-20:]
    runbook_path = Path(get_settings().runbooks_dir) / (inc.runbook or "")
    runbook = runbook_path.read_text()[:3000] if runbook_path.is_file() else ""
    return {
        "incident": inc.key, "severity": inc.severity, "failure_code": inc.failure_code, "title": c.title,
        "partner": inc.partner_id, "customer_impact": c.customer_impact,
        "replay_possible_after_fix": c.replayable,
        "transactions": [{"txn_id": t.txn_id, "channel": t.channel, "doc_type": t.doc_type,
                          "business_key": t.business_key, "failure_detail": t.failure_detail} for t in txns],
        "affected_transactions": len(inc.transactions), "first_transaction_timeline": timeline,
        "runbook_excerpt": runbook,
    }


def rule_based_draft(facts: dict) -> dict:
    c = get_classification(facts["failure_code"])
    first = facts["transactions"][0] if facts["transactions"] else {}
    ref = first.get("business_key") or first.get("txn_id", "")
    n = facts["affected_transactions"]
    actions = {
        "DOWNSTREAM_TIMEOUT": ["Check order-service health (curl its /health endpoint) and its recent logs",
                               "Confirm network path and latency from IntegrX to order-service",
                               "Once healthy, replay the affected transactions from the archive",
                               "Verify each replay reaches PROCESSED and the 997 is sent"],
        "AS2_CERT_INVALID": ["Run pdoc as2 on the partner certificate to confirm the expiry date",
                             "Request the renewed certificate from the partner and install it",
                             "Ask the partner to resend once the new certificate is installed (do not replay: the "
                             "original signature could not be verified)",
                             "Add the new expiry date to the certificate calendar"],
    }.get(c.code, ["Follow the runbook steps for this failure code",
                   "Confirm the root cause from the timeline evidence",
                   "Fix or request a corrected document, then verify processing"])
    return {
        "summary": f"{n} transaction(s) from {facts['partner']} failed with {c.code}: {c.title.lower()}.",
        "likely_root_cause": first.get("failure_detail") or c.title,
        "evidence": facts["first_transaction_timeline"][-3:],
        "recommended_actions": actions,
        "customer_reply": (
            f"Hello {facts['partner']} team,\n\nWe have identified an issue affecting your document {ref}. "
            f"{c.customer_impact} "
            + ("We are fixing this on our side and will reprocess the document; no action is needed from you."
               if c.replayable else "Could you please review the document and send a corrected version?")
            + "\n\nWe will confirm once it is resolved.\n\nRegards,\nIntegration Support"),
        "confidence": "medium",
        "generated_by": "rules",
    }


def draft(inc: Incident) -> dict:
    facts = build_facts(inc)
    s = get_settings()
    if not s.gemini_api_key:
        result = rule_based_draft(facts)
    else:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{s.gemini_model}:generateContent"
            body = {"contents": [{"parts": [{"text": PROMPT.format(facts=json.dumps(facts, indent=2, default=str))}]}],
                    "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}}
            with httpx.Client(timeout=30, transport=transport) as client:
                r = client.post(url, json=body, headers={"x-goog-api-key": s.gemini_api_key})
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            result = json.loads(text)
            missing = {"summary", "likely_root_cause", "recommended_actions", "customer_reply"} - set(result)
            if missing:
                raise ValueError(f"model reply missing {sorted(missing)}")
            result["generated_by"] = f"gemini:{s.gemini_model}"
        except Exception as e:
            log("ai_triage_fallback", f"AI draft failed, using rule-based draft: {e}", level="WARNING", incident=inc.key)
            result = rule_based_draft(facts)
    log("ai_triage_draft", f"{inc.key} draft created by {result['generated_by']}", incident=inc.key)
    return result
