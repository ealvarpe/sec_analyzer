"""
SEC filing downloader backed by *edgartools* with SQLite caching.

Wraps :pypi:`edgartools` for filing discovery and download, and persists
both the filing index and raw text/HTML content in the project's SQLite
database so repeated runs avoid redundant network traffic.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Optional

from edgar import Company, set_identity
from sqlalchemy.orm import Session

from backend.config import FILING_FORMS, SEC_IDENTITY
from backend.models.database import FilingCache, FilingIndex, TickerMetadata

logger = logging.getLogger(__name__)


class SECDownloader:
    """Download and cache SEC filings via *edgartools*.

    Parameters
    ----------
    session:
        An open SQLAlchemy :class:`Session` bound to the project database.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        set_identity(SEC_IDENTITY)

    # ── Company lookup ───────────────────────────────────────────────────

    def get_company(self, ticker: str) -> Optional[Company]:
        """Return an :class:`edgar.Company` for *ticker*, caching metadata.

        Returns ``None`` on failure (logged as an error).
        """
        try:
            company = Company(ticker)
            # Persist basic company metadata if not already cached
            existing = (
                self.session.query(TickerMetadata)
                .filter_by(ticker=ticker.upper())
                .first()
            )
            if not existing:
                meta = TickerMetadata(
                    ticker=ticker.upper(),
                    company_name=getattr(company, "name", ""),
                    cik=str(getattr(company, "cik", "")),
                    sic_code=str(getattr(company, "sic", "")),
                    entity_type=getattr(company, "entity_type", ""),
                    updated_at=datetime.utcnow(),
                )
                self.session.add(meta)
                self.session.commit()
            return company
        except Exception:
            logger.exception("Failed to get company %s", ticker)
            return None

    # ── Bulk filing fetch ────────────────────────────────────────────────

    def fetch_filings(
        self,
        ticker: str,
        date_from: str,
        date_to: str,
        progress_callback: Optional[Callable[[str], Any]] = None,
    ) -> list[Any]:
        """Fetch all relevant filings for *ticker* within a date range.

        Each discovered filing is added to the ``filing_index`` table.
        Already-indexed filings (by accession number) are not duplicated.

        Parameters
        ----------
        ticker:
            Stock ticker symbol (e.g. ``"AAPL"``).
        date_from:
            Inclusive start date in ``YYYY-MM-DD`` format.
        date_to:
            Inclusive end date in ``YYYY-MM-DD`` format.
        progress_callback:
            Optional callable invoked with a human-readable status string
            after each form type is processed.

        Returns
        -------
        list
            A list of *edgartools* filing objects that fall within the
            requested date range (both new and previously indexed).
        """
        company = self.get_company(ticker)
        if not company:
            return []

        all_filings: list[Any] = []

        try:
            filings = company.get_filings()
            for filing in filings:
                filing_date_str = str(filing.filing_date)
                if filing_date_str < date_from or filing_date_str > date_to:
                    continue

                accession = str(filing.accession_no)

                # Check whether we already have this filing indexed
                existing = (
                    self.session.query(FilingIndex)
                    .filter_by(accession_number=accession)
                    .first()
                )

                if not existing:
                    idx = FilingIndex(
                        ticker=ticker.upper(),
                        accession_number=accession,
                        form_type=filing.form,
                        filing_date=filing_date_str,
                        filing_url=str(
                            getattr(filing, "filing_url", "") or ""
                        ),
                        company_name=str(
                            getattr(filing, "company", "") or ""
                        ),
                        cik=str(getattr(filing, "cik", "") or ""),
                        categories="[]",
                        processed=False,
                        created_at=datetime.utcnow(),
                    )
                    self.session.add(idx)

                all_filings.append(filing)

            if progress_callback:
                progress_callback("Fetched all filings from SEC")

        except Exception:
            logger.warning(
                "Error fetching filings for %s", ticker, exc_info=True
            )

        self.session.commit()
        return all_filings

    # ── Content retrieval (cached) ───────────────────────────────────────

    def get_filing_text(self, filing: Any) -> Optional[str]:
        """Return the plain-text content of *filing*, using SQLite cache.

        On a cache miss the text is fetched via *edgartools* and stored in
        the ``filing_cache`` table for future calls.
        """
        accession = str(filing.accession_no)
        cached = (
            self.session.query(FilingCache)
            .filter_by(accession_number=accession)
            .first()
        )

        if cached and cached.text_content:
            return cached.text_content

        try:
            text: str = filing.text()
            if cached:
                cached.text_content = text
            else:
                cache_entry = FilingCache(
                    accession_number=accession,
                    text_content=text,
                    created_at=datetime.utcnow(),
                )
                self.session.add(cache_entry)
            self.session.commit()
            return text
        except Exception as e:
            logger.warning(
                "Conflict or error saving text for %s, attempting rollback and update: %s",
                accession,
                e,
            )
            try:
                self.session.rollback()
                # Attempt to retrieve existing record and update
                existing = (
                    self.session.query(FilingCache)
                    .filter_by(accession_number=accession)
                    .first()
                )
                if existing:
                    existing.text_content = text
                    self.session.commit()
                    return text
                else:
                    # If it somehow doesn't exist, create it again
                    cache_entry = FilingCache(
                        accession_number=accession,
                        text_content=text,
                        created_at=datetime.utcnow(),
                    )
                    self.session.add(cache_entry)
                    self.session.commit()
                    return text
            except Exception:
                logger.error(
                    "Failed to save text cache for %s after rollback",
                    accession,
                    exc_info=True,
                )
                return None

    def get_filing_html(self, filing: Any) -> Optional[str]:
        """Return the HTML content of *filing*, using SQLite cache.

        On a cache miss the HTML is fetched via *edgartools* and stored in
        the ``filing_cache`` table for future calls.
        """
        accession = str(filing.accession_no)
        cached = (
            self.session.query(FilingCache)
            .filter_by(accession_number=accession)
            .first()
        )

        if cached and cached.html_content:
            return cached.html_content

        try:
            html: str = filing.html()
            if cached:
                cached.html_content = html
            else:
                cache_entry = FilingCache(
                    accession_number=accession,
                    html_content=html,
                    created_at=datetime.utcnow(),
                )
                self.session.add(cache_entry)
            self.session.commit()
            return html
        except Exception as e:
            logger.warning(
                "Conflict or error saving HTML for %s, attempting rollback and update: %s",
                accession,
                e,
            )
            try:
                self.session.rollback()
                # Attempt to retrieve existing record and update
                existing = (
                    self.session.query(FilingCache)
                    .filter_by(accession_number=accession)
                    .first()
                )
                if existing:
                    existing.html_content = html
                    self.session.commit()
                    return html
                else:
                    # If it somehow doesn't exist, create it again
                    cache_entry = FilingCache(
                        accession_number=accession,
                        html_content=html,
                        created_at=datetime.utcnow(),
                    )
                    self.session.add(cache_entry)
                    self.session.commit()
                    return html
            except Exception:
                logger.error(
                    "Failed to save HTML cache for %s after rollback",
                    accession,
                    exc_info=True,
                )
                return None
