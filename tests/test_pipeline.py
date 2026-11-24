"""End-to-end tests through the HTTP API: intake, validation, transform, delivery, acks, incidents, replay."""
import json
from pathlib import Path

from conftest import ADMIN, post, sample
from pdoc import check_bytes
from pdoc.trace import summarise, trace


def txn(client, txn_id):
    r = client.get(f"/api/v1/transactions/{txn_id}", headers=ADMIN)
    assert r.status_code == 200
    return r.json()


def test_good_850_is_processed_and_acknowledged(client, downstream, env):
    r = post(client, "ACME01", "k-acme", "good/po_850.edi")
    assert r.status_code == 202 and r.json()["status"] == "PROCESSED"
    t = txn(client, r.json()["txn_id"])
    stages = [e["stage"] for e in t["timeline"]]
    assert stages == ["received", "archived", "validated", "transformed", "delivering", "delivered", "processed", "ack_sent"]
    assert t["canonical"]["po_number"] == "4500012" and len(t["canonical"]["lines"]) == 2
    assert downstream.received[-1]["doc_type"] == "order"
    ack = Path(env["tmp"], "outbox", "ACME01", f"{t['txn_id']}_997.edi").read_bytes()
    result = check_bytes(ack)  # our own 997 must pass Payload Doctor cleanly
    assert result.findings == [] and "997" in result.summary
    assert Path(env["tmp"], "sftp", "acme01", "download", f"{t['txn_id']}_997.edi").exists()


def test_invalid_850_fails_with_rejected_997_and_incident(client, env):
    r = post(client, "ACME01", "k-acme", "bad/850_missing_shipto.edi")
    t = txn(client, r.json()["txn_id"])
    assert t["status"] == "FAILED" and t["failure_code"] == "EDI_MAPPING_ERROR"
    assert t["classification"]["replayable"] is False
    assert "X12-014" in t["failure_detail"]
    ack = Path(env["tmp"], "outbox", "ACME01", f"{t['txn_id']}_997.edi").read_text()
    assert "AK3*N1*7" in ack and "AK9*R" in ack
    inc = client.get(f"/api/v1/incidents/{t['incident']}", headers=ADMIN).json()
    assert inc["severity"] == "P3" and inc["jira_key"].startswith("LOCAL-")
    assert Path(env["tmp"], "tickets", f"{inc['key']}.md").exists()


def test_epcis_duplicate_serial_is_p2(client):
    r = post(client, "ACME01", "k-acme", "bad/epcis_duplicate_serial.xml")
    t = txn(client, r.json()["txn_id"])
    assert t["failure_code"] == "DUPLICATE_SERIAL" and t["classification"]["severity"] == "P2"


def test_epcis_and_edifact_canonical(client):
    t = txn(client, post(client, "ACME01", "k-acme", "good/epcis_shipment.xml").json()["txn_id"])
    item = t["canonical"]["items"][0]
    assert t["status"] == "PROCESSED" and item["gtin"] == "10614141073464" and item["qty"] == 5
    assert item["lot"] == "LOT2611A" and t["canonical"]["po_number"] == "4500012"
    t = txn(client, post(client, "ACME01", "k-acme", "good/orders.edifact").json()["txn_id"])
    assert t["canonical"]["po_number"] == "4500013" and t["canonical"]["ship_to"]["id"] == "PNQ-WH1"
    assert any(e["stage"] == "ack_sent" for e in t["timeline"])


def test_downstream_timeout_retry_incident_dedupe_and_replay(client, downstream, env):
    downstream.mode = "slow"
    first = post(client, "RETAILCO", "k-retail", "good/po_850.edi").json()
    second = post(client, "RETAILCO", "k-retail", "good/asn_856.edi").json()
    t1, t2 = txn(client, first["txn_id"]), txn(client, second["txn_id"])
    assert t1["failure_code"] == "DOWNSTREAM_TIMEOUT" and t1["attempts"] == 3
    assert [e["stage"] for e in t1["timeline"]].count("delivery_retry") == 2
    assert t1["incident"] == t2["incident"]  # deduplicated: one incident, two transactions

    # The JSON log is readable by Payload Doctor's trace command.
    events = trace(first["txn_id"], [Path(env["tmp"], "logs")])
    assert summarise(events)["status"] == "FAILED" and summarise(events)["retries"] == 2

    r = client.post(f"/api/v1/transactions/{first['txn_id']}/replay", headers=ADMIN, json={"approved_by": "x", "reason": "r"})
    assert r.status_code == 422  # approver name and reason are required
    downstream.mode = "normal"
    r = client.post(f"/api/v1/transactions/{first['txn_id']}/replay", headers=ADMIN,
                    json={"approved_by": "Hrushika", "reason": "order-service recovered"})
    assert r.status_code == 200 and r.json()["status"] == "PROCESSED" and r.json()["replay_of"] == first["txn_id"]
    original = txn(client, first["txn_id"])
    assert original["status"] == "REPLAYED" and original["replays"] == [r.json()["txn_id"]]


def test_replay_refused_when_partner_must_resend(client):
    t = post(client, "ACME01", "k-acme", "bad/850_negative_qty.edi").json()
    r = client.post(f"/api/v1/transactions/{t['txn_id']}/replay", headers=ADMIN,
                    json={"approved_by": "Hrushika", "reason": "trying again"})
    assert r.status_code == 409 and "partner must resend" in r.json()["detail"]


def test_downstream_auth_failure_is_not_retried(client, downstream):
    downstream.mode = "auth401"
    t = txn(client, post(client, "ACME01", "k-acme", "good/desadv.edifact").json()["txn_id"])
    assert t["failure_code"] == "DOWNSTREAM_AUTH_FAILURE" and t["attempts"] == 1


def test_auth(client):
    assert post(client, "ACME01", "wrong", "good/po_850.edi").status_code == 401
    assert client.get("/api/v1/transactions").status_code == 401
    assert client.get("/api/v1/health").json()["status"] == "ok"


def test_ack_fault(client):
    client.post("/api/v1/admin/faults", headers=ADMIN, json={"name": "ack_not_sent", "on": True})
    try:
        t = txn(client, post(client, "MEDILINE", "k-medi", "good/po_850.edi").json()["txn_id"])
        assert t["status"] == "PROCESSED" and any(e["stage"] == "ack_failed" for e in t["timeline"])
        inc = client.get(f"/api/v1/incidents/{t['incident']}", headers=ADMIN).json()
        assert inc["failure_code"] == "ACK_NOT_SENT"
    finally:
        client.post("/api/v1/admin/faults", headers=ADMIN, json={"name": "ack_not_sent", "on": False})
    r = client.post(f"/api/v1/transactions/{t['txn_id']}/resend-ack", headers=ADMIN).json()
    assert r["result"] == "ack_sent" and "accepted" in r["message"]


def test_stats(client):
    s = client.get("/api/v1/stats", headers=ADMIN).json()
    assert s["transactions"] > 0 and "FAILED" in s["by_status"] and s["top_failures"]


def test_logs_are_json_with_txn_ids(env, client):
    lines = Path(env["tmp"], "logs", "integrx.jsonl").read_text().splitlines()
    recs = [json.loads(line) for line in lines]
    assert all("@timestamp" in r and "level" in r and "stage" in r and "event" not in r for r in recs)
    assert any(r.get("txn_id") and r["stage"] == "failed" for r in recs)


def test_sample_payload_downloadable(client):
    t = post(client, "ACME01", "k-acme", "good/inventory.csv").json()
    r = client.get(f"/api/v1/transactions/{t['txn_id']}/payload", headers=ADMIN)
    assert r.content == sample("good/inventory.csv")
