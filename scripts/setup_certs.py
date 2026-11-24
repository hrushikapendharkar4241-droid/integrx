"""Create, expire or renew the demo AS2 certificates.

  python scripts/setup_certs.py                 # create IntegrX + partner key pairs (skips existing ones)
  python scripts/setup_certs.py --expire ACME01 # replace ACME01's certificate with an expired one (fault)
  python scripts/setup_certs.py --renew ACME01  # issue ACME01 a fresh certificate (the fix)

Private keys are written with a demo passphrase. They are for local testing only; never reuse them.
Files:
  deploy/certs/integrx_key.pem     IntegrX private key + certificate (used to decrypt and sign MDNs)
  deploy/certs/integrx_cert.pem    IntegrX public certificate (give this to partners)
  deploy/certs/<partner>_cert.pem  partner public certificates (verify partner signatures)
  partner_sim/certs/<partner>_key.pem  partner private keys, used only by the partner simulator
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy" / "certs"
SIM = ROOT / "partner_sim" / "certs"
PASS = b"integrx-demo"
PARTNERS = ["ACME01", "MEDILINE"]


def make(cn: str, start_days: int, end_days: int) -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "IntegrX demo (synthetic)")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now + dt.timedelta(days=start_days)).not_valid_after(now + dt.timedelta(days=end_days))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.BestAvailableEncryption(PASS))
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    return key_pem + cert_pem, cert_pem


def write_partner(as2_id: str, start: int, end: int) -> None:
    key, cert = make(as2_id, start, end)
    (SIM / f"{as2_id.lower()}_key.pem").write_bytes(key)
    (DEPLOY / f"{as2_id.lower()}_cert.pem").write_bytes(cert)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expire", metavar="PARTNER")
    ap.add_argument("--renew", metavar="PARTNER")
    args = ap.parse_args()
    DEPLOY.mkdir(parents=True, exist_ok=True)
    SIM.mkdir(parents=True, exist_ok=True)
    if args.expire:
        write_partner(args.expire.upper(), -400, -1)
        print(f"{args.expire.upper()} now has an EXPIRED certificate (fault injected)")
        return
    if args.renew:
        write_partner(args.renew.upper(), -1, 730)
        print(f"{args.renew.upper()} certificate renewed for 2 years")
        return
    if not (DEPLOY / "integrx_key.pem").exists():
        key, cert = make("WHOLESALE02", -1, 730)
        (DEPLOY / "integrx_key.pem").write_bytes(key)
        (DEPLOY / "integrx_cert.pem").write_bytes(cert)
        (SIM / "integrx_cert.pem").write_bytes(cert)
    for p in PARTNERS:
        if not (DEPLOY / f"{p.lower()}_cert.pem").exists():
            write_partner(p, -1, 730)
    print(f"certificates ready in {DEPLOY} and {SIM}")


if __name__ == "__main__":
    main()
