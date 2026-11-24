#!/usr/bin/env bash
# find_errors.sh [YYYY-MM-DD] [LOG_DIR] [--summary]
# Lists ERROR lines for a day from the JSON logs, or with --summary counts them by failure code and partner.
set -euo pipefail
DAY="${1:-$(date -u +%F)}"
LOGS="${2:-logs}"
MODE="${3:-}"
command -v jq >/dev/null || { echo "jq is required" >&2; exit 3; }

lines() { cat "$LOGS"/*.jsonl 2>/dev/null | jq -c --arg d "$DAY" 'select(.level=="ERROR" and (.["@timestamp"] | startswith($d)))'; }

if [[ "$MODE" == "--summary" ]]; then
  echo "Errors on $DAY"
  echo "By failure code:"; lines | jq -r 'select(.stage=="failed") | .failure_code // "?"' | sort | uniq -c | sort -rn | sed 's/^/  /'
  echo "By partner:";      lines | jq -r '.partner // "?"' | sort | uniq -c | sort -rn | sed 's/^/  /'
  echo "Failed transactions:"; lines | jq -r 'select(.stage=="failed") | "  \(.txn_id)  \(.failure_code)"' | sort -u
else
  lines | jq -r '"\(.["@timestamp"][11:23])  \(.txn_id // "-")  \(.partner // "-")  \(.stage): \(.message)"'
fi
