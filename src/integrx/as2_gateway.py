"""AS2 receiving gateway (real AS2: S/MIME signing and encryption, signed MDN receipts) using pyas2lib."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx
from pyas2lib import Message, Organization
from pyas2lib import Partner as As2Partner

from . import classifier, faults
from .config import Partner, get_settings
from .db import get_txn, session
from .incidents import open_or_link
from .logs import log
from .pipeline import event, ingest

mdn_transport: httpx.BaseTransport | None = None  # tests inject a mock


@dataclass
class As2Response:
    status_code: int
    headers: dict
    body: bytes
    txn_id: str | None


def _org() -> Organization:
    s = get_settings()
    key = Path(s.our_as2_key).read_bytes()
    return Organization(s.our_as2_id, sign_key=key, sign_key_pass=s.our_as2_key_pass,
                        decrypt_key=key, decrypt_key_pass=s.our_as2_key_pass)


def _partner(p: Partner) -> As2Partner:
    cert = Path(p.as2_cert).read_bytes()
    return As2Partner(p.as2_id, verify_cert=cert, encrypt_cert=cert, sign=True, encrypt=True,
                      enc_alg="aes_256_cbc", mdn_mode="SYNC", mdn_digest_alg="sha256", validate_certs=True)


def _cert_detail(p: Partner) -> str:
    """Human-readable certificate status from Payload Doctor, used as incident evidence."""
    try:
        from pdoc.as2check import check_cert

        r = check_cert(Path(p.as2_cert))
        return "; ".join(f"{f.rule.id}: {f.message}" for f in r.findings if f.severity != "info") or r.summary
    except Exception as e:  # pragma: no cover - evidence only
        return f"certificate check failed: {e}"


def receive(headers: dict[str, str], body: bytes) -> As2Response:
    s = get_settings()
    h = {k.lower(): v for k, v in headers.items()}
    as2_from = h.get("as2-from", "").strip('"')
    message_id = h.get("message-id", "")
    partner = s.partner_by(as2_id=as2_from)
    if partner is None or not partner.as2_cert:
        log("as2_rejected", f"unknown AS2-From {as2_from!r}", level="ERROR", message_id=message_id)
        return As2Response(403, {}, b"Unknown AS2 partner", None)

    raw = b"".join(f"{k}: {v}\r\n".encode() for k, v in headers.items()) + b"\r\n" + body
    msg = Message()
    status, exc, mdn = msg.parse(raw, find_org_cb=lambda as2: _org() if as2 == s.our_as2_id else None,
                                 find_partner_cb=lambda as2: _partner(partner))
    payload = b""
    filename = None
    if msg.payload is not None:
        payload = msg.payload.get_payload(decode=True) or b""
        filename = msg.payload.get_filename()
    filename = filename or f"as2_{message_id.strip('<>').split('@')[0] or 'message'}.dat"
    log("as2_received", f"AS2 message {message_id} from {as2_from}: {status}", message_id=message_id,
        partner=partner.id, as2_status=status)

    if status == "processed":
        txn_id = ingest(payload, partner.id, "AS2", filename)
    else:
        detail = str(exc[0]) if exc and exc[0] else str(status)
        if "Partner Certificate Invalid" in detail:  # pyas2lib truncates the OpenSSL reason; say it plainly
            detail = "partner certificate failed verification"
        c = classifier.from_as2(detail)
        if c.code == "AS2_CERT_INVALID":
            detail = f"{detail} ({_cert_detail(partner)})"
        txn_id = ingest(payload, partner.id, "AS2", filename, transport_failure=(c, f"AS2 {status}: {detail}"))

    if mdn is None:
        return As2Response(200, {}, b"", txn_id)
    mdn_headers = dict(mdn.headers)
    async_url = h.get("receipt-delivery-option")
    if async_url:
        _deliver_async_mdn(txn_id, partner, async_url, mdn_headers, mdn.content)
        return As2Response(200, {}, b"", txn_id)
    _note_mdn(txn_id, "MDN returned synchronously")
    return As2Response(200, mdn_headers, mdn.content, txn_id)


def _note_mdn(txn_id: str, message: str, level: str = "INFO") -> None:
    with session() as s:
        txn = get_txn(s, txn_id)
        event(s, txn, "mdn_sent" if level == "INFO" else "mdn_failed", message, level=level)
        s.commit()


def _deliver_async_mdn(txn_id: str, partner: Partner, url: str, headers: dict, content: bytes) -> None:
    try:
        if faults.is_on("mdn_delivery_down"):
            raise httpx.ConnectError("fault 'mdn_delivery_down' is active")
        with httpx.Client(timeout=15, transport=mdn_transport) as client:
            r = client.post(url, headers=headers, content=content)
        r.raise_for_status()
        _note_mdn(txn_id, f"asynchronous MDN delivered to {url}")
    except Exception as e:
        with session() as s:
            txn = get_txn(s, txn_id)
            event(s, txn, "mdn_failed", f"asynchronous MDN to {url} failed: {e}", level="ERROR")
            open_or_link(s, txn, classifier.get("AS2_MDN_NOT_DELIVERED"), f"MDN delivery to {url} failed: {e}")
            s.commit()
