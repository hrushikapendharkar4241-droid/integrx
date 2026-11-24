"""Deliver canonical documents to the downstream order service, with timeout and retries."""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from .config import get_settings

# Tests swap this for an httpx.MockTransport.
transport: httpx.BaseTransport | None = None


@dataclass
class Attempt:
    number: int
    ok: bool
    status_code: int | None
    timed_out: bool
    error: str | None
    elapsed_ms: int


def deliver(txn_id: str, doc: dict, on_attempt=None) -> list[Attempt]:
    """POST the canonical document; retry on timeouts and 5xx, never on 4xx."""
    s = get_settings()
    attempts: list[Attempt] = []
    total = 1 + s.max_retries
    with httpx.Client(timeout=s.downstream_timeout, transport=transport) as client:
        for n in range(1, total + 1):
            start = time.monotonic()
            try:
                r = client.post(s.downstream_url, json=doc,
                                headers={"Authorization": f"Bearer {s.downstream_token}", "X-Transaction-ID": txn_id,
                                         "Idempotency-Key": txn_id})
                ok = 200 <= r.status_code < 300
                a = Attempt(n, ok, r.status_code, False, None if ok else f"HTTP {r.status_code}: {r.text[:200]}",
                            int((time.monotonic() - start) * 1000))
            except httpx.TimeoutException:
                a = Attempt(n, False, None, True, f"ConnectionTimeout: {s.downstream_timeout:g}s exceeded",
                            int((time.monotonic() - start) * 1000))
            except httpx.HTTPError as e:
                a = Attempt(n, False, None, False, f"{type(e).__name__}: {e}", int((time.monotonic() - start) * 1000))
            attempts.append(a)
            if on_attempt:
                on_attempt(a, n < total)
            retryable = a.timed_out or a.status_code is None or (a.status_code or 0) >= 500
            if a.ok or not retryable or n == total:
                break
            time.sleep(s.retry_backoff)
    return attempts
