"""Database models: transactions, their event timeline, canonical records and incidents."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    txn_id: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    partner_id: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(16))  # SFTP | AS2 | REST
    filename: Mapped[str | None] = mapped_column(String(255))
    fmt: Mapped[str | None] = mapped_column(String(32))
    doc_type: Mapped[str | None] = mapped_column(String(32))
    business_key: Mapped[str | None] = mapped_column(String(64), index=True)  # PO / shipment number
    status: Mapped[str] = mapped_column(String(16), index=True, default="RECEIVED")
    failure_code: Mapped[str | None] = mapped_column(String(48), index=True)
    failure_detail: Mapped[str | None] = mapped_column(Text)
    archive_key: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    replay_of: Mapped[str | None] = mapped_column(String(32))
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    events: Mapped[list["Event"]] = relationship(back_populates="txn", order_by="Event.id", cascade="all, delete-orphan")
    incident: Mapped["Incident | None"] = relationship(back_populates="transactions")

    def to_dict(self, with_events: bool = False) -> dict:
        d = {k: getattr(self, k) for k in ("txn_id", "partner_id", "channel", "filename", "fmt", "doc_type",
                                            "business_key", "status", "failure_code", "failure_detail",
                                            "archive_key", "size_bytes", "attempts", "replay_of")}
        d["incident"] = self.incident.key if self.incident else None
        d["created_at"] = self.created_at.isoformat() if self.created_at else None
        d["updated_at"] = self.updated_at.isoformat() if self.updated_at else None
        if with_events:
            d["timeline"] = [e.to_dict() for e in self.events]
        return d


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    txn_pk: Mapped[int] = mapped_column(ForeignKey("transactions.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    stage: Mapped[str] = mapped_column(String(32))
    level: Mapped[str] = mapped_column(String(10), default="INFO")
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict | None] = mapped_column(JSON)

    txn: Mapped[Transaction] = relationship(back_populates="events")

    def to_dict(self) -> dict:
        return {"ts": self.ts.isoformat(), "stage": self.stage, "level": self.level,
                "message": self.message, "data": self.data}


class CanonicalRecord(Base):
    __tablename__ = "canonical_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    txn_id: Mapped[str] = mapped_column(String(32), index=True)
    doc_type: Mapped[str] = mapped_column(String(32))
    business_key: Mapped[str | None] = mapped_column(String(64))
    body: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    failure_code: Mapped[str] = mapped_column(String(48), index=True)
    severity: Mapped[str] = mapped_column(String(4))  # P1..P4
    partner_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="OPEN")  # OPEN | RESOLVED
    runbook: Mapped[str | None] = mapped_column(String(128))
    jira_key: Mapped[str | None] = mapped_column(String(32))
    ai_draft: Mapped[dict | None] = mapped_column(JSON)
    draft_status: Mapped[str | None] = mapped_column(String(16))  # DRAFT | APPROVED | SENT
    approved_by: Mapped[str | None] = mapped_column(String(64))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    transactions: Mapped[list[Transaction]] = relationship(back_populates="incident")

    @property
    def key(self) -> str:
        return f"INC-{self.id:05d}"

    def to_dict(self) -> dict:
        return {
            "key": self.key, "failure_code": self.failure_code, "severity": self.severity,
            "partner_id": self.partner_id, "title": self.title, "status": self.status, "runbook": self.runbook,
            "jira_key": self.jira_key, "draft_status": self.draft_status, "ai_draft": self.ai_draft,
            "approved_by": self.approved_by, "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "resolution": self.resolution, "created_at": self.created_at.isoformat() if self.created_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "transactions": [t.txn_id for t in self.transactions],
        }


_engine = None
_Session: sessionmaker | None = None


def init_db(url: str | None = None) -> sessionmaker:
    global _engine, _Session
    url = url or get_settings().db_url
    if url.startswith("sqlite:///"):
        from pathlib import Path
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    kwargs = {"connect_args": {"check_same_thread": False, "timeout": 30}} if url.startswith("sqlite") else {"pool_pre_ping": True}
    _engine = create_engine(url, **kwargs)
    Base.metadata.create_all(_engine)
    _Session = sessionmaker(_engine, expire_on_commit=False)
    return _Session


def session() -> Session:
    if _Session is None:
        init_db()
    return _Session()


def get_txn(s: Session, txn_id: str) -> Transaction | None:
    return s.scalar(select(Transaction).where(Transaction.txn_id == txn_id))


def get_incident(s: Session, key: str) -> Incident | None:
    try:
        pk = int(key.upper().removeprefix("INC-"))
    except ValueError:
        return None
    return s.get(Incident, pk)
