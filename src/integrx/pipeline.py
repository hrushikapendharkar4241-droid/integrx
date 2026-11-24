"""The transaction pipeline: receive -> archive -> validate -> transform -> deliver -> acknowledge.

Every step writes an Event row (the timeline in the API) and a JSON log line (the timeline in Kibana).
Any failure is classified and linked to an incident.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pdoc import check_bytes
from sqlalchemy.orm import Session

from . import archive, classifier, faults
from .acks import build_997, build_contrl
from .canonical import TransformError, business_key, transform
from .config import get_settings
from .db import CanonicalRecord, Event, Transaction, get_txn, session, utcnow
from .downstream import deliver
from .incidents import open_or_link
from .logs import log


def event(s: Session, txn: Transaction, stage: str, message: str, level: str = "INFO", **data) -> None:
    s.add(Event(txn_pk=txn.id, ts=utcnow(), stage=stage, level=level, message=message, data=data or None))
    # Commit per step: support can watch a transaction in flight (e.g. stuck in "delivering"), and no
    # database lock is held while we wait on a slow downstream service.
    s.commit()
    log(stage, message, level=level, txn_id=txn.txn_id, partner=txn.partner_id, channel=txn.channel,
        filename=txn.filename, failure_code=txn.failure_code, **data)


def fail(s: Session, txn: Transaction, c: classifier.Classification, detail: str) -> None:
    txn.status = "FAILED"
    txn.failure_code = c.code
    txn.failure_detail = detail
    event(s, txn, "failed", f"{c.code}: {detail}", level="ERROR", severity=c.severity, runbook=c.runbook,
          replayable=c.replayable)
    open_or_link(s, txn, c, detail)


def ingest(data: bytes, partner_id: str, channel: str, filename: str | None = None, *,
           replay_of: str | None = None, transport_failure: tuple[classifier.Classification, str] | None = None) -> str:
    """Create a transaction for an incoming document and run it through the pipeline. Returns the TXN ID."""
    with session() as s:
        txn = Transaction(partner_id=partner_id, channel=channel, filename=filename, size_bytes=len(data),
                          replay_of=replay_of)
        s.add(txn)
        s.flush()
        txn.txn_id = f"TXN-{datetime.now(timezone.utc):%Y}-{txn.id:06d}"
        what = f"replay of {replay_of}" if replay_of else f"{filename or 'payload'} via {channel}"
        event(s, txn, "received", f"{what} from {partner_id} ({len(data)} bytes)")
        if data:
            key = f"{partner_id}/{datetime.now(timezone.utc):%Y/%m/%d}/{txn.txn_id}/{Path(filename or 'payload').name}"
            txn.archive_key = archive.put(key, data)
            event(s, txn, "archived", f"original stored at {txn.archive_key}")
        if transport_failure:
            c, detail = transport_failure
            fail(s, txn, c, detail)
        else:
            _process(s, txn, data)
        s.commit()
        return txn.txn_id


def _process(s: Session, txn: Transaction, data: bytes) -> None:
    result = check_bytes(data, txn.filename or txn.txn_id)
    txn.fmt = result.fmt
    errors = [f for f in result.findings if f.severity == "error"]
    warnings = [f for f in result.findings if f.severity == "warning"]
    findings = [f.to_dict() for f in result.findings][:20]
    if errors:
        first = errors[0]
        event(s, txn, "validation_failed", f"{len(errors)} error(s); first {first.rule.id}: {first.message}",
              level="ERROR", findings=findings, rule=first.rule.id)
        fail(s, txn, classifier.from_rule(first.rule.id),
             f"{first.rule.id} {first.rule.title}: {first.message}" + (f" ({first.location})" if first.location else ""))
        _acknowledge(s, txn, data, accepted=False, error_location=first.location)
        return
    event(s, txn, "validated", f"{result.summary}; {len(warnings)} warning(s)",
          level="WARNING" if warnings else "INFO", findings=findings or None)
    txn.status = "VALIDATED"

    try:
        doc = transform(result.fmt, data)
    except TransformError as e:
        fail(s, txn, classifier.get("TRANSFORM_ERROR"), str(e))
        return
    txn.doc_type = doc["doc_type"]
    txn.business_key = business_key(doc)
    s.add(CanonicalRecord(txn_id=txn.txn_id, doc_type=doc["doc_type"], business_key=txn.business_key, body=doc))
    event(s, txn, "transformed", f"canonical {doc['doc_type']} {txn.business_key or ''} from {doc['source_format']}".strip())
    txn.status = "TRANSFORMED"

    if doc["doc_type"] == "ack":
        txn.status = "PROCESSED"
        event(s, txn, "processed", "inbound acknowledgement recorded")
        return

    def on_attempt(a, will_retry):
        txn.attempts = a.number
        if a.ok:
            event(s, txn, "delivered", f"order-service accepted document (HTTP {a.status_code}, {a.elapsed_ms} ms)",
                  attempt=a.number)
        else:
            event(s, txn, "delivery_error", a.error or "delivery failed", level="ERROR", attempt=a.number,
                  http_status=a.status_code, timed_out=a.timed_out, elapsed_ms=a.elapsed_ms)
            if will_retry:
                event(s, txn, "delivery_retry", f"retry {a.number} of {get_settings().max_retries} scheduled",
                      level="WARNING", attempt=a.number)

    event(s, txn, "delivering", "sending canonical document to order-service")
    attempts = deliver(txn.txn_id, doc, on_attempt)
    last = attempts[-1]
    if not last.ok:
        c = classifier.from_downstream(last.status_code, last.timed_out)
        fail(s, txn, c, f"{last.error} after {len(attempts)} attempt(s)")
        return
    txn.status = "PROCESSED"
    event(s, txn, "processed", f"{doc['doc_type']} {txn.business_key or ''} processed".strip())
    _acknowledge(s, txn, data, accepted=True)


def _acknowledge(s: Session, txn: Transaction, data: bytes, accepted: bool, error_location: str | None = None) -> None:
    """Send a 997 or CONTRL back to the partner for EDI documents."""
    settings = get_settings()
    if txn.fmt not in ("x12", "edifact") or txn.doc_type == "ack":
        return
    partner = settings.partners().get(txn.partner_id)
    if partner and partner.ack_format == "none":
        return
    try:
        if faults.is_on("ack_not_sent"):
            raise OSError("outbox write failed: fault 'ack_not_sent' is active")
        if txn.fmt == "x12":
            seg = None
            if error_location and error_location.startswith("segment "):
                # "segment 7 (N1)" -> ("N1", 7)
                parts = error_location.split()
                seg = (parts[2].strip("()"), int(parts[1])) if len(parts) >= 3 else None
            ack, name = build_997(data, accepted, txn.id, settings.our_isa_id, seg), f"{txn.txn_id}_997.edi"
        else:
            ack, name = build_contrl(data, accepted, txn.id, settings.our_isa_id), f"{txn.txn_id}_CONTRL.edifact"
        targets = [Path(settings.outbox) / txn.partner_id / name]
        if partner and partner.sftp_user:
            targets.append(Path(settings.sftp_root) / partner.sftp_user / "download" / name)
        for t in targets:
            t.parent.mkdir(parents=True, exist_ok=True)
            t.write_bytes(ack)
        event(s, txn, "ack_sent", f"{'accepted' if accepted else 'rejected'} {name.split('_')[1].split('.')[0]} "
                                  f"written for {txn.partner_id}")
    except Exception as e:
        event(s, txn, "ack_failed", f"acknowledgement not sent: {e}", level="ERROR")
        open_or_link(s, txn, classifier.get("ACK_NOT_SENT"), str(e))


def replay(txn_id: str, approved_by: str, reason: str) -> str:
    """Re-run the archived original as a new transaction, keeping the failed one and its history."""
    with session() as s:
        txn = get_txn(s, txn_id)
        if txn is None:
            raise KeyError(txn_id)
        if not txn.archive_key:
            raise ValueError(f"{txn_id} has no archived payload to replay")
        data = archive.get(txn.archive_key)
        partner, channel, filename = txn.partner_id, txn.channel, txn.filename
    new_id = ingest(data, partner, channel, filename, replay_of=txn_id)
    with session() as s:
        txn = get_txn(s, txn_id)
        new = get_txn(s, new_id)
        event(s, txn, "replayed", f"replayed as {new_id} -> {new.status} (approved by {approved_by}: {reason})",
              level="INFO" if new.status == "PROCESSED" else "WARNING", replay_txn=new_id)
        if new.status == "PROCESSED":
            txn.status = "REPLAYED"
        s.commit()
    return new_id
