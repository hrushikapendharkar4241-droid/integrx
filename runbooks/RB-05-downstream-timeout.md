# RB-05: Downstream order service timeout or error

| | |
| --- | --- |
| **Failure codes** | `DOWNSTREAM_TIMEOUT`, `DOWNSTREAM_SERVER_ERROR`, `DOWNSTREAM_AUTH_FAILURE` (P2); `DOWNSTREAM_REJECTED` (P3) |
| **Alert** | ElastAlert rule `IntegrX failure spike per partner` |
| **Replay?** | **Yes**, for timeouts, 5xx and auth failures, after the cause is fixed. **No** for `DOWNSTREAM_REJECTED`: the order system refused the business data. |

## Symptoms

- Documents from **many partners** fail at the same time with the same code. The payloads were valid; the problem is after transformation.
- The timeline shows `delivery_error`, then `delivery_retry` ×2, then `failed`. A timeout takes timeout × 3 attempts.
- One incident collects many transactions. Deduplication groups failures of the same code per partner within 60 minutes.

## Likely causes

| Code | Usual cause |
| --- | --- |
| `DOWNSTREAM_TIMEOUT` | The order service is overloaded or hung (database locks, long garbage-collection pauses), or there is a network problem between the services |
| `DOWNSTREAM_SERVER_ERROR` | The order service crashed, its database is unavailable, or a bad deployment |
| `DOWNSTREAM_AUTH_FAILURE` | Our API token expired or was rotated without updating `INTEGRX_DOWNSTREAM_TOKEN`. Not retried, because retrying will not help. |
| `DOWNSTREAM_REJECTED` | A 4xx for the business data, such as an unknown product or a closed PO. Not a platform problem. |

## Diagnose

```bash
scripts/analyze_transaction.sh TXN-2026-000005              # attempts, timings, error text
curl -s localhost:8090/health | jq .                        # order-service health (the demo stub shows its mode)
curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" localhost:8090/health
docker compose logs --since 30m order-service | tail -50
scripts/find_errors.sh "$(date -u +%F)" logs --summary      # how wide is the impact?
# Everything waiting for a replay:
curl -s -H "X-Admin-Key: $INTEGRX_ADMIN_KEY" "localhost:8080/api/v1/transactions?status=FAILED&limit=500" \
  | jq -r '.[] | select(.failure_code|startswith("DOWNSTREAM_")) | .txn_id'
```

## Resolve

1. **Restore the service first.** Restart it, scale it, or roll back the bad deployment. In the demo: `scripts/inject_fault.sh downstream-ok`.
2. **Auth failure:** update `INTEGRX_DOWNSTREAM_TOKEN` in `.env`, then `docker compose up -d api watcher`.
3. Confirm health: `curl localhost:8090/health` returns `ok`.
4. **Replay** each affected transaction with your name and the reason:
   ```bash
   for t in $(curl -s -H "X-Admin-Key: $INTEGRX_ADMIN_KEY" "localhost:8080/api/v1/transactions?status=FAILED&limit=500" \
              | jq -r '.[] | select(.failure_code=="DOWNSTREAM_TIMEOUT") | .txn_id'); do
     curl -s -X POST "localhost:8080/api/v1/transactions/$t/replay" -H "X-Admin-Key: $INTEGRX_ADMIN_KEY" \
       -H 'Content-Type: application/json' -d '{"approved_by":"<you>","reason":"order-service restored at <time>"}' \
       | jq -r '"\(.replay_of) -> \(.txn_id) \(.status)"'
   done
   ```
   Replays reuse the archived original. Each replay is a new transaction linked by `replay_of`, and the original is marked `REPLAYED` once its replay succeeds.

## Verify

- Every replay is `PROCESSED`, and a 997/CONTRL was sent for EDI documents.
- The success rate on the dashboard (`GET /api/v1/stats`) returns to normal.
- Resolve the incident, recording the cause and the number of replayed transactions.

## Escalate when

- The order service is not restored within 30 minutes. Escalate to the order-service on-call. Documents are safe in the archive, but nothing reaches the order system.
- Time-critical documents (same-day ship notices) are affected. Inform the partner's operations contact, using an approved status email.
