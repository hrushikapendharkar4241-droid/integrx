"""Test setup: every test session gets its own temp folders, SQLite database, certificates and partners."""
import json
import os
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
PD_SAMPLES = Path(os.environ.get("PAYLOAD_DOCTOR_SAMPLES", ROOT.parent / "payload-doctor" / "samples"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from setup_certs import make  # noqa: E402


class Downstream:
    """Mock order-service: mode = normal | slow | error500 | auth401."""

    def __init__(self):
        self.mode = "normal"
        self.received = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.mode == "slow":
            raise httpx.ReadTimeout("timed out", request=request)
        if self.mode == "error500":
            return httpx.Response(500, text="database connection pool exhausted")
        if self.mode == "auth401":
            return httpx.Response(401, text="invalid token")
        self.received.append(json.loads(request.content))
        return httpx.Response(201, json={"accepted": True})


@pytest.fixture(scope="session")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("integrx")
    certs = tmp / "certs"
    certs.mkdir()
    keys = {}
    for name, start, end in [("integrx", -1, 700), ("acme01", -1, 700), ("mediline", -400, -2)]:
        key, cert = make(name.upper() if name != "integrx" else "WHOLESALE02", start, end)
        (certs / f"{name}_key.pem").write_bytes(key)
        (certs / f"{name}_cert.pem").write_bytes(cert)
        keys[name] = (key, cert)
    partners = {"partners": [
        {"id": "ACME01", "name": "Acme", "api_key": "k-acme", "as2_id": "ACME01",
         "as2_cert": str(certs / "acme01_cert.pem"), "sftp_user": "acme01"},
        {"id": "MEDILINE", "name": "MediLine", "api_key": "k-medi", "as2_id": "MEDILINE",
         "as2_cert": str(certs / "mediline_cert.pem")},
        {"id": "RETAILCO", "name": "RetailCo", "api_key": "k-retail", "sftp_user": "retailco"},
    ]}
    (tmp / "partners.json").write_text(json.dumps(partners))
    os.environ.update({
        "INTEGRX_DB_URL": f"sqlite:///{tmp}/integrx.db",
        "INTEGRX_LOG_FILE": str(tmp / "logs" / "integrx.jsonl"),
        "INTEGRX_ARCHIVE": f"local:{tmp}/archive",
        "INTEGRX_OUTBOX": str(tmp / "outbox"),
        "INTEGRX_TICKETS_DIR": str(tmp / "tickets"),
        "INTEGRX_SFTP_ROOT": str(tmp / "sftp"),
        "INTEGRX_FAULTS_FILE": str(tmp / "faults.json"),
        "INTEGRX_PARTNERS_FILE": str(tmp / "partners.json"),
        "INTEGRX_RUNBOOKS_DIR": str(ROOT / "runbooks"),
        "INTEGRX_DOWNSTREAM_URL": "http://order-service.test/api/documents",
        "INTEGRX_RETRY_BACKOFF": "0",
        "INTEGRX_ADMIN_KEY": "test-admin",
        "INTEGRX_AS2_KEY": str(certs / "integrx_key.pem"),
        "INTEGRX_AS2_KEY_PASS": "integrx-demo",
    })
    for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "GEMINI_API_KEY"):
        os.environ.pop(k, None)
    from integrx.config import get_settings
    from integrx.db import init_db

    get_settings.cache_clear()
    init_db()
    return {"tmp": tmp, "keys": keys}


@pytest.fixture
def downstream(env):
    from integrx import downstream as ds

    mock = Downstream()
    ds.transport = httpx.MockTransport(mock.handler)
    yield mock
    ds.transport = None


@pytest.fixture
def client(env, downstream):
    from fastapi.testclient import TestClient

    from integrx.api import app

    with TestClient(app) as c:
        yield c


ADMIN = {"X-Admin-Key": "test-admin"}


def sample(rel: str) -> bytes:
    return (PD_SAMPLES / rel).read_bytes()


def post(client, partner: str, key: str, rel: str):
    return client.post("/api/v1/transactions", content=sample(rel),
                       headers={"X-Partner-ID": partner, "X-API-Key": key, "X-Filename": Path(rel).name})
