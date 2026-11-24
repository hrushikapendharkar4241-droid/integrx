"""Partner simulator: sends documents to IntegrX the way a trading partner would (REST, SFTP, AS2).

  python partner_sim/partners.py rest  ACME01 FILE
  python partner_sim/partners.py sftp  ACME01 FILE [--password WRONG]
  python partner_sim/partners.py as2   ACME01 FILE [--async-mdn URL]

Every send is logged to logs/partner-sim.jsonl, so failures on the partner's side (like an SFTP login
that is refused) also show up in Kibana.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
LOG = Path(os.environ.get("PARTNER_SIM_LOG", ROOT / "logs" / "partner-sim.jsonl"))
API = os.environ.get("INTEGRX_URL", "http://localhost:8080")
SFTP_HOST = os.environ.get("SFTP_HOST", "localhost")
SFTP_PORT = int(os.environ.get("SFTP_PORT", "2222"))
PARTNERS = json.loads((ROOT / "deploy" / "partners.json").read_text())["partners"]
SFTP_PASSWORDS = {"acme01": "acme01-sftp-pass", "mediline": "mediline-sftp-pass", "retailco": "retailco-sftp-pass"}


def partner(pid: str) -> dict:
    for p in PARTNERS:
        if p["id"] == pid:
            return p
    sys.exit(f"unknown partner {pid}")


def plog(event: str, message: str, level: str = "INFO", **fields) -> None:
    rec = {"@timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
           "level": level, "app": "partner-sim", "stage": event, "message": message, **fields}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    print(json.dumps(rec))


def send_rest(pid: str, path: Path, api_key: str | None = None) -> dict:
    p = partner(pid)
    r = httpx.post(f"{API}/api/v1/transactions", content=path.read_bytes(), timeout=120,
                   headers={"X-Partner-ID": pid, "X-API-Key": api_key or p["api_key"], "X-Filename": path.name})
    level = "INFO" if r.status_code < 400 else "ERROR"
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    plog("rest_sent", f"{path.name} -> HTTP {r.status_code} {body}", level, partner=pid, txn_id=body.get("txn_id"))
    return body


def send_sftp(pid: str, path: Path, password: str | None = None) -> bool:
    import paramiko

    user = partner(pid)["sftp_user"]
    pw = password or SFTP_PASSWORDS[user]
    transport = paramiko.Transport((SFTP_HOST, SFTP_PORT))
    try:
        transport.connect(username=user, password=pw)
        sftp = paramiko.SFTPClient.from_transport(transport)
        sftp.put(str(path), f"upload/{path.name}")
        plog("sftp_uploaded", f"{path.name} uploaded to {SFTP_HOST}:{SFTP_PORT} as {user}", partner=pid)
        return True
    except paramiko.AuthenticationException as e:
        plog("sftp_auth_failed", f"SFTP login as {user} to {SFTP_HOST}:{SFTP_PORT} refused: {e}", "ERROR",
             partner=pid, failure_code="SFTP_AUTH_FAILURE")
        return False
    finally:
        transport.close()


def build_as2(pid: str, data: bytes, filename: str, async_mdn: str | None = None,
              key: bytes | None = None, receiver_cert: bytes | None = None):
    """Build a signed + encrypted AS2 message from partner `pid` to IntegrX."""
    from pyas2lib import Message, Organization
    from pyas2lib import Partner as As2Partner

    key = key or (ROOT / "partner_sim" / "certs" / f"{pid.lower()}_key.pem").read_bytes()
    receiver_cert = receiver_cert or (ROOT / "partner_sim" / "certs" / "integrx_cert.pem").read_bytes()
    org = Organization(pid, sign_key=key, sign_key_pass="integrx-demo", decrypt_key=key,
                       decrypt_key_pass="integrx-demo", mdn_url=async_mdn)
    receiver = As2Partner("WHOLESALE02", verify_cert=receiver_cert, encrypt_cert=receiver_cert, sign=True,
                          encrypt=True, enc_alg="aes_256_cbc", mdn_mode="ASYNC" if async_mdn else "SYNC",
                          mdn_digest_alg="sha256")
    msg = Message(org, receiver)
    ctype = ("application/edi-x12" if data.startswith(b"ISA") else
             "application/xml" if data.lstrip().startswith(b"<") else "application/octet-stream")
    msg.build(data, filename=filename, content_type=ctype)
    return msg


def send_as2(pid: str, path: Path, async_mdn: str | None = None) -> str:
    from pyas2lib import Mdn

    msg = build_as2(pid, path.read_bytes(), path.name, async_mdn)
    r = httpx.post(f"{API}/as2", content=msg.content, headers=dict(msg.headers), timeout=120)
    if async_mdn or not r.content:
        plog("as2_sent", f"{path.name} sent; HTTP {r.status_code}; MDN expected at {async_mdn}", partner=pid,
             message_id=msg.message_id)
        return "sent"
    raw = b"".join(f"{k}: {v}\r\n".encode() for k, v in r.headers.items()) + b"\r\n" + r.content
    status, detail = Mdn().parse(raw, find_message_cb=lambda mid, rcv: msg)
    level = "INFO" if status == "processed" else "ERROR"
    plog("as2_mdn", f"{path.name}: MDN {status} {detail or ''}".strip(), level, partner=pid, message_id=msg.message_id)
    return status


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("channel", choices=["rest", "sftp", "as2"])
    ap.add_argument("partner")
    ap.add_argument("file", type=Path)
    ap.add_argument("--password", help="SFTP password override (use a wrong one to simulate an auth failure)")
    ap.add_argument("--async-mdn", help="Request an asynchronous MDN at this URL")
    a = ap.parse_args()
    if a.channel == "rest":
        send_rest(a.partner, a.file)
    elif a.channel == "sftp":
        send_sftp(a.partner, a.file, a.password)
    else:
        send_as2(a.partner, a.file, a.async_mdn)


if __name__ == "__main__":
    main()
