# Postmortem PM-001: Serialized shipment rejected because of a duplicate serial number

> **Exercise.** This postmortem describes a failure reproduced in the IntegrX lab with synthetic data: `scripts/inject_fault.sh bad-payloads`, sample `epcis_duplicate_serial.xml`. The partner, product and quantities are invented. The structure is the one used for real incidents.

| | |
| --- | --- |
| **Incident** | INC-00001, `DUPLICATE_SERIAL`, P2 |
| **Partner** | ACME01 (synthetic) |
| **Transaction** | TXN-2026-000004, EPCIS 1.2 shipment for PO 4500012 |
| **Detected by** | Payload Doctor rule EPC-004 during validation, which opened an incident automatically |
| **Customer impact** | The receiving warehouse could not verify the shipment. In a real network the goods would be held at the dock until corrected data arrived. |
| **Status** | Resolved. The partner resent corrected data. |

## Summary

ACME01 sent an EPCIS shipment in which serial number `SN00003` (GTIN 10614141073464) was commissioned twice, in two separate commissioning events. IntegrX rejected the document at validation, before it reached the order system, and opened a P2 incident. The partner's serialization system had re-sent a commissioning event for one unit after a label reprint, without decommissioning the original.

## Timeline (UTC)

| Time | Event |
| --- | --- |
| 07:45:24 | TXN-2026-000004 received over REST from ACME01 (epcis_duplicate_serial.xml) |
| 07:45:24 | Validation failed: EPC-004, `urn:epc:id:sgtin:0614141.107346.SN00003 is commissioned 2 times` |
| 07:45:24 | Classified `DUPLICATE_SERIAL` (P2); INC-00001 opened; ticket written |
| 07:46:49 | Customer ticket: "File rejected? We sent a file at 07:45 and got no response" |
| +5 min | Engineer found the transaction from file name and time; `pdoc check` and `pdoc diff` against the last good file isolated the extra commissioning event |
| +12 min | AI triage drafted a reply; the engineer edited it to name the serial and approved it; reply sent |
| +40 min | Partner confirmed a label reprint; sent corrected EPCIS with one commissioning event per unit |
| +41 min | Corrected document processed; incident resolved |

## Root cause: five whys

1. **Why was the shipment rejected?** The EPCIS document commissioned serial SN00003 twice.
2. **Why was it commissioned twice?** The packaging line reprinted a damaged label and the reprint created a new commissioning event.
3. **Why did a reprint create a new commissioning event?** The partner's line software treats "print" and "commission" as one step.
4. **Why was the earlier event not decommissioned or corrected?** No rule on their side checks for an already-commissioned serial before sending.
5. **Why did this reach us instead of being caught at the source?** The partner only validates EPCIS against the XSD, which cannot detect duplicates across events.

**Root cause:** the partner's serialization process re-commissions on reprint, and their pre-send validation does not check uniqueness across events.

## What went well

- The failure was caught **before** the order system, so no inventory was created for unverifiable product.
- The classification (P2, not replayable, partner must resend) was correct, which stopped anyone from retrying a document that would fail again.
- `pdoc diff` against the last good file showed the extra event in seconds.

## What went poorly

- The partner learned about the rejection from silence, not from us. EPCIS documents get no functional acknowledgement in the current setup, unlike X12 with its 997.
- The ticket gave a file name and time but no transaction ID. Finding the transaction took about 5 minutes.

## Action items

| # | Action | Owner | Type |
| --- | --- | --- | --- |
| 1 | Send an automatic rejection notice to the partner for failed EPCIS documents, as already happens with 997s for X12 | IntegrX team | Detect faster |
| 2 | Add transaction ID and PO number to every partner-facing message so tickets can reference them | IntegrX team | Speed up diagnosis |
| 3 | Partner to add a uniqueness check across events before sending | ACME01 | Prevent |
| 4 | Add a "recently rejected for duplicates" view to the console so repeated issues are visible per partner | IntegrX team | Detect patterns |
| 5 | Add this case to the regression tests (done: `tests/test_pipeline.py::test_epcis_duplicate_serial_is_p2`) | IntegrX team | Prevent regression |
