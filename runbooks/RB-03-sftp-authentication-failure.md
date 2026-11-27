# RB-03: Partner cannot log in to SFTP

| | |
| --- | --- |
| **Failure code** | `SFTP_AUTH_FAILURE` (P2), opened manually with `POST /api/v1/incidents`, or from the ElastAlert rule `Partner SFTP authentication failures` |
| **Replay?** | No. Nothing arrived, so there is nothing to replay. The partner uploads again once they can log in. |

## Symptoms

- The partner says "Permission denied", "Authentication failed" or "connection refused" when uploading.
- No new transactions from that partner on the SFTP channel. Their REST or AS2 traffic, if any, is normal.
- Partner-side logs, in the demo `logs/partner-sim.jsonl`, show `sftp_auth_failed`.

## Likely causes

| Error the partner sees | Usual cause |
| --- | --- |
| `Permission denied (password)` / `Authentication failed` | Password changed or expired, wrong user name, or the partner rotated their SSH key without sending us the new public key |
| `Permission denied` **after** login, during upload | Folder permissions: the `upload` folder is not writable by the partner's user ID |
| `Connection refused` / timeout | SFTP service down, wrong port (2222 in the demo), or a firewall or IP allow-list change |
| `Host key verification failed` | Our host key changed (server rebuilt); the partner must accept the new key after verifying its fingerprint |

## Diagnose

```bash
# Is the SFTP server up and listening?
docker compose ps sftp
nc -vz localhost 2222

# What does the server log say about this user?
docker compose logs --since 1h sftp | grep -iE "acme01|failed|invalid user|accepted"

# Can WE log in as the partner with the expected credentials? (demo password in partner_sim/partners.py)
sftp -P 2222 acme01@localhost

# Folder permissions (the container writes as uid 1001-1003)
ls -lan data/sftp/acme01/

# Partner-side errors collected in Kibana / logs
jq -c 'select(.stage=="sftp_auth_failed")' logs/partner-sim.jsonl | tail -5
```

## Resolve

1. **Wrong or expired credentials:** reset the password, or install the partner's new public key in `/home/<user>/.ssh/keys/`. Share credentials only over the agreed secure channel.
2. **Folder permissions:** `chmod 777 data/sftp/<user>/upload` in the demo. In production, give ownership to the user's UID.
3. **Service down:** `docker compose up -d sftp`, then check the logs for startup errors.
4. **Host key changed:** send the partner the new host key fingerprint (`ssh-keyscan -p 2222 localhost | ssh-keygen -lf -`) so they can verify and accept it.
5. Open or update the incident:
   ```bash
   curl -s -X POST localhost:8080/api/v1/incidents -H "X-Admin-Key: $INTEGRX_ADMIN_KEY" -H 'Content-Type: application/json' \
     -d '{"failure_code":"SFTP_AUTH_FAILURE","partner_id":"ACME01","detail":"Permission denied since 09:10 UTC","reported_by":"<you>"}'
   ```

6. **Upload succeeds but the watcher never sees the file:** the sftp container can be attached to an old bind mount (for example after the stack was started from another copy of the repo, or after Docker Desktop restarted). Compare the two sides:
   ```bash
   docker compose exec sftp ls -la /home/retailco/upload
   ls -la data/sftp/retailco/upload
   ```
   If they differ, recreate the container so it mounts the host folder again, then ask the partner to retry:
   ```bash
   docker compose up -d --force-recreate sftp
   ```
   Seen in the failure drills on 27 Nov 2026: the login worked after the credentials were fixed, but the file stayed inside the stale container until `sftp` was recreated.

## Verify

- The partner uploads a test file. The watcher picks it up within about 10 seconds (two polls) and creates a transaction with `channel = SFTP`.
- The server log shows `Accepted password for acme01` (or `Accepted publickey`).

## Escalate when

- Many partners cannot connect at the same time. That suggests a platform or network change, so escalate to infrastructure.
- Repeated failed logins come from an unknown IP address. Treat this as a possible brute-force attempt and inform security.
