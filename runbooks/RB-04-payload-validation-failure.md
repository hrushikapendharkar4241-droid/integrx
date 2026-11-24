# RB-04: Payload validation failure (EPCIS, X12, EDIFACT, CSV)

| | |
| --- | --- |
| **Failure codes** | `DUPLICATE_SERIAL` (P2), `AGGREGATION_MISMATCH` (P2), `PARTNER_REJECTED_ACK` (P2), `EPCIS_DATA_ERROR`, `INVALID_XML`, `EDI_ENVELOPE_ERROR`, `EDI_MAPPING_ERROR`, `CSV_DATA_ERROR`, `FILE_FORMAT_ERROR`, `TRANSFORM_ERROR` (P3) |
| **Replay?** | **No**, except `TRANSFORM_ERROR`. The data itself is wrong, so the partner must send a corrected document. `TRANSFORM_ERROR` means the data was valid but our mapping failed: fix the mapping, then replay. |

## Symptoms

- The partner says "our ASN or order was not received or not processed", or the warehouse says "the shipment data is missing".
- The transaction is `FAILED` at `validation_failed`, before transformation.
- For EDI, a **rejected** 997 (AK5*R / AK9*R) or CONTRL (action 4) was sent back to the partner automatically.

## Diagnose

```bash
# 1. Find the transaction from what the partner gives you (PO number, file name or time)
curl -s -H "X-Admin-Key: $INTEGRX_ADMIN_KEY" "localhost:8080/api/v1/transactions?business_key=4500012" | jq .
curl -s -H "X-Admin-Key: $INTEGRX_ADMIN_KEY" "localhost:8080/api/v1/transactions?status=FAILED&partner=ACME01" \
  | jq -r '.[] | "\(.txn_id) \(.created_at) \(.filename) \(.failure_code)"'

# 2. Read the diagnosis
scripts/analyze_transaction.sh TXN-2026-000004

# 3. Download the original and get the full Payload Doctor report (why / likely cause / fix for every finding)
curl -s -H "X-Admin-Key: $INTEGRX_ADMIN_KEY" localhost:8080/api/v1/transactions/TXN-2026-000004/payload -o /tmp/orig.xml
pdoc check /tmp/orig.xml

# 4. Compare with the last good file from the same partner to see exactly what changed
pdoc diff last_good.xml /tmp/orig.xml
```

## What the common findings mean

| Rule | What to tell the partner |
| --- | --- |
| EPC-004 duplicate serial | The same serial number was commissioned or shipped twice. Their serialization team must check the serial range and send corrected data. Goods may be quarantined until then. |
| EPC-005 aggregation mismatch | A case or pallet lists a unit that was never commissioned. Ask for the missing commissioning event or a corrected aggregation. |
| EPC-006 missing lot/expiry | Commissioning events need `cbvmda:lotNumber` and `cbvmda:itemExpirationDate`. |
| EPC-009 no owning party | The shipping event needs a source and destination of type `owning_party`. |
| X12-014 no ship-to | Their ERP map skipped the N1*ST loop. |
| X12-002..007 / EDI-001..004 | Envelope control numbers or counts are wrong. This is usually a translator configuration problem. |
| X12-012 test flag | ISA15 = T. Their profile is still on test. |
| CSV-005 GTIN check digit | Usually Excel dropping a leading zero. Ask them to format the column as text. |

## Resolve

1. Send the partner a clear explanation. Use the AI triage draft (`POST /api/v1/incidents/<key>/triage`), edit it, and approve it. Include the rule, the location (line or segment), and the fix from the Payload Doctor output.
2. The partner sends a corrected file. Confirm the new transaction is `PROCESSED`.
3. `TRANSFORM_ERROR` only: fix the mapping in `src/integrx/canonical.py`, add a test that uses the failing sample, deploy, then replay with an approver.

## Verify

- The corrected document's transaction is `PROCESSED`, and an **accepted** 997/CONTRL was sent.
- For pharma serialization data: the warehouse confirms they can receive and verify the shipment.

## Escalate when

- `DUPLICATE_SERIAL` or `AGGREGATION_MISMATCH` concerns product that has **already arrived** at the warehouse. Escalate to quality/compliance, because product may need to be quarantined.
- The same validation error starts for a partner who sent valid files yesterday. Something changed on their side (a map or software upgrade), so ask for their change log.
- Several partners fail the same rule at the same time. Suspect our side: a rule or mapping change in a recent deployment.
