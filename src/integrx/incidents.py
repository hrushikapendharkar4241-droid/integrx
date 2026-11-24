"""Incidents: one per failure type per partner, deduplicated, with a Jira ticket (or an offline ticket file)."""
from __future__ import annotations

import base64
from datetime import timedelta
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .classifier import Classification
from .config import get_settings
from .db import Incident, Transaction, utcnow
from .logs import log

# Tests can replace this with an httpx.MockTransport.
jira_transport: httpx.BaseTransport | None = None


def open_or_link(s: Session, txn: Transaction, c: Classification, detail: str) -> Incident:
    """Link the transaction to an open incident of the same type for the same partner, or open a new one.

    Twenty timeouts in ten minutes are one incident with twenty transactions, not twenty tickets.
    """
    settings = get_settings()
    since = utcnow() - timedelta(minutes=settings.incident_dedupe_minutes)
    existing = s.scalar(select(Incident).where(Incident.failure_code == c.code, Incident.partner_id == txn.partner_id,
                                               Incident.status == "OPEN", Incident.created_at >= since)
                        .order_by(Incident.id.desc()))
    if existing:
        txn.incident = existing
        s.flush()
        log("incident_linked", f"{txn.txn_id} linked to {existing.key}", txn_id=txn.txn_id, incident=existing.key,
            failure_code=c.code, partner=txn.partner_id)
        return existing

    inc = Incident(failure_code=c.code, severity=c.severity, partner_id=txn.partner_id,
                   title=f"[{c.severity}] {c.code} - {txn.partner_id} - {c.title}", runbook=c.runbook)
    s.add(inc)
    s.flush()
    txn.incident = inc
    s.flush()
    inc.jira_key = create_ticket(inc, txn, c, detail)
    log("incident_opened", f"{inc.key} opened: {c.title}", level="WARNING", txn_id=txn.txn_id, incident=inc.key,
        failure_code=c.code, severity=c.severity, partner=txn.partner_id, jira=inc.jira_key)
    return inc


def _ticket_text(inc: Incident, txn: Transaction, c: Classification, detail: str) -> list[str]:
    settings = get_settings()
    runbook = f"{settings.confluence_base.rstrip('/')}/{c.runbook}" if settings.confluence_base else f"runbooks/{c.runbook}"
    return [
        f"Incident: {inc.key}   Severity: {c.severity}   Partner: {txn.partner_id}",
        f"First transaction: {txn.txn_id} ({txn.channel}, {txn.filename or 'no filename'})",
        f"Failure code: {c.code}",
        f"Customer impact: {c.customer_impact}",
        f"Evidence: {detail}",
        f"Replay after fix: {'yes' if c.replayable else 'no - partner must resend a corrected document'}",
        f"Runbook: {runbook}",
        f"Investigate: scripts/analyze_transaction.sh {txn.txn_id}",
    ]


def create_ticket(inc: Incident, txn: Transaction, c: Classification, detail: str) -> str:
    """Create a Jira issue when Jira is configured; otherwise write a Markdown ticket to data/tickets/."""
    settings = get_settings()
    lines = _ticket_text(inc, txn, c, detail)
    if settings.jira_base_url and settings.jira_email and settings.jira_api_token:
        try:
            token = base64.b64encode(f"{settings.jira_email}:{settings.jira_api_token}".encode()).decode()
            body = {"fields": {
                "project": {"key": settings.jira_project},
                "issuetype": {"name": "Task"},
                "summary": inc.title[:250],
                "labels": ["integrx", c.code.lower(), c.severity.lower()],
                "description": {"type": "doc", "version": 1, "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": line}]} for line in lines]},
            }}
            with httpx.Client(timeout=15, transport=jira_transport) as client:
                r = client.post(f"{settings.jira_base_url.rstrip('/')}/rest/api/3/issue", json=body,
                                headers={"Authorization": f"Basic {token}", "Accept": "application/json"})
            r.raise_for_status()
            return r.json()["key"]
        except Exception as e:  # Jira being down must never stop transaction processing
            log("jira_error", f"Jira ticket creation failed, writing offline ticket: {e}", level="WARNING",
                incident=inc.key)
    path = Path(settings.tickets_dir) / f"{inc.key}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {inc.title}\n\n" + "\n".join(f"- {line}" for line in lines) + "\n")
    return f"LOCAL-{inc.key}"


def resolve(s: Session, inc: Incident, resolution: str, by: str) -> Incident:
    inc.status = "RESOLVED"
    inc.resolution = f"{resolution} (resolved by {by})"
    inc.resolved_at = utcnow()
    s.flush()
    log("incident_resolved", f"{inc.key} resolved by {by}: {resolution}", incident=inc.key, failure_code=inc.failure_code)
    return inc
