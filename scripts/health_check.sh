#!/usr/bin/env bash
# health_check.sh: API, order-service, recent failures, AS2 certificates, disk. Exit 1 if anything needs attention.
set -uo pipefail
API="${INTEGRX_URL:-http://localhost:8080}"
ORDER="${ORDER_SERVICE_URL:-http://localhost:8090}"
KEY="${INTEGRX_ADMIN_KEY:-change-me-admin-key}"
CERTS="${CERT_DIR:-deploy/certs}"
DATA="${DATA_DIR:-data}"
problems=0
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
ok()   { echo "$(ts) OK    $*"; }
bad()  { echo "$(ts) ALERT $*"; problems=1; }

if h=$(curl -fs --max-time 5 "$API/api/v1/health"); then
  [[ $(jq -r .status <<<"$h") == ok ]] && ok "api healthy" || bad "api degraded: $h"
  faults=$(jq -r '.faults | to_entries | map(select(.value)) | map(.key) | join(",")' <<<"$h")
  [[ -z "$faults" ]] || bad "fault switches ON: $faults"
else
  bad "api not reachable at $API"
fi

if o=$(curl -fs --max-time 5 "$ORDER/health"); then
  [[ $(jq -r .status <<<"$o") == ok ]] && ok "order-service healthy" || bad "order-service $(jq -r .status <<<"$o")"
else
  bad "order-service not reachable at $ORDER"
fi

if s=$(curl -fs --max-time 5 -H "X-Admin-Key: $KEY" "$API/api/v1/stats?hours=1"); then
  failed=$(jq -r '.by_status.FAILED // 0' <<<"$s"); open=$(jq -r .open_incidents <<<"$s")
  [[ "$failed" -lt 5 ]] && ok "failed transactions last hour: $failed" || bad "failed transactions last hour: $failed"
  ok "open incidents: $open"
fi

if command -v pdoc >/dev/null; then
  for c in "$CERTS"/*_cert.pem; do
    [[ -f "$c" ]] || continue
    out=$(pdoc as2 --json "$c"); code=$?
    msg=$(jq -r '.[0].summary' <<<"$out")
    [[ $code -eq 0 ]] && ok "$(basename "$c"): $msg" || bad "$(basename "$c"): $msg"
  done
fi

use=$(df -P "$DATA" 2>/dev/null | awk 'NR==2 {gsub("%","",$5); print $5}')
[[ -n "${use:-}" && "$use" -lt 85 ]] && ok "disk usage ${use}%" || bad "disk usage ${use:-unknown}%"
exit $problems
