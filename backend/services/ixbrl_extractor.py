"""
Inline XBRL (iXBRL) fact extractor.

Wraps the ``ixbrlparse`` library to extract structured facts from SEC filing
HTML that contains inline XBRL markup.  If ``ixbrlparse`` is not installed the
class still instantiates but all extraction methods return ``None``.
"""

from __future__ import annotations

import io
import logging
from collections import defaultdict
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

# ── Conditional import ───────────────────────────────────────────────────────
try:
    from ixbrlparse import IXBRL  # type: ignore[import-untyped]

    _HAS_IXBRL = True
except ImportError:
    _HAS_IXBRL = False
    logger.warning(
        "ixbrlparse is not installed – IXBRLExtractor will return dummy values. "
        "Install with: pip install ixbrlparse"
    )

# ── Well-known concept names ────────────────────────────────────────────────
KNOWN_CONCEPTS: list[str] = [
    "EntityCommonStockSharesOutstanding",
    "CashAndCashEquivalentsAtCarryingValue",
    "ConvertibleNotesPayable",
    "DebtInstrumentFaceAmount",
    "DebtInstrumentInterestRateStatedPercentage",
    "DebtInstrumentMaturityDate",
    "ClassOfWarrantOrRightExercisePriceOfWarrantsOrRights",
    "StockIssuedDuringPeriodSharesNewIssues",
]


class IXBRLExtractor:
    """Extract iXBRL facts from SEC filing HTML.

    Parameters
    ----------
    html_content:
        Raw HTML string containing inline XBRL markup.

    Attributes
    ----------
    facts : list
        Parsed fact objects (empty if ``ixbrlparse`` is unavailable).
    available : bool
        ``True`` when the underlying library loaded successfully.
    """

    def __init__(self, html_content: str) -> None:
        self.available: bool = _HAS_IXBRL
        self.facts: list[Any] = []

        if not _HAS_IXBRL:
            logger.debug("ixbrlparse not available – skipping parse")
            return

        # Fast pre-check: if it doesn't contain HTML or XBRL markup, it's not an iXBRL filing
        content_lower = html_content.lower()
        if not ("<html" in content_lower or "<xbrl" in content_lower or "ix:" in content_lower):
            logger.debug("Document lacks HTML/XBRL markup – skipping iXBRL parse")
            return

        try:
            file_like = io.StringIO(html_content)
            doc = IXBRL(file_like)
            self.facts = []
            if hasattr(doc, 'numeric') and doc.numeric:
                self.facts.extend(doc.numeric)
            if hasattr(doc, 'nonnumeric') and doc.nonnumeric:
                self.facts.extend(doc.nonnumeric)
            logger.debug("Parsed %d iXBRL facts from document", len(self.facts))
        except Exception as e:
            if "Filetype not recognised" in str(e):
                logger.debug("ixbrlparse: Filetype not recognised (no html/xbrl element found)")
            else:
                logger.debug("Failed to parse iXBRL content: %s", e)
            self.facts = []

    # ── Numeric extraction ───────────────────────────────────────────────

    def get_numeric_value(self, concept_names: list[str]) -> float | None:
        """Return the first numeric value matching any of *concept_names*.

        Concepts are tried in the order given; for each concept the *last*
        fact (typically the most recent) is preferred.

        Parameters
        ----------
        concept_names:
            XBRL concept names to search for (e.g.
            ``["CashAndCashEquivalentsAtCarryingValue"]``).

        Returns
        -------
        float or None
            The numeric value, or ``None`` if not found / library unavailable.
        """
        if not self.available or not self.facts:
            return None

        for concept in concept_names:
            matches = [
                f for f in self.facts
                if getattr(f, "name", "").endswith(concept)
            ]
            if matches:
                # Take the last occurrence (most recent / primary)
                fact = matches[-1]
                try:
                    value = fact.value
                    if value is None:
                        continue
                    return float(value)
                except (TypeError, ValueError):
                    logger.debug("Non-numeric value for %s: %r", concept, fact.value)
                    continue
        return None

    # ── Text extraction ──────────────────────────────────────────────────

    def get_text_value(self, concept_names: list[str]) -> str | None:
        """Return the first text value matching any of *concept_names*.

        Parameters
        ----------
        concept_names:
            XBRL concept names to search for.

        Returns
        -------
        str or None
        """
        if not self.available or not self.facts:
            return None

        for concept in concept_names:
            matches = [
                f for f in self.facts
                if getattr(f, "name", "").endswith(concept)
            ]
            if matches:
                value = matches[-1].value
                if value is not None:
                    return str(value).strip()
        return None

    # ── Date extraction ──────────────────────────────────────────────────

    def get_date_value(self, concept_names: list[str]) -> str | None:
        """Return a date string (``YYYY-MM-DD``) for the first matching concept.

        Handles ``datetime.date``, ``datetime.datetime``, and string values
        that look like ISO dates.

        Parameters
        ----------
        concept_names:
            XBRL concept names to search for.

        Returns
        -------
        str or None
            Formatted as ``YYYY-MM-DD``, or ``None``.
        """
        if not self.available or not self.facts:
            return None

        for concept in concept_names:
            matches = [
                f for f in self.facts
                if getattr(f, "name", "").endswith(concept)
            ]
            if matches:
                value = matches[-1].value
                if value is None:
                    continue

                if isinstance(value, date):
                    return value.isoformat()
                if isinstance(value, datetime):
                    return value.date().isoformat()

                # Try parsing a string date
                str_val = str(value).strip()
                for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y"):
                    try:
                        return datetime.strptime(str_val, fmt).date().isoformat()
                    except ValueError:
                        continue
                # Return raw string if all parsing fails
                if str_val:
                    return str_val
        return None

    # ── Bulk extraction ──────────────────────────────────────────────────

    def get_all_facts(self) -> dict[str, list[Any]] | None:
        """Return all parsed facts grouped by concept name.

        Returns
        -------
        dict[str, list] or None
            Mapping from concept name to a list of fact dicts, or ``None``
            if the library is unavailable.
        """
        if not self.available or not self.facts:
            return None

        grouped: dict[str, list[Any]] = defaultdict(list)
        for fact in self.facts:
            name = getattr(fact, "name", "unknown")
            grouped[name].append(
                {
                    "name": name,
                    "value": getattr(fact, "value", None),
                    "context": str(getattr(fact, "context", "")),
                }
            )
        return dict(grouped)
