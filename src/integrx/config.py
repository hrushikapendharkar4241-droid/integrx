"""Settings come from environment variables so the same code runs in Docker, under systemd, or in tests."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Partner:
    id: str
    name: str
    api_key: str = ""
    as2_id: str = ""
    as2_cert: str = ""  # partner's public certificate (verify signatures, encrypt MDNs)
    sftp_user: str = ""
    ack_format: str = "auto"  # auto | x12 | edifact | none


@dataclass
class Settings:
    db_url: str = field(default_factory=lambda: _env("INTEGRX_DB_URL", "sqlite:///./data/integrx.db"))
    log_file: str = field(default_factory=lambda: _env("INTEGRX_LOG_FILE", "./logs/integrx.jsonl"))
    archive: str = field(default_factory=lambda: _env("INTEGRX_ARCHIVE", "local:./data/archive"))
    outbox: str = field(default_factory=lambda: _env("INTEGRX_OUTBOX", "./data/outbox"))
    tickets_dir: str = field(default_factory=lambda: _env("INTEGRX_TICKETS_DIR", "./data/tickets"))
    sftp_root: str = field(default_factory=lambda: _env("INTEGRX_SFTP_ROOT", "./data/sftp"))
    faults_file: str = field(default_factory=lambda: _env("INTEGRX_FAULTS_FILE", "./data/faults.json"))
    partners_file: str = field(default_factory=lambda: _env("INTEGRX_PARTNERS_FILE", "./deploy/partners.json"))
    runbooks_dir: str = field(default_factory=lambda: _env("INTEGRX_RUNBOOKS_DIR", "./runbooks"))
    downstream_url: str = field(default_factory=lambda: _env("INTEGRX_DOWNSTREAM_URL", "http://localhost:8090/api/documents"))
    downstream_token: str = field(default_factory=lambda: _env("INTEGRX_DOWNSTREAM_TOKEN", "demo-downstream-token"))
    downstream_timeout: float = field(default_factory=lambda: float(_env("INTEGRX_DOWNSTREAM_TIMEOUT", "30")))
    max_retries: int = field(default_factory=lambda: int(_env("INTEGRX_MAX_RETRIES", "2")))
    retry_backoff: float = field(default_factory=lambda: float(_env("INTEGRX_RETRY_BACKOFF", "2")))
    admin_key: str = field(default_factory=lambda: _env("INTEGRX_ADMIN_KEY", "change-me-admin-key"))
    our_as2_id: str = field(default_factory=lambda: _env("INTEGRX_AS2_ID", "WHOLESALE02"))
    our_as2_key: str = field(default_factory=lambda: _env("INTEGRX_AS2_KEY", "./deploy/certs/integrx_key.pem"))
    our_as2_key_pass: str = field(default_factory=lambda: _env("INTEGRX_AS2_KEY_PASS", "integrx-demo"))
    our_isa_id: str = field(default_factory=lambda: _env("INTEGRX_ISA_ID", "WHOLESALE02"))
    jira_base_url: str = field(default_factory=lambda: _env("JIRA_BASE_URL", ""))
    jira_email: str = field(default_factory=lambda: _env("JIRA_EMAIL", ""))
    jira_api_token: str = field(default_factory=lambda: _env("JIRA_API_TOKEN", ""))
    jira_project: str = field(default_factory=lambda: _env("JIRA_PROJECT_KEY", "SUP"))
    confluence_base: str = field(default_factory=lambda: _env("CONFLUENCE_RUNBOOK_BASE", ""))
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY", ""))
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL", "gemini-2.5-flash"))
    incident_dedupe_minutes: int = field(default_factory=lambda: int(_env("INTEGRX_INCIDENT_DEDUPE_MINUTES", "60")))

    def partners(self) -> dict[str, Partner]:
        path = Path(self.partners_file)
        if not path.exists():
            return {}
        raw = json.loads(path.read_text())
        return {p["id"]: Partner(**p) for p in raw["partners"]}

    def partner_by(self, **kw) -> Partner | None:
        for p in self.partners().values():
            if all(getattr(p, k) == v for k, v in kw.items()):
                return p
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
