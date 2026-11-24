# RB-02: Missing acknowledgement (MDN, 997 or CONTRL)

| | |
| --- | --- |
| **Failure codes** | `AS2_MDN_NOT_DELIVERED` (P2), `ACK_NOT_SENT` (P3) |
| **Replay?** | `ACK_NOT_SENT`: the acknowledgement can be regenerated. `AS2_MDN_NOT_DELIVERED`: **no.** The document was already processed, so a replay would create a duplicate. |

## Symptoms

- The partner says "we never got a receipt" or "we never got a 997", and may **resend the same document**.
- In IntegrX the transaction is `PROCESSED`, but its timeline shows `mdn_failed` or `ack_failed`.
- Duplicate business keys appear: the same PO or shipment number in two transactions within a short time.

## Why it matters

The document was processed correctly, but the partner has no proof of that. Their system will usually retry. Without a duplicate check, the same order or shipment is processed twice.

## Likely causes

1. **Asynchronous MDN:** the partner's MDN URL is down, unreachable through their firewall, or wrong in their profile.
2. **997/CONTRL:** our outbox or the partner's SFTP `download` folder is not writable (disk full, permissions), or the ack format is set to `none` for that partner.
3. The partner expects a **synchronous** MDN but requested an asynchronous one, or the reverse (profile mismatch).

## Diagnose

```bash
scripts/analyze_transaction.sh TXN-2026-000020            # look for mdn_failed / ack_failed and the error text
curl -s -H "X-Admin-Key: $INTEGRX_ADMIN_KEY" localhost:8080/api/v1/admin/faults | jq .   # demo fault switches
ls -la data/outbox/ACME01/ data/sftp/acme01/download/    # was a 997 written?
df -h data/                                               # disk full?
# Async MDN target reachable from our side?
curl -sv --max-time 10 -X POST https://partner.example/as2/mdn -o /dev/null
# Duplicates caused by partner retries
curl -s -H "X-Admin-Key: $INTEGRX_ADMIN_KEY" "localhost:8080/api/v1/transactions?business_key=4500012" | jq -r '.[] | "\(.txn_id) \(.status) \(.created_at)"'
```

## Resolve

1. **MDN URL down:** send the partner the transaction list with times, and ask them to fix the MDN endpoint or switch to synchronous MDNs. Tell them explicitly **not to resend**, because the documents were processed.
2. **997 not written:** fix the cause (free disk space, fix folder permissions, turn off the demo fault with `scripts/inject_fault.sh ack-ok`). Regenerate the acknowledgement without reprocessing the document, then confirm it appears in `data/outbox/<partner>/` and the partner's `download/` folder:
   ```bash
   curl -s -X POST localhost:8080/api/v1/transactions/TXN-2026-000020/resend-ack -H "X-Admin-Key: $INTEGRX_ADMIN_KEY" | jq .
   ```
3. If the partner has already resent: confirm the downstream system de-duplicated using the `Idempotency-Key` header. If not, tell the order team which duplicate to cancel.

## Verify

- The next transaction from the partner shows `mdn_sent` or `ack_sent` in its timeline.
- The partner confirms they received receipts and their retry queue is empty.

## Escalate when

- Duplicates reached the order system and orders may ship twice. Escalate to the order operations team immediately.
- Acknowledgement failures affect more than one partner. That points to a platform problem (disk, permissions, network).
