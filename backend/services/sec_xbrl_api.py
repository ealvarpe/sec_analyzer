"""
SEC EDGAR Company Facts (XBRL) API client.

Fetches structured XBRL data from the SEC EDGAR ``companyfacts`` endpoint,
caches responses in SQLite, and provides convenience helpers to extract
common time-series (shares outstanding, cash, debt instruments).
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import Column, DateTime, String, Text, text
from sqlalchemy.orm import Session, declarative_base

from backend.config import SEC_IDENTITY

logger = logging.getLogger(__name__)

Base = declarative_base()

# ── Rate-limit state ─────────────────────────────────────────────────────────
_last_request_time: float = 0.0
_RATE_LIMIT_DELAY: float = 0.15  # seconds between SEC requests


class XBRLCache(Base):  # type: ignore[misc]
    """SQLAlchemy model for the ``xbrl_cache`` table."""

    __tablename__ = "xbrl_cache"

    cik = Column(String, primary_key=True)
    data_json = Column(Text, nullable=False)
    fetched_at = Column(DateTime, nullable=False)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _pad_cik(cik: str) -> str:
    """Zero-pad a CIK to 10 digits as required by the SEC API."""
    return cik.strip().zfill(10)


def _enforce_rate_limit() -> None:
    """Block until at least ``_RATE_LIMIT_DELAY`` seconds have elapsed."""
    global _last_request_time
    now = time.monotonic()
    elapsed = now - _last_request_time
    if elapsed < _RATE_LIMIT_DELAY:
        time.sleep(_RATE_LIMIT_DELAY - elapsed)
    _last_request_time = time.monotonic()


def _ensure_table(session: Session) -> None:
    """Create the ``xbrl_cache`` table if it does not exist."""
    bind = session.get_bind()
    Base.metadata.create_all(bind, tables=[XBRLCache.__table__], checkfirst=True)


# ── Public API ───────────────────────────────────────────────────────────────

def get_company_facts(cik: str, session: Session) -> dict[str, Any]:
    """Fetch SEC EDGAR Company Facts JSON for a given CIK.

    Results are cached in the ``xbrl_cache`` SQLite table.  If a cached entry
    exists and is less than 24 hours old the cached data is returned without
    making an HTTP request.

    Parameters
    ----------
    cik:
        Central Index Key (numeric string, will be zero-padded).
    session:
        An active SQLAlchemy :class:`Session` bound to the target database.

    Returns
    -------
    dict
        The parsed JSON payload from the SEC Company Facts endpoint.

    Raises
    ------
    httpx.HTTPStatusError
        If the SEC API returns a non-2xx status code.
    """
    _ensure_table(session)
    clean_cik = cik.strip().lstrip("0") or "0"

    # ── Check cache ──────────────────────────────────────────────────────
    cached: XBRLCache | None = session.get(XBRLCache, clean_cik)
    if cached is not None:
        age = datetime.now(timezone.utc) - cached.fetched_at.replace(tzinfo=timezone.utc)
        if age < timedelta(hours=24):
            logger.debug("XBRL cache hit for CIK %s (age %s)", clean_cik, age)
            return json.loads(cached.data_json)
        logger.debug("XBRL cache stale for CIK %s (age %s)", clean_cik, age)

    # ── Fetch from SEC ───────────────────────────────────────────────────
    padded = _pad_cik(cik)
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{padded}.json"
    headers = {
        "User-Agent": SEC_IDENTITY,
        "Accept": "application/json",
    }

    _enforce_rate_limit()
    logger.info("Fetching XBRL company facts for CIK %s from SEC", padded)

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
    except httpx.HTTPStatusError:
        logger.error("SEC API returned error for CIK %s: %s", padded, resp.status_code)
        raise
    except Exception:
        logger.exception("Failed to fetch company facts for CIK %s", padded)
        raise

    # ── Update cache ─────────────────────────────────────────────────────
    try:
        now = datetime.now(timezone.utc)
        if cached is not None:
            cached.data_json = json.dumps(data)
            cached.fetched_at = now
        else:
            session.add(XBRLCache(cik=clean_cik, data_json=json.dumps(data), fetched_at=now))
        session.commit()
    except Exception:
        logger.exception("Failed to write XBRL cache for CIK %s", clean_cik)
        session.rollback()

    return data


# ── Time-series extraction helpers ───────────────────────────────────────────

def _extract_facts(
    data: dict[str, Any],
    concept_names: list[str],
    taxonomies: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Pull a time-series from the company-facts JSON.

    Parameters
    ----------
    data:
        The full company-facts dict.
    concept_names:
        XBRL concept names to search for (tried in order).
    taxonomies:
        Taxonomy namespaces to look in (default ``["us-gaap", "dei"]``).

    Returns
    -------
    list[dict]
        Each entry has keys ``date``, ``value``, ``form_type``, ``accession``.
    """
    if taxonomies is None:
        taxonomies = ["us-gaap", "dei"]

    facts_root = data.get("facts", {})
    results: list[dict[str, Any]] = []

    for taxonomy in taxonomies:
        taxonomy_facts = facts_root.get(taxonomy, {})
        for concept in concept_names:
            concept_data = taxonomy_facts.get(concept)
            if concept_data is None:
                continue
            units = concept_data.get("units", {})
            for _unit_key, entries in units.items():
                for entry in entries:
                    results.append(
                        {
                            "date": entry.get("end", entry.get("filed", "")),
                            "value": entry.get("val"),
                            "form_type": entry.get("form", ""),
                            "accession": entry.get("accn", ""),
                        }
                    )
    # Deduplicate by (date, value, accession)
    seen: set[tuple[str, Any, str]] = set()
    unique: list[dict[str, Any]] = []
    for r in results:
        key = (r["date"], r["value"], r["accession"])
        if key not in seen:
            seen.add(key)
            unique.append(r)

    # Sort by date ascending
    unique.sort(key=lambda r: r["date"])
    return unique


def get_shares_outstanding_history(
    cik: str,
    session: Session,
) -> list[dict[str, Any]]:
    """Return time series of shares outstanding for the given CIK.

    Checks both ``us-gaap`` and ``dei`` taxonomies for the
    ``EntityCommonStockSharesOutstanding`` concept.

    Parameters
    ----------
    cik:
        Central Index Key.
    session:
        Active SQLAlchemy session.

    Returns
    -------
    list[dict]
        Entries with ``date``, ``value``, ``form_type``, ``accession``.
    """
    try:
        data = get_company_facts(cik, session)
    except Exception:
        logger.exception("Cannot fetch company facts for shares outstanding (CIK %s)", cik)
        return []

    return _extract_facts(
        data,
        concept_names=["EntityCommonStockSharesOutstanding"],
        taxonomies=["dei", "us-gaap"],
    )


def get_cash_history(
    cik: str,
    session: Session,
) -> list[dict[str, Any]]:
    """Return time series of cash and cash equivalents.

    Parameters
    ----------
    cik:
        Central Index Key.
    session:
        Active SQLAlchemy session.

    Returns
    -------
    list[dict]
        Entries with ``date``, ``value``, ``form_type``, ``accession``.
    """
    try:
        data = get_company_facts(cik, session)
    except Exception:
        logger.exception("Cannot fetch company facts for cash history (CIK %s)", cik)
        return []

    return _extract_facts(
        data,
        concept_names=[
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsAndShortTermInvestments",
        ],
    )


def get_debt_facts(
    cik: str,
    session: Session,
) -> list[dict[str, Any]]:
    """Return time series of convertible/debt instrument facts.

    Searches for ``ConvertibleDebt``, ``DebtInstrumentFaceAmount``, and
    ``ConvertibleNotesPayable``.

    Parameters
    ----------
    cik:
        Central Index Key.
    session:
        Active SQLAlchemy session.

    Returns
    -------
    list[dict]
        Entries with ``date``, ``value``, ``form_type``, ``accession``.
    """
    try:
        data = get_company_facts(cik, session)
    except Exception:
        logger.exception("Cannot fetch company facts for debt (CIK %s)", cik)
        return []

    return _extract_facts(
        data,
        concept_names=[
            "ConvertibleDebt",
            "DebtInstrumentFaceAmount",
            "ConvertibleNotesPayable",
        ],
    )
