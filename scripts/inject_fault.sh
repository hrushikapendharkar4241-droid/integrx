#!/usr/bin/env bash
# inject_fault.sh FAULT
# Break things on purpose, then practise the runbook. Each fault prints how to undo it.
set -euo pipefail
API="${INTEGRX_URL:-http://localhost:8080}"
ORDER="${ORDER_SERVICE_URL:-http://localhost:8090}"
KEY="${INTEGRX_ADMIN_KEY:-change-me-admin-key}"
PD="${PAYLOAD_DOCTOR_SAMPLES:-../payload-doctor/samples}"
cd "$(dirname "$0")/.."

mode()  { curl -fs -X POST "$ORDER/admin/mode" -H 'Content-Type: application/json' -d "{\"mode\":\"$1\"}" | jq -c .; }
fault() { curl -fs -X POST "$API/api/v1/admin/faults" -H "X-Admin-Key: $KEY" -H 'Content-Type: application/json' \
            -d "{\"name\":\"$1\",\"on\":$2}" | jq -c .; }

case "${1:-}" in
  downstream-timeout) mode slow;     echo "order-service now hangs. Undo: $0 downstream-ok" ;;
  downstream-500)     mode error500; echo "order-service now returns 500. Undo: $0 downstream-ok" ;;
  downstream-401)     mode auth401;  echo "order-service now rejects our token. Undo: $0 downstream-ok" ;;
  downstream-ok)      mode normal ;;
  ack-failure)        fault ack_not_sent true;  echo "997/CONTRL writes fail. Undo: $0 ack-ok" ;;
  ack-ok)             fault ack_not_sent false ;;
  mdn-down)           fault mdn_delivery_down true; echo "async MDNs cannot be delivered. Undo: $0 mdn-ok" ;;
  mdn-ok)             fault mdn_delivery_down false ;;
  as2-cert-expired)   python scripts/setup_certs.py --expire "${2:-ACME01}"
                      echo "Send an AS2 message: python partner_sim/partners.py as2 ${2:-ACME01} $PD/good/po_850.edi"
                      echo "Fix (RB-01): python scripts/setup_certs.py --renew ${2:-ACME01}" ;;
  sftp-wrong-password) for i in 1 2 3; do
                        python partner_sim/partners.py sftp "${2:-RETAILCO}" "$PD/good/inventory.csv" --password wrong || true
                      done; echo "Partner-side login failures logged (RB-03)." ;;
  bad-payloads)       for f in "$PD"/bad/*; do python partner_sim/partners.py rest ACME01 "$f" >/dev/null; done
                      echo "Sent $(ls "$PD"/bad | wc -l) bad files from ACME01 over REST." ;;
  *) cat <<USAGE
usage: $0 FAULT
  downstream-timeout | downstream-500 | downstream-401 | downstream-ok
  ack-failure | ack-ok | mdn-down | mdn-ok
  as2-cert-expired [PARTNER] | sftp-wrong-password [PARTNER] | bad-payloads
USAGE
     exit 3 ;;
esac
