# Shift handoff: IST to US East

**From:** Hrushika (IST shift, 09:30-18:30 IST)  **To:** US East on-call  **Written:** 24 Nov 2026, 18:25 IST (07:55 ET)

> Lab exercise based on the IntegrX demo environment. Partners and numbers are synthetic.

## Summary

Two P2s are still open, one P1 was resolved during the shift, and nothing needs action in the next hour. The main risk overnight is **RETAILCO**: two documents failed on order-service timeouts and are waiting for a replay once order-service is confirmed stable.

## Open incidents

| Incident | Sev | Partner | What happened | Done so far | Next step for you |
| --- | --- | --- | --- | --- | --- |
| INC-00002 | P2 | RETAILCO | order-service timed out (3 attempts each) for TXN-...-000005 and -000007 | -000005 replayed OK as -000008 after order-service recovered. -000007 (inventory file via SFTP) not yet replayed. | Check `curl localhost:8090/health`. If OK, replay -000007 per RB-05 with your name and reason, then resolve. |
| INC-00003 | P2 | ACME01 | Same order-service timeout window, TXN-...-000006 (ASN 4500012) | Not replayed. ACME01 has not chased it yet. | Replay together with -000007. ASN 4500012 is for a delivery tomorrow, so replay before 14:00 ET. |
| INC-00001 | P2 | ACME01 | Duplicate serial in EPCIS shipment (EPC-004) | Approved reply sent asking for corrected data. See PM-001 draft. | No action unless ACME01 resends. When they do, confirm `PROCESSED` and resolve. **Do not replay**: the data is wrong. |

## Resolved this shift

- **INC-00004, P1, ACME01, AS2 certificate expired.** ACME01's certificate expired on 23 Nov. All their AS2 messages were rejected with MDN failed. The renewed certificate was installed and its fingerprint verified by phone. ACME01 resent; MDN processed. RB-01 followed; no replay, by design.

## Watch overnight

- **order-service:** two timeout windows today, at 07:45 and 07:46 UTC. If timeouts come back, page the order-service on-call. Do not replay in a loop.
- **Certificates:** `scripts/health_check.sh` shows all AS2 certificates valid for 700+ days after the renewal.
- **Fault switches:** none are on. `GET /api/v1/health` shows `"faults": {}`.

## Useful commands

```bash
scripts/analyze_transaction.sh TXN-2026-000007
scripts/find_errors.sh 2026-11-24 logs --summary
curl -s -H "X-Admin-Key: $INTEGRX_ADMIN_KEY" "localhost:8080/api/v1/incidents?status=OPEN" | jq -r '.[] | "\(.key) \(.severity) \(.title)"'
```

Reachable on Slack until 19:30 IST for questions about INC-00001.
