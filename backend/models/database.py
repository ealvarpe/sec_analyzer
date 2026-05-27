"""
SQLAlchemy 2.0 ORM models for the SEC Analyzer database.

Tables
------
- FilingIndex   – master index of every filing discovered
- FilingCache   – cached text/HTML of downloaded filings
- ExtractionLog – per-filing extraction status log
- AnalysisRun   – top-level record for each analysis run
- TickerMetadata – basic company info per ticker
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
    Index,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)


# ── Base ─────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


# ── FilingIndex ──────────────────────────────────────────────────────────────

class FilingIndex(Base):
    """Master index of every SEC filing discovered during a search."""

    __tablename__ = "filing_index"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    accession_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    form_type: Mapped[str] = mapped_column(String(20), nullable=False)
    filing_date: Mapped[str] = mapped_column(String(10), nullable=False)
    filing_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    company_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    cik: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    categories: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<FilingIndex(id={self.id}, ticker={self.ticker!r}, "
            f"form={self.form_type!r}, date={self.filing_date!r})>"
        )


# ── FilingCache ──────────────────────────────────────────────────────────────

class FilingCache(Base):
    """Cached raw text / HTML content of a downloaded filing."""

    __tablename__ = "filing_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    accession_number: Mapped[str] = mapped_column(
        String(30), unique=True, index=True, nullable=False
    )
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    html_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )

    def __repr__(self) -> str:
        return f"<FilingCache(id={self.id}, accession={self.accession_number!r})>"


# ── ExtractionLog ───────────────────────────────────────────────────────────

class ExtractionLog(Base):
    """Per-filing extraction result log entry."""

    __tablename__ = "extraction_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    accession_number: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    ticker: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # success / error / skipped
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    records_extracted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<ExtractionLog(id={self.id}, ticker={self.ticker!r}, "
            f"category={self.category!r}, status={self.status!r})>"
        )


# ── AnalysisRun ──────────────────────────────────────────────────────────────

class AnalysisRun(Base):
    """Top-level record tracking an entire analysis run."""

    __tablename__ = "analysis_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tickers: Mapped[str] = mapped_column(Text, nullable=False)  # comma-separated
    data_root: Mapped[str] = mapped_column(String(500), nullable=False)
    date_from: Mapped[str] = mapped_column(String(10), nullable=False)
    date_to: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running"
    )  # running / completed / failed
    started_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    results_summary: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    def __repr__(self) -> str:
        return (
            f"<AnalysisRun(id={self.id}, tickers={self.tickers!r}, "
            f"status={self.status!r})>"
        )


# ── TickerMetadata ───────────────────────────────────────────────────────────

class TickerMetadata(Base):
    """Basic company information cached per ticker."""

    __tablename__ = "ticker_metadata"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    cik: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    sic_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    entity_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<TickerMetadata(id={self.id}, ticker={self.ticker!r}, "
            f"company={self.company_name!r})>"
        )


# ── Engine / Session helpers ─────────────────────────────────────────────────

def get_engine(db_path: Path | str) -> Engine:
    """Create a SQLAlchemy engine pointing at *db_path* (SQLite).

    Parameters
    ----------
    db_path:
        Absolute path to the ``.db`` file.  Parent directories must exist.

    Returns
    -------
    Engine
        A configured :class:`sqlalchemy.engine.Engine`.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{db_path}"
    return create_engine(url, connect_args={"timeout": 30.0}, echo=False)


def create_tables(engine: Engine) -> None:
    """Create all tables that do not yet exist in the database."""
    Base.metadata.create_all(engine)


def get_session(engine: Engine) -> Session:
    """Return a new :class:`Session` bound to *engine*.

    Usage::

        session = get_session(engine)
        with session:
            session.add(...)
            session.commit()
    """
    factory = sessionmaker(bind=engine)
    return factory()
