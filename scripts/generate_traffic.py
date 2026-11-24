"""Send synthetic traffic to a running IntegrX so the dashboards have something to show.

  python scripts/generate_traffic.py --count 200 --bad-ratio 0.08

Mixes good and bad Payload Doctor samples across partners and channels (REST and AS2 over HTTP; SFTP by
dropping files into the partner's upload folder). Every number this produces is synthetic: label it so.
"""
from __future__ import annotations

import argparse
import random
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from partner_sim.partners import send_as2, send_rest  # noqa: E402

PD = ROOT.parent / "payload-doctor" / "samples"
GOOD = ["good/po_850.edi", "good/asn_856.edi", "good/orders.edifact", "good/desadv.edifact",
        "good/epcis_shipment.xml", "good/inventory.csv"]
BAD = [p.relative_to(PD).as_posix() for p in sorted((PD / "bad").iterdir())] if (PD / "bad").exists() else []
SFTP_USERS = {"ACME01": "acme01", "MEDILINE": "mediline", "RETAILCO": "retailco"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=50)
    ap.add_argument("--bad-ratio", type=float, default=0.08)
    ap.add_argument("--delay", type=float, default=0.2, help="seconds between documents")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    for i in range(a.count):
        rel = rng.choice(BAD) if BAD and rng.random() < a.bad_ratio else rng.choice(GOOD)
        partner = rng.choice(["ACME01", "MEDILINE", "RETAILCO"])
        channel = rng.choice(["REST", "REST", "SFTP", "AS2"]) if partner != "RETAILCO" else rng.choice(["REST", "SFTP"])
        src = PD / rel
        try:
            if channel == "REST":
                send_rest(partner, src)
            elif channel == "AS2":
                send_as2(partner, src)
            else:
                dest = ROOT / "data" / "sftp" / SFTP_USERS[partner] / "upload" / f"{i:05d}_{src.name}"
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(src, dest)
        except Exception as e:  # keep going; the failure itself is useful traffic
            print(f"{partner} {channel} {rel}: {e}")
        time.sleep(a.delay)
    print(f"sent {a.count} synthetic documents")


if __name__ == "__main__":
    main()
