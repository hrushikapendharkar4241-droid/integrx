"""IntegrX HTTP API: REST intake, AS2 endpoint, support endpoints (timeline, replay, incidents) and console."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text

from . import archive, classifier, faults
from .ai_triage import draft as ai_draft
from .as2_gateway import receive as as2_receive
from .config import get_settings
from .db import CanonicalRecord, Incident, Transaction, get_incident, get_txn, init_db, session, utcnow
from .incidents import resolve as resolve_incident
from .logs import log
from .pipeline import _acknowledge, ingest, replay

@asynccontextmanager
async def lifespan(_app):
    init_db()
    yield


app = FastAPI(title="IntegrX", version="0.1.0", lifespan=lifespan,
              description="B2B integration troubleshooting platform: intake, tracing, diagnosis and replay.")
STATIC = Path(__file__).parent / "static"


def admin(x_admin_key: str = Header(default="")) -> str:
    if x_admin_key != get_settings().admin_key:
        raise HTTPException(401, "missing or wrong X-Admin-Key")
    return "admin"


# ---------------------------------------------------------------- intake -------------------------------------
@app.post("/api/v1/transactions", status_code=202, tags=["intake"])
async def rest_intake(request: Request, x_partner_id: str = Header(...), x_api_key: str = Header(...),
                      x_filename: str | None = Header(default=None)):
    partner = get_settings().partners().get(x_partner_id)
    if partner is None or not partner.api_key or partner.api_key != x_api_key:
        log("rest_auth_failed", f"REST intake rejected for partner {x_partner_id!r}", level="WARNING",
            partner=x_partner_id, client_ip=request.client.host if request.client else None)
        raise HTTPException(401, "invalid partner credentials")
    body = await request.body()
    if not body:
        raise HTTPException(400, "empty body")
    txn_id = ingest(body, partner.id, "REST", x_filename)
    with session() as s:
        txn = get_txn(s, txn_id)
        return {"txn_id": txn_id, "status": txn.status, "failure_code": txn.failure_code}


@app.post("/as2", tags=["intake"])
async def as2_endpoint(request: Request):
    body = await request.body()
    r = as2_receive(dict(request.headers), body)
    return Response(content=r.body, status_code=r.status_code, headers={k: v for k, v in r.headers.items()
                                                                         if k.lower() != "content-length"})


# ---------------------------------------------------------------- transactions -------------------------------
@app.get("/api/v1/transactions", tags=["support"], dependencies=[Depends(admin)])
def list_transactions(status: str | None = None, partner: str | None = None, business_key: str | None = None,
                      limit: int = 50):
    with session() as s:
        q = select(Transaction).order_by(Transaction.id.desc()).limit(min(limit, 500))
        if status:
            q = q.where(Transaction.status == status.upper())
        if partner:
            q = q.where(Transaction.partner_id == partner)
        if business_key:
            q = q.where(Transaction.business_key == business_key)
        return [t.to_dict() for t in s.scalars(q)]


@app.get("/api/v1/transactions/{txn_id}", tags=["support"], dependencies=[Depends(admin)])
def get_transaction(txn_id: str):
    with session() as s:
        txn = get_txn(s, txn_id)
        if txn is None:
            raise HTTPException(404, f"{txn_id} not found")
        d = txn.to_dict(with_events=True)
        rec = s.scalar(select(CanonicalRecord).where(CanonicalRecord.txn_id == txn_id))
        d["canonical"] = rec.body if rec else None
        if txn.failure_code:
            c = classifier.get(txn.failure_code)
            d["classification"] = {"code": c.code, "severity": c.severity, "title": c.title, "runbook": c.runbook,
                                   "replayable": c.replayable, "customer_impact": c.customer_impact}
        replays = s.scalars(select(Transaction.txn_id).where(Transaction.replay_of == txn_id)).all()
        d["replays"] = list(replays)
        return d


@app.get("/api/v1/transactions/{txn_id}/payload", tags=["support"], dependencies=[Depends(admin)])
def get_payload(txn_id: str):
    with session() as s:
        txn = get_txn(s, txn_id)
        if txn is None or not txn.archive_key:
            raise HTTPException(404, "no archived payload")
        return Response(archive.get(txn.archive_key), media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{txn.filename or txn_id}"'})


class ReplayRequest(BaseModel):
    approved_by: str = Field(min_length=2, description="Name of the engineer approving the replay")
    reason: str = Field(min_length=5)
    force: bool = Field(False, description="Replay even if the failure is classified as not replayable")


@app.post("/api/v1/transactions/{txn_id}/replay", tags=["support"], dependencies=[Depends(admin)])
def replay_transaction(txn_id: str, req: ReplayRequest):
    with session() as s:
        txn = get_txn(s, txn_id)
        if txn is None:
            raise HTTPException(404, f"{txn_id} not found")
        if txn.status not in ("FAILED",):
            raise HTTPException(409, f"{txn_id} is {txn.status}; only FAILED transactions can be replayed")
        c = classifier.get(txn.failure_code or "UNKNOWN")
        if not c.replayable and not req.force:
            raise HTTPException(409, f"{c.code} is not fixed by a replay: the partner must resend the document once "
                                     "the cause is fixed (use force=true with a reason to override)")
    log("replay_approved", f"replay of {txn_id} approved by {req.approved_by}: {req.reason}", txn_id=txn_id,
        approved_by=req.approved_by, forced=req.force)
    new_id = replay(txn_id, req.approved_by, req.reason)
    return get_transaction(new_id)


@app.post("/api/v1/transactions/{txn_id}/resend-ack", tags=["support"], dependencies=[Depends(admin)])
def resend_ack(txn_id: str):
    """Regenerate and resend the 997/CONTRL for an EDI transaction (RB-02), without reprocessing the document."""
    with session() as s:
        txn = get_txn(s, txn_id)
        if txn is None or not txn.archive_key:
            raise HTTPException(404, f"{txn_id} not found or has no archived payload")
        if txn.fmt not in ("x12", "edifact"):
            raise HTTPException(409, "acknowledgements are only sent for X12 and EDIFACT documents")
        accepted = txn.status in ("PROCESSED", "REPLAYED")
        _acknowledge(s, txn, archive.get(txn.archive_key), accepted=accepted)
        s.commit()
        last = txn.events[-1]
        return {"txn_id": txn_id, "result": last.stage, "message": last.message}


# ---------------------------------------------------------------- incidents ----------------------------------
@app.get("/api/v1/incidents", tags=["incidents"], dependencies=[Depends(admin)])
def list_incidents(status: str | None = None):
    with session() as s:
        q = select(Incident).order_by(Incident.id.desc())
        if status:
            q = q.where(Incident.status == status.upper())
        return [i.to_dict() for i in s.scalars(q)]


def _incident_or_404(s, key: str) -> Incident:
    inc = get_incident(s, key)
    if inc is None:
        raise HTTPException(404, f"{key} not found")
    return inc


@app.get("/api/v1/incidents/{key}", tags=["incidents"], dependencies=[Depends(admin)])
def get_incident_view(key: str):
    with session() as s:
        return _incident_or_404(s, key).to_dict()


class ManualIncident(BaseModel):
    failure_code: str
    partner_id: str
    detail: str = Field(min_length=5)
    reported_by: str = Field(min_length=2)


@app.post("/api/v1/incidents", status_code=201, tags=["incidents"], dependencies=[Depends(admin)])
def open_manual_incident(req: ManualIncident):
    """Open an incident for a failure that produced no file, e.g. a partner who cannot log in to SFTP."""
    if req.failure_code not in classifier.CATALOGUE:
        raise HTTPException(400, f"unknown failure_code; use one of {sorted(classifier.CATALOGUE)}")
    if req.partner_id not in get_settings().partners():
        raise HTTPException(400, f"unknown partner {req.partner_id}")
    c = classifier.get(req.failure_code)
    channel = "SFTP" if c.code.startswith("SFTP") else "AS2" if c.code.startswith("AS2") else "REST"
    txn_id = ingest(b"", req.partner_id, channel, None,
                    transport_failure=(c, f"{req.detail} (reported by {req.reported_by})"))
    with session() as s:
        return get_txn(s, txn_id).incident.to_dict()


@app.post("/api/v1/incidents/{key}/triage", tags=["incidents"], dependencies=[Depends(admin)])
def triage(key: str):
    """Ask the AI triage assistant for a draft diagnosis and customer reply. Nothing is sent."""
    with session() as s:
        inc = _incident_or_404(s, key)
        inc.ai_draft = ai_draft(inc)
        inc.draft_status = "DRAFT"
        inc.approved_by = None
        inc.approved_at = None
        s.commit()
        return inc.to_dict()


class Approval(BaseModel):
    approver: str = Field(min_length=2)
    customer_reply: str | None = Field(None, description="Edited reply text; omit to approve the draft as written")


@app.post("/api/v1/incidents/{key}/approve", tags=["incidents"], dependencies=[Depends(admin)])
def approve(key: str, req: Approval):
    with session() as s:
        inc = _incident_or_404(s, key)
        if inc.draft_status != "DRAFT" or not inc.ai_draft:
            raise HTTPException(409, "there is no draft waiting for approval; run triage first")
        draft = dict(inc.ai_draft)
        if req.customer_reply:
            draft["customer_reply"] = req.customer_reply
            draft["edited_by_human"] = True
        inc.ai_draft = draft
        inc.draft_status = "APPROVED"
        inc.approved_by = req.approver
        inc.approved_at = utcnow()
        s.commit()
        log("draft_approved", f"{inc.key} draft approved by {req.approver}", incident=inc.key,
            edited=bool(req.customer_reply))
        return inc.to_dict()


@app.post("/api/v1/incidents/{key}/send-reply", tags=["incidents"], dependencies=[Depends(admin)])
def send_reply(key: str):
    with session() as s:
        inc = _incident_or_404(s, key)
        if inc.draft_status != "APPROVED":
            raise HTTPException(409, "the reply must be approved by a named engineer before it is sent")
        out = Path(get_settings().outbox) / "replies" / f"{inc.key}.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f"To: {inc.partner_id} integration contact\nSubject: {inc.title}\n"
                       f"Approved-By: {inc.approved_by}\n\n{inc.ai_draft['customer_reply']}\n")
        inc.draft_status = "SENT"
        s.commit()
        log("reply_sent", f"{inc.key} customer reply sent (approved by {inc.approved_by})", incident=inc.key)
        return {"incident": inc.key, "sent_to": inc.partner_id, "file": str(out)}


class Resolution(BaseModel):
    resolution: str = Field(min_length=5)
    by: str = Field(min_length=2)


@app.post("/api/v1/incidents/{key}/resolve", tags=["incidents"], dependencies=[Depends(admin)])
def resolve(key: str, req: Resolution):
    with session() as s:
        inc = _incident_or_404(s, key)
        resolve_incident(s, inc, req.resolution, req.by)
        s.commit()
        return inc.to_dict()


# ---------------------------------------------------------------- health, stats, faults ---------------------
@app.get("/api/v1/health", tags=["ops"])
def health():
    try:
        with session() as s:
            s.execute(text("SELECT 1"))
        db = "ok"
    except Exception as e:  # pragma: no cover
        db = f"error: {e}"
    return {"status": "ok" if db == "ok" else "degraded", "database": db, "faults": faults.active(),
            "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/v1/stats", tags=["ops"], dependencies=[Depends(admin)])
def stats(hours: int = 24):
    since = utcnow() - timedelta(hours=hours)
    with session() as s:
        by_status = dict(s.execute(select(Transaction.status, func.count()).where(Transaction.created_at >= since)
                                   .group_by(Transaction.status)).all())
        by_failure = s.execute(select(Transaction.failure_code, func.count())
                               .where(Transaction.created_at >= since, Transaction.failure_code.is_not(None))
                               .group_by(Transaction.failure_code).order_by(func.count().desc())).all()
        open_incidents = s.scalar(select(func.count()).select_from(Incident).where(Incident.status == "OPEN"))
    total = sum(by_status.values())
    ok = by_status.get("PROCESSED", 0) + by_status.get("REPLAYED", 0)
    return {"window_hours": hours, "transactions": total, "by_status": by_status,
            "success_rate": round(100 * ok / total, 1) if total else None,
            "top_failures": [{"code": c, "count": n} for c, n in by_failure], "open_incidents": open_incidents}


class FaultSwitch(BaseModel):
    name: str
    on: bool


@app.get("/api/v1/admin/faults", tags=["ops"], dependencies=[Depends(admin)])
def get_faults():
    return {"active": faults.active(), "available": faults.KNOWN}


@app.post("/api/v1/admin/faults", tags=["ops"], dependencies=[Depends(admin)])
def set_faults(req: FaultSwitch):
    try:
        state = faults.set_fault(req.name, req.on)
    except KeyError:
        raise HTTPException(400, f"unknown fault; use one of {sorted(faults.KNOWN)}")
    log("fault_switched", f"fault {req.name} turned {'ON' if req.on else 'OFF'}", level="WARNING", fault=req.name)
    return {"active": state}


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/console")


@app.get("/console", include_in_schema=False)
def console():
    return FileResponse(STATIC / "console.html")
