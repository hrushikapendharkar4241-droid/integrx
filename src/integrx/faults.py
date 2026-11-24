"""In-process fault switches, shared between processes through a small JSON file.

Infrastructure faults (wrong SFTP password, expired AS2 certificate, downstream outage) are injected with
scripts/inject_fault.sh. The switches here cover faults inside IntegrX itself.
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import get_settings

KNOWN = {
    "ack_not_sent": "Writing outbound 997/CONTRL acknowledgements fails",
    "mdn_delivery_down": "Asynchronous MDNs cannot be delivered to partners",
}


def _path() -> Path:
    return Path(get_settings().faults_file)


def active() -> dict[str, bool]:
    try:
        return {k: bool(v) for k, v in json.loads(_path().read_text()).items() if k in KNOWN}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def is_on(name: str) -> bool:
    return active().get(name, False)


def set_fault(name: str, on: bool) -> dict[str, bool]:
    if name not in KNOWN:
        raise KeyError(name)
    state = active()
    state[name] = on
    _path().parent.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(state, indent=2))
    return state
