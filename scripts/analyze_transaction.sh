#!/usr/bin/env bash
# analyze_transaction.sh TXN_ID [LOG_DIR]
# One-screen summary of a transaction: where it came from, how far it got, why it failed, what to do next.
# Uses the IntegrX API; if the API is down, falls back to reading the JSON logs with Payload Doctor's trace.
set -uo pipefail
TXN="${1:?usage: analyze_transaction.sh TXN_ID [LOG_DIR]}"
LOGS="${2:-logs}"
API="${INTEGRX_URL:-http://localhost:8080}"
KEY="${INTEGRX_ADMIN_KEY:-change-me-admin-key}"

if json=$(curl -fs -H "X-Admin-Key: $KEY" "$API/api/v1/transactions/$TXN" 2>/dev/null); then
  jq -r '
    def stage_status(s): if ([.timeline[].stage] | index(s)) then "PASSED" else "-" end;
    "Transaction:   \(.txn_id)",
    "Partner:       \(.partner_id)",
    "Source:        \(.channel)   File: \(.filename // "-")",
    "Format:        \(.fmt // "-")   Document: \(.doc_type // "-") \(.business_key // "")",
    "",
    "Validation:    \(if ([.timeline[].stage] | index("validation_failed")) then "FAILED" else stage_status("validated") end)",
    "Transform:     \(stage_status("transformed"))",
    "Processing:    \(.status)",
    "Attempts:      \(.attempts)",
    (if .failure_code then
      "",
      "Error:         \(.failure_detail)",
      "RCA:           \(.classification.code) (\(.classification.severity)) - \(.classification.title)",
      "Impact:        \(.classification.customer_impact)",
      "Replay fixes:  \(if .classification.replayable then "yes, after the cause is fixed" else "no, partner must resend" end)",
      "Runbook:       runbooks/\(.classification.runbook)",
      "Incident:      \(.incident // "-")"
     else empty end),
    (if .replay_of then "Replay of:     \(.replay_of)" else empty end),
    (if (.replays | length) > 0 then "Replayed as:   \(.replays | join(", "))" else empty end),
    "",
    "Timeline:",
    (.timeline[] | "  \(.ts[11:23])  \(.level | . + "       " | .[0:7]) \(.stage): \(.message)")
  ' <<<"$json"
else
  echo "IntegrX API not reachable at $API; reading logs in $LOGS instead." >&2
  pdoc trace "$TXN" "$LOGS"
fi
