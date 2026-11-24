# P1 status email: example and template

> Written for a non-technical customer contact. Lab example based on the AS2 certificate scenario (INC-00004); partner names are synthetic.

## Example: first update (within 30 minutes of a P1)

**Subject:** [P1] Shipment notices from ACME01 not received since 23 Nov 07:47 UTC, update 1

Hello Priya,

**What is happening:** Since 23 November at 07:47 UTC, shipment notices sent by your system over AS2 are being rejected by our platform. Your system will have received a "failed" receipt for each one.

**Impact on you:** Your warehouse partners cannot see these shipments in advance. The goods themselves are not affected.

**Cause:** The security certificate your system uses to sign these messages expired on 23 November. Our system rejects messages it cannot verify, to protect both of us.

**What we need from you:** Please send us your renewed public certificate, and have your IT team read us its fingerprint by phone (+91-20-0000-0000). We never need your private key.

**What happens next:** As soon as the new certificate is installed, which takes about 15 minutes on our side, please resend the rejected notices. We have listed them below.

| Your file | Sent (UTC) | Our reference |
| --- | --- | --- |
| asn_856.edi | 24 Nov 07:47 | TXN-2026-000011 |
| asn_856.edi | 24 Nov 07:46 | TXN-2026-000009 |

**Next update:** by 09:00 UTC, or sooner if anything changes.

Regards,
Hrushika Pendharkar
Integration Support, IST shift

---

## Template

**Subject:** [P{n}] {what the customer notices} since {time UTC}, update {k}

- **What is happening:** one or two sentences, in the customer's words, not ours.
- **Impact on you:** what they cannot do, and what is *not* affected.
- **Cause:** plain language. If the cause is not yet known, say what has been ruled out.
- **What we are doing / what we need from you:** concrete actions, with owners.
- **What happens next:** the next step and when it will happen. Never promise a fix time you do not control.
- **Next update:** a time, even if nothing changes.

**Rules:** no internal jargon (AS2 IDs, failure codes, rule numbers), no blame, one update per agreed interval, and every update stands on its own for a reader who missed the earlier ones.
