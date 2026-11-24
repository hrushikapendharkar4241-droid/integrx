"""AS2 (real S/MIME + MDN), SFTP watcher, incidents workflow, AI triage governance, Jira, S3 archive."""
import json
from pathlib import Path

import httpx
from conftest import ADMIN, post, sample
from pyas2lib import Mdn

from partner_sim.partners import build_as2


def as2_post(client, env, partner, rel, async_mdn=None):
    key = env["keys"][partner.lower()][0]
    msg = build_as2(partner, sample(rel), Path(rel).name, async_mdn, key=key, receiver_cert=env["keys"]["integrx"][1])
    r = client.post("/as2", content=msg.content, headers=dict(msg.headers))
    return msg, r


def parse_mdn(msg, r):
    raw = b"".join(f"{k}: {v}\r\n".encode() for k, v in r.headers.items()) + b"\r\n" + r.content
    return Mdn().parse(raw, find_message_cb=lambda mid, rcv: msg)


def latest(client, **params):
    return client.get("/api/v1/transactions", headers=ADMIN, params={"limit": 1, **params}).json()[0]


def test_as2_roundtrip_with_signed_mdn(client, env):
    msg, r = as2_post(client, env, "ACME01", "good/po_850.edi")
    assert r.status_code == 200
    status, detail = parse_mdn(msg, r)
    assert status == "processed", detail
    t = latest(client, partner="ACME01")
    assert t["channel"] == "AS2" and t["status"] == "PROCESSED" and t["filename"] == "po_850.edi"


def test_as2_expired_partner_certificate(client, env):
    msg, r = as2_post(client, env, "MEDILINE", "good/po_850.edi")
    status, detail = parse_mdn(msg, r)
    assert status.startswith("failed") and "certificate" in (detail or "")
    t = latest(client, partner="MEDILINE")
    assert t["failure_code"] == "AS2_CERT_INVALID" and "AS2-001" in t["failure_detail"]
    inc = client.get(f"/api/v1/incidents/{t['incident']}", headers=ADMIN).json()
    assert inc["severity"] == "P1" and inc["runbook"] == "RB-01-as2-certificate-expiry.md"


def test_async_mdn_delivery_failure(client, env):
    client.post("/api/v1/admin/faults", headers=ADMIN, json={"name": "mdn_delivery_down", "on": True})
    try:
        _, r = as2_post(client, env, "ACME01", "good/asn_856.edi", async_mdn="http://partner.test/mdn")
        assert r.status_code == 200 and r.content == b""
        t = latest(client, partner="ACME01")
        assert t["status"] == "PROCESSED"  # the document itself was fine...
        inc = client.get(f"/api/v1/incidents/{t['incident']}", headers=ADMIN).json()
        assert inc["failure_code"] == "AS2_MDN_NOT_DELIVERED"  # ...but the partner never got a receipt
    finally:
        client.post("/api/v1/admin/faults", headers=ADMIN, json={"name": "mdn_delivery_down", "on": False})


def test_async_mdn_delivered(client, env):
    from integrx import as2_gateway

    got = []
    as2_gateway.mdn_transport = httpx.MockTransport(lambda req: got.append(req) or httpx.Response(200))
    try:
        as2_post(client, env, "ACME01", "good/orders.edifact", async_mdn="http://partner.test/mdn")
        t = client.get(f"/api/v1/transactions/{latest(client, partner='ACME01')['txn_id']}", headers=ADMIN).json()
        assert any(e["stage"] == "mdn_sent" for e in t["timeline"]) and len(got) == 1
    finally:
        as2_gateway.mdn_transport = None


def test_unknown_as2_partner(client):
    r = client.post("/as2", content=b"x", headers={"AS2-From": "NOBODY", "AS2-To": "WHOLESALE02"})
    assert r.status_code == 403


def test_sftp_watcher_waits_for_stable_file(client, env):
    from integrx.sftp_watcher import Watcher

    upload = Path(env["tmp"], "sftp", "retailco", "upload")
    upload.mkdir(parents=True, exist_ok=True)
    (upload / "inv.csv").write_bytes(sample("good/inventory.csv"))
    w = Watcher()
    assert w.poll() == []  # first sighting: could still be uploading
    done = w.poll()
    assert len(done) == 1 and not (upload / "inv.csv").exists()
    assert Path(env["tmp"], "sftp", "retailco", "processed", f"{done[0]}_inv.csv").exists()
    t = client.get(f"/api/v1/transactions/{done[0]}", headers=ADMIN).json()
    assert t["channel"] == "SFTP" and t["doc_type"] == "inventory" and t["status"] == "PROCESSED"


def test_manual_sftp_auth_incident(client):
    r = client.post("/api/v1/incidents", headers=ADMIN, json={
        "failure_code": "SFTP_AUTH_FAILURE", "partner_id": "RETAILCO",
        "detail": "partner reports 'Permission denied' since 09:10", "reported_by": "Hrushika"})
    assert r.status_code == 201 and r.json()["runbook"] == "RB-03-sftp-authentication-failure.md"


def test_triage_requires_human_approval(client):
    t = post(client, "ACME01", "k-acme", "bad/epcis_no_owner.xml").json()
    key = client.get(f"/api/v1/transactions/{t['txn_id']}", headers=ADMIN).json()["incident"]
    assert client.post(f"/api/v1/incidents/{key}/send-reply", headers=ADMIN).status_code == 409
    assert client.post(f"/api/v1/incidents/{key}/approve", headers=ADMIN, json={"approver": "Hrushika"}).status_code == 409
    d = client.post(f"/api/v1/incidents/{key}/triage", headers=ADMIN).json()
    assert d["draft_status"] == "DRAFT" and d["ai_draft"]["generated_by"] == "rules"
    assert client.post(f"/api/v1/incidents/{key}/send-reply", headers=ADMIN).status_code == 409
    a = client.post(f"/api/v1/incidents/{key}/approve", headers=ADMIN,
                    json={"approver": "Hrushika", "customer_reply": "Edited by a human."}).json()
    assert a["approved_by"] == "Hrushika" and a["ai_draft"]["edited_by_human"] is True
    sent = client.post(f"/api/v1/incidents/{key}/send-reply", headers=ADMIN).json()
    assert "Edited by a human." in Path(sent["file"]).read_text()
    res = client.post(f"/api/v1/incidents/{key}/resolve", headers=ADMIN,
                      json={"resolution": "Partner resent with owning party", "by": "Hrushika"}).json()
    assert res["status"] == "RESOLVED"


def test_gemini_draft_never_sees_payload(client, monkeypatch):
    from integrx import ai_triage
    from integrx.config import get_settings

    seen = []

    def handler(req):
        seen.append(req.content.decode())
        reply = {"summary": "s", "likely_root_cause": "c", "evidence": [], "recommended_actions": ["a"],
                 "customer_reply": "Hello", "confidence": "high"}
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(reply)}]}}]})

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    get_settings.cache_clear()
    ai_triage.transport = httpx.MockTransport(handler)
    try:
        t = post(client, "ACME01", "k-acme", "bad/850_bad_counts.edi").json()
        key = client.get(f"/api/v1/transactions/{t['txn_id']}", headers=ADMIN).json()["incident"]
        d = client.post(f"/api/v1/incidents/{key}/triage", headers=ADMIN).json()
        assert d["ai_draft"]["generated_by"].startswith("gemini:")
        assert "PHARMA-01" not in seen[0] and "Hinjewadi" not in seen[0]  # payload contents are not sent
    finally:
        ai_triage.transport = None
        monkeypatch.delenv("GEMINI_API_KEY")
        get_settings.cache_clear()


def test_gemini_failure_falls_back_to_rules(client, monkeypatch):
    from integrx import ai_triage
    from integrx.config import get_settings

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    get_settings.cache_clear()
    ai_triage.transport = httpx.MockTransport(lambda req: httpx.Response(429, text="quota"))
    try:
        t = post(client, "ACME01", "k-acme", "bad/850_truncated.edi").json()
        key = client.get(f"/api/v1/transactions/{t['txn_id']}", headers=ADMIN).json()["incident"]
        d = client.post(f"/api/v1/incidents/{key}/triage", headers=ADMIN).json()
        assert d["ai_draft"]["generated_by"] == "rules"
    finally:
        ai_triage.transport = None
        monkeypatch.delenv("GEMINI_API_KEY")
        get_settings.cache_clear()


def test_jira_ticket_created(client, monkeypatch):
    from integrx import incidents
    from integrx.config import get_settings

    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        assert req.url.path == "/rest/api/3/issue" and req.headers["Authorization"].startswith("Basic ")
        return httpx.Response(201, json={"key": "SUP-42"})

    for k, v in {"JIRA_BASE_URL": "https://example.atlassian.net", "JIRA_EMAIL": "me@example.com",
                 "JIRA_API_TOKEN": "t", "JIRA_PROJECT_KEY": "SUP"}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    incidents.jira_transport = httpx.MockTransport(handler)
    try:
        t = post(client, "ACME01", "k-acme", "bad/inventory_bad.csv").json()
        key = client.get(f"/api/v1/transactions/{t['txn_id']}", headers=ADMIN).json()["incident"]
        inc = client.get(f"/api/v1/incidents/{key}", headers=ADMIN).json()
        assert inc["jira_key"] == "SUP-42"
        assert bodies[0]["fields"]["project"]["key"] == "SUP" and bodies[0]["fields"]["description"]["type"] == "doc"
    finally:
        incidents.jira_transport = None
        for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "JIRA_PROJECT_KEY"):
            monkeypatch.delenv(k)
        get_settings.cache_clear()


def test_s3_archive(monkeypatch):
    import boto3
    from moto import mock_aws

    from integrx import archive
    from integrx.config import get_settings

    with mock_aws():
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        boto3.client("s3").create_bucket(Bucket="integrx-archive")
        monkeypatch.setenv("INTEGRX_ARCHIVE", "s3://integrx-archive")
        get_settings.cache_clear()
        try:
            ref = archive.put("ACME01/x/po.edi", b"ISA*demo")
            assert ref == "s3://integrx-archive/ACME01/x/po.edi" and archive.get(ref) == b"ISA*demo"
        finally:
            monkeypatch.delenv("INTEGRX_ARCHIVE")
            get_settings.cache_clear()


def test_watcher_recovers_files_stuck_in_processing(env):
    from integrx.sftp_watcher import recover_stuck

    stuck = Path(env["tmp"], "sftp", "retailco", "processing")
    stuck.mkdir(parents=True, exist_ok=True)
    (stuck / "left_behind.csv").write_text("a,b\n1,2\n")
    assert recover_stuck() == 1
    assert Path(env["tmp"], "sftp", "retailco", "upload", "left_behind.csv").exists()
    (Path(env["tmp"], "sftp", "retailco", "upload", "left_behind.csv")).unlink()
