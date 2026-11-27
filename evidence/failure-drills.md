# Failure drill evidence (2026-11-27 07:29 UTC, approver Hrushika)

| # | Failure code | Failing TXN | Fix / replay TXN | Incident | Jira | Resolved |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | INVALID_XML | TXN-2026-000001 | TXN-2026-000002 | INC-00001 | LOCAL-INC-00001 | yes |
| 2 | DUPLICATE_SERIAL | TXN-2026-000003 | TXN-2026-000004 | INC-00002 | LOCAL-INC-00002 | yes |
| 3 | AGGREGATION_MISMATCH | TXN-2026-000005 | TXN-2026-000006 | INC-00003 | LOCAL-INC-00003 | yes |
| 4 | EPCIS_DATA_ERROR | TXN-2026-000007 | TXN-2026-000008 | INC-00004 | LOCAL-INC-00004 | yes |
| 5 | EPCIS_DATA_ERROR | TXN-2026-000009 | TXN-2026-000010 | INC-00005 | LOCAL-INC-00005 | yes |
| 6 | EDI_MAPPING_ERROR | TXN-2026-000011 | TXN-2026-000012 | INC-00006 | LOCAL-INC-00006 | yes |
| 7 | EDI_ENVELOPE_ERROR | TXN-2026-000013 | TXN-2026-000014 | INC-00007 | LOCAL-INC-00007 | yes |
| 8 | EDI_ENVELOPE_ERROR | TXN-2026-000015 | TXN-2026-000016 | INC-00008 | LOCAL-INC-00008 | yes |
| 9 | EDI_ENVELOPE_ERROR | TXN-2026-000017 | TXN-2026-000018 | INC-00009 | LOCAL-INC-00009 | yes |
| 10 | CSV_DATA_ERROR | TXN-2026-000019 | TXN-2026-000020 | INC-00010 | LOCAL-INC-00010 | yes |
| 11 | AS2_CERT_INVALID | TXN-2026-000021 | TXN-2026-000022 | INC-00011 | LOCAL-INC-00011 | yes |
| 12 | AS2_MDN_NOT_DELIVERED | TXN-2026-000023 | TXN-2026-000024 | INC-00012 | LOCAL-INC-00012 | yes |
| 13 | SFTP_AUTH_FAILURE | TXN-2026-000068 | TXN-2026-000071 | INC-00009 | SUP-3 | yes (re-run 27 Nov, after the sftp mount fix) |
| 14 | DOWNSTREAM_TIMEOUT | TXN-2026-000026 | TXN-2026-000027 | INC-00014 | LOCAL-INC-00014 | yes |
| 15 | ACK_NOT_SENT | TXN-2026-000028 | TXN-2026-000028 (ack resent) | INC-00015 | LOCAL-INC-00015 | yes |

Notes:
- Drills 1-12, 14 and 15 ran before Jira was connected, so their tickets are local (data/tickets/).
- Drill 13 first run: the login worked after the credentials fix, but the sftp container was on a stale
  bind mount, so the watcher never saw the upload. Fixed with `docker compose up -d --force-recreate sftp`
  (now RB-03 step 6) and re-run with Jira connected.
