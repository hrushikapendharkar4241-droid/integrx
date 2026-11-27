#!/usr/bin/env bash
# Creates the IntegrX data view and four saved searches in Kibana. Run once after the observability stack is up:
#   bash deploy/kibana/setup.sh [http://localhost:5601]
set -euo pipefail
KIBANA="${1:-http://localhost:5601}"
H=(-H "kbn-xsrf: true" -H "Content-Type: application/json")

until curl -fs "$KIBANA/api/status" >/dev/null; do echo "waiting for Kibana..."; sleep 5; done

curl -fs -X POST "$KIBANA/api/data_views/data_view" "${H[@]}" -d '{
  "override": true,
  "data_view": {"id": "integrx-logs", "title": "integrx-*", "name": "IntegrX logs", "timeFieldName": "@timestamp"}
}' >/dev/null && echo "data view: IntegrX logs"

search() {  # id, title, KQL query, columns(JSON array)
  curl -fs -X POST "$KIBANA/api/saved_objects/search/$1?overwrite=true" "${H[@]}" -d "{
    \"attributes\": {
      \"title\": \"$2\",
      \"columns\": $4,
      \"sort\": [[\"@timestamp\", \"desc\"]],
      \"kibanaSavedObjectMeta\": {\"searchSourceJSON\": \"{\\\"query\\\":{\\\"language\\\":\\\"kuery\\\",\\\"query\\\":\\\"$3\\\"},\\\"filter\\\":[],\\\"indexRefName\\\":\\\"kibanaSavedObjectMeta.searchSourceJSON.index\\\"}\"}
    },
    \"references\": [{\"name\": \"kibanaSavedObjectMeta.searchSourceJSON.index\", \"type\": \"index-pattern\", \"id\": \"integrx-logs\"}]
  }" >/dev/null && echo "saved search: $2"
}

search integrx-failed "IntegrX - Failed transactions" 'app:integrx and stage:failed' '["txn_id","partner","channel","failure_code","message"]'
search integrx-timeline "IntegrX - Transaction timeline (filter by txn_id)" 'app:integrx and txn_id:*' '["txn_id","stage","level","message"]'
search integrx-incidents "IntegrX - Incidents" 'stage:(incident_opened or incident_linked or incident_resolved)' '["incident","severity","failure_code","partner","message"]'
search integrx-partner-errors "IntegrX - Partner-side errors" 'app:partner-sim and level:ERROR' '["partner","stage","message"]'

DASH="$(dirname "$0")/integration-health-dashboard.ndjson"
if [[ -f "$DASH" ]]; then
  curl -fs -X POST "$KIBANA/api/saved_objects/_import?overwrite=true" -H "kbn-xsrf: true" -F "file=@$DASH" >/dev/null \
    && echo "dashboard: IntegrX - Integration health (imported)"
  echo "Done. Open Kibana -> Dashboards -> IntegrX - Integration health."
  exit 0
fi

cat <<'TXT'

Done. Open Kibana -> Discover and pick a saved search. To build the health dashboard
(Analytics -> Dashboards -> Create, data view "IntegrX logs"), add these Lens panels:
  1. Metric: Count, filter  stage:received                        -> "Transactions"
  2. Metric: Count, filter  stage:failed                          -> "Failed"
  3. Bar (horizontal): Top values of failure_code, filter stage:failed -> "Top failure types"
  4. Line: Count over @timestamp, break down by level             -> "Errors over time"
  5. Table: Top values of partner x failure_code                  -> "Failures by partner"
  6. Saved search panel: "IntegrX - Incidents"
Save it as "IntegrX - Integration health".
TXT
