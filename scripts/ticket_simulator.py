"""Customer ticket simulator: turns recent failures into the emails partners actually send.

  python scripts/ticket_simulator.py            # writes data/tickets/inbox/TICKET-*.md

A support engineer starts from the ticket, not from the dashboard: find the transaction ID, open its
timeline, find the root cause, fix, replay, reply. That is the IntegrX demo.
"""
from __future__ import annotations

import json
import os
import random
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = os.environ.get("INTEGRX_URL", "http://localhost:8080")
KEY = os.environ.get("INTEGRX_ADMIN_KEY", "change-me-admin-key")
OUT = Path(__file__).resolve().parent.parent / "data" / "tickets" / "inbox"

TEMPLATES = {
    "shipment": "Subject: ASN for PO {ref} never arrived - urgent\n\nHi team,\n\nOur warehouse is expecting the shipment "
                "for PO {ref} today but we have no ship notice in our system. We sent it at {time} UTC. "
                "Can you check what happened? The truck is at the dock.\n\nThanks,\n{partner} logistics",
    "order": "Subject: Order {ref} not confirmed\n\nHello,\n\nWe placed purchase order {ref} at {time} UTC and have not "
             "received an acknowledgement or confirmation. Did you get it?\n\nRegards,\n{partner} purchasing",
    "inventory": "Subject: Stock file not loaded\n\nHi,\n\nOur inventory file sent at {time} UTC does not show in the "
                 "portal. Please check.\n\n{partner} IT",
    None: "Subject: File rejected?\n\nHello,\n\nWe sent a file at {time} UTC ({filename}) and got no response. "
          "Can you confirm whether it was received?\n\n{partner} EDI team",
}


def get(path: str):
    req = urllib.request.Request(f"{API}{path}", headers={"X-Admin-Key": KEY})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def main() -> None:
    failed = get("/api/v1/transactions?status=FAILED&limit=20")
    if not failed:
        print("no failed transactions yet; run scripts/inject_fault.sh first")
        return
    OUT.mkdir(parents=True, exist_ok=True)
    for t in random.sample(failed, min(3, len(failed))):
        kind = t["doc_type"] or {"epcis-xml": "shipment", "csv": "inventory"}.get(t["fmt"] or "")
        body = TEMPLATES.get(kind, TEMPLATES[None]).format(
            ref=t["business_key"] or "(see attached)", time=t["created_at"][11:16], partner=t["partner_id"],
            filename=t["filename"] or "-")
        name = OUT / f"TICKET-{datetime.now(timezone.utc):%H%M%S}-{t['txn_id'][-6:]}.md"
        # The partner does not know our transaction ID; the engineer has to find it from PO, file name or time.
        name.write_text(f"From: {t['partner_id']}\nReceived: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC\n\n{body}\n")
        print(f"wrote {name}")


if __name__ == "__main__":
    main()
