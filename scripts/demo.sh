#!/usr/bin/env bash
# demo.sh: the 3-minute interview demo, scripted. Run with the core stack up (docker compose up -d).
#   Story: a partner's AS2 certificate expired -> their ASN is rejected -> ticket -> diagnose -> fix -> replay.
set -euo pipefail
cd "$(dirname "$0")/.."
API="${INTEGRX_URL:-http://localhost:8080}"; KEY="${INTEGRX_ADMIN_KEY:-change-me-admin-key}"
PD="${PAYLOAD_DOCTOR_SAMPLES:-../payload-doctor/samples}"
step() { echo; echo "== $*"; read -rp "   (press Enter) " _; }

step "1. Normal traffic: ACME01 sends a ship notice over AS2 and gets a signed receipt (MDN)"
python partner_sim/partners.py as2 ACME01 "$PD/good/asn_856.edi"

step "2. Fault: ACME01's AS2 certificate expires (scripts/inject_fault.sh as2-cert-expired)"
scripts/inject_fault.sh as2-cert-expired ACME01

step "3. ACME01 sends the next ASN. The MDN comes back FAILED."
python partner_sim/partners.py as2 ACME01 "$PD/good/asn_856.edi" || true

step "4. The customer emails: 'ASN never arrived'. Find the failed transaction."
TXN=$(curl -fs -H "X-Admin-Key: $KEY" "$API/api/v1/transactions?status=FAILED&partner=ACME01&limit=1" | jq -r '.[0].txn_id')
scripts/analyze_transaction.sh "$TXN"

step "5. Confirm with Payload Doctor"
pdoc as2 -b deploy/certs/acme01_cert.pem || true

step "6. Replay is refused on purpose: a message whose signature failed verification must not be trusted"
curl -s -X POST "$API/api/v1/transactions/$TXN/replay" -H "X-Admin-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"approved_by":"Hrushika","reason":"trying a replay"}' | jq -r .detail

step "7. Fix (RB-01): exchange a renewed certificate, then the partner resends and gets a processed MDN"
python scripts/setup_certs.py --renew ACME01
python partner_sim/partners.py as2 ACME01 "$PD/good/asn_856.edi"

step "8. AI triage drafts the customer reply; a human approves it before it is sent"
INC=$(curl -fs -H "X-Admin-Key: $KEY" "$API/api/v1/transactions/$TXN" | jq -r .incident)
curl -fs -X POST "$API/api/v1/incidents/$INC/triage" -H "X-Admin-Key: $KEY" | jq '.ai_draft | {summary, generated_by}'
curl -fs -X POST "$API/api/v1/incidents/$INC/approve" -H "X-Admin-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"approver":"Hrushika"}' >/dev/null
curl -fs -X POST "$API/api/v1/incidents/$INC/send-reply" -H "X-Admin-Key: $KEY" | jq .
curl -fs -X POST "$API/api/v1/incidents/$INC/resolve" -H "X-Admin-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"resolution":"Renewed ACME01 AS2 certificate; partner resent ASN; MDN processed","by":"Hrushika"}' | jq '{key, status, resolution}'
echo; echo "Done. Show the incident in the console: $API/console"
