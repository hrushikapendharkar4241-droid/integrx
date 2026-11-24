# RB-01: AS2 certificate expired or invalid

| | |
| --- | --- |
| **Failure codes** | `AS2_CERT_INVALID` (P1), `AS2_SECURITY_ERROR` (P2) |
| **Alert** | ElastAlert rule `IntegrX AS2 certificate failure` |
| **Replay?** | **No.** A message whose signature could not be verified is not trusted after the fact. The partner resends once the new certificate is installed. The archived copy is kept only as evidence. |

## Symptoms

- The partner says "our files are not getting through", or "your MDN says failed".
- The partner-side log shows `MDN failed/Failure invalid-certificate`, `decryption-failed` or `authentication-failed`.
- In IntegrX, every AS2 transaction from that partner is `FAILED` with `AS2_CERT_INVALID`, and nothing reaches validation.
- Messages from other partners and other channels are fine. That rules out a platform-wide outage.

## Likely causes

1. **The partner's certificate expired.** This is the most common cause. Their new certificate was never sent to us or never installed.
2. **We installed the wrong certificate**, for example their test certificate in production, or the certificate of another AS2 ID.
3. **Our own certificate expired**, so the partner's gateway can no longer encrypt to us or verify our MDNs.
4. **Algorithm mismatch.** The partner switched to an encryption or signing algorithm we do not accept.

## Diagnose

```bash
# 1. Confirm the failure and see the evidence
scripts/analyze_transaction.sh TXN-2026-000011

# 2. Check the certificate we hold for the partner (expiry, key size, signature algorithm)
pdoc as2 deploy/certs/acme01_cert.pem

# 3. Check our own certificate as well
pdoc as2 deploy/certs/integrx_cert.pem

# 4. How many messages are affected, and since when?
scripts/find_errors.sh "$(date -u +%F)" logs --summary
curl -s -H "X-Admin-Key: $INTEGRX_ADMIN_KEY" "localhost:8080/api/v1/transactions?status=FAILED&partner=ACME01" \
  | jq -r '.[] | select(.failure_code=="AS2_CERT_INVALID") | "\(.txn_id) \(.created_at) \(.filename)"'

# 5. Compare fingerprints with the certificate the partner says they use
openssl x509 -in deploy/certs/acme01_cert.pem -noout -fingerprint -sha256 -subject -enddate
```

## Resolve

1. Ask the partner for their renewed **public** certificate over an agreed secure channel. Never accept a private key.
2. Check that the SHA-256 fingerprint matches what the partner reads out to you, by phone or in a verified email.
3. Install it. In the demo, run `python scripts/setup_certs.py --renew ACME01`. In production, replace `deploy/certs/<partner>_cert.pem` and restart the API.
4. Run `pdoc as2` on the new file. It must show no AS2-001 or AS2-002 findings.
5. Ask the partner to **resend** the rejected messages. Give them the list of file names and times from step 4 above.
6. Add the new expiry date to the certificate calendar (`health_check.sh` warns 30 days before).

## Verify

- The partner's next message returns `MDN processed`.
- `scripts/analyze_transaction.sh <new TXN>` shows `PROCESSED`, and a 997/CONTRL is sent for EDI documents.
- No new `AS2_CERT_INVALID` lines appear in Kibana for 30 minutes.
- Resolve the incident with what was done: `POST /api/v1/incidents/INC-xxxxx/resolve`.

## Escalate when

- The partner cannot provide a certificate within 2 hours and the documents are time-critical, such as ship notices for today's deliveries. Escalate to the account owner to agree a temporary fallback channel, for example SFTP.
- **Our** certificate is the problem. Escalate to the platform/security owner, because every partner is affected.
- The fingerprint the partner reads out does not match the file you received. Treat this as a possible security incident.
