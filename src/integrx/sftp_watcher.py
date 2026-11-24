"""Watch partner SFTP upload folders and ingest files once they have finished uploading.

Layout (shared with the atmoz/sftp container):  data/sftp/<sftp_user>/upload/<file>
A file is picked up only when its size and modification time are unchanged across two polls, so a
half-uploaded file is never processed (a classic cause of "truncated file" tickets).
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from .config import get_settings
from .logs import log
from .pipeline import ingest


class Watcher:
    def __init__(self) -> None:
        self.seen: dict[Path, tuple[int, float]] = {}

    def poll(self) -> list[str]:
        s = get_settings()
        root = Path(s.sftp_root)
        users = {p.sftp_user: p for p in s.partners().values() if p.sftp_user}
        done: list[str] = []
        for user, partner in users.items():
            upload = root / user / "upload"
            if not upload.is_dir():
                continue
            for f in sorted(upload.iterdir()):
                if not f.is_file() or f.name.startswith("."):
                    continue
                st = f.stat()
                sig = (st.st_size, st.st_mtime)
                if self.seen.get(f) != sig:
                    self.seen[f] = sig  # first sighting or still growing: wait for the next poll
                    continue
                processing = root / user / "processing"
                processing.mkdir(parents=True, exist_ok=True)
                target = processing / f.name
                shutil.move(str(f), target)
                self.seen.pop(f, None)
                txn_id = ingest(target.read_bytes(), partner.id, "SFTP", f.name)
                archived = root / user / "processed"
                archived.mkdir(exist_ok=True)
                shutil.move(str(target), archived / f"{txn_id}_{f.name}")
                done.append(txn_id)
        return done


def recover_stuck() -> int:
    """Move files left in processing/ by a crash back to upload/, so they are picked up again."""
    s = get_settings()
    moved = 0
    for p in s.partners().values():
        if not p.sftp_user:
            continue
        stuck = Path(s.sftp_root) / p.sftp_user / "processing"
        if stuck.is_dir():
            for f in stuck.iterdir():
                if f.is_file():
                    shutil.move(str(f), Path(s.sftp_root) / p.sftp_user / "upload" / f.name)
                    log("sftp_recovered", f"{f.name} was stuck in processing; moved back to upload", level="WARNING",
                        partner=p.id)
                    moved += 1
    return moved


def main(interval: float = 5.0) -> None:
    log("sftp_watcher_started", f"watching {get_settings().sftp_root} every {interval}s")
    recover_stuck()
    w = Watcher()
    while True:
        try:
            w.poll()
        except Exception as e:  # keep watching even if one file fails
            log("sftp_watcher_error", f"{type(e).__name__}: {e}", level="ERROR")
        time.sleep(interval)


if __name__ == "__main__":
    main()
