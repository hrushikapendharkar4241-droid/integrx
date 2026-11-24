"""Structured JSON logging. Every line carries the transaction ID so Kibana and `pdoc trace` can follow it."""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from .config import get_settings

_lock = threading.Lock()


def log(event: str, message: str, *, level: str = "INFO", txn_id: str | None = None, **fields) -> dict:
    record = {
        "@timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "level": level,
        "app": "integrx",  # not "service"/"event": those are ECS objects in Elasticsearch and would clash
        "stage": event,
        "message": message,
    }
    if txn_id:
        record["txn_id"] = txn_id
    record.update({k: v for k, v in fields.items() if v is not None})
    line = json.dumps(record, default=str)
    path = Path(get_settings().log_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    print(line, file=sys.stderr)
    return record
