"""
Filing classifier – assign data categories to SEC filings.

Uses a two-stage approach:

1. **Static mapping** – each form type (e.g. ``S-3``, ``8-K``) is mapped
   to a set of *candidate* categories.
2. **Content refinement** – for multi-category form types the actual filing
   text is scanned with compiled regex patterns to confirm or expand the
   category set.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from backend.models.database import FilingIndex
from backend.services.regex_patterns import PATTERNS

logger = logging.getLogger(__name__)


# ── Static form → category mapping ──────────────────────────────────────────

FORM_CATEGORY_MAP: dict[str, list[str]] = {
    "S-3":    ["shelf", "atm"],
    "S-3/A":  ["shelf", "atm"],
    "S-3ASR": ["shelf", "atm"],
    "424B5":  ["atm", "warrant", "convertible_note", "completed_offering"],
    "424B7":  ["warrant", "completed_offering"],
    "424B2":  ["convertible_note", "completed_offering"],
    "424B3":  ["convertible_note", "completed_offering"],
    "424B4":  ["s1_offering", "completed_offering"],
    "8-K":    [
        "warrant",
        "convertible_note",
        "convertible_preferred",
        "equity_line",
        "completed_offering",
    ],
    "S-1":    ["s1_offering"],
    "S-1/A":  ["s1_offering"],
    "10-K":   ["cash_position", "shares_outstanding"],
    "10-Q":   ["cash_position", "shares_outstanding"],
    "EFFECT": ["shelf", "s1_offering"],
    "POS AM":  ["shelf"],
    "FWP":    ["completed_offering"],
}

# Forms whose categories should be refined by content analysis
_REFINE_FORMS: frozenset[str] = frozenset(
    {"8-K", "424B5", "424B2", "424B3", "424B7", "S-3", "S-3/A"}
)


# ── Classification ───────────────────────────────────────────────────────────

def classify_filing(
    filing: Any,
    text: Optional[str] = None,
) -> list[str]:
    """Classify a filing into data categories.

    Parameters
    ----------
    filing:
        An *edgartools* filing object (must have a ``.form`` attribute).
    text:
        Optional plain-text content of the filing.  When provided and the
        form type is one of the multi-category forms, keyword detection
        is used to refine the category list.

    Returns
    -------
    list[str]
        De-duplicated list of category keys (e.g. ``["atm", "warrant"]``).
    """
    form = filing.form.upper().strip()

    # Stage 1 – static mapping
    categories: list[str] = list(FORM_CATEGORY_MAP.get(form, []))

    # Stage 2 – content-based refinement for ambiguous forms
    if form in _REFINE_FORMS and text:
        text_lower = text.lower()

        # ATM detection
        if PATTERNS["atm_keyword"].search(text_lower):
            if "atm" not in categories:
                categories.append("atm")

        # Warrant detection (simple keyword)
        if "warrant" in text_lower and "warrant" not in categories:
            categories.append("warrant")

        # Convertible note detection
        if (
            PATTERNS["convertible_note_kw"].search(text)
            and "convertible_note" not in categories
        ):
            categories.append("convertible_note")

        # Convertible preferred detection
        if (
            PATTERNS["convertible_preferred_kw"].search(text)
            and "convertible_preferred" not in categories
        ):
            categories.append("convertible_preferred")

        # Equity line detection
        if (
            PATTERNS["equity_line_kw"].search(text)
            and "equity_line" not in categories
        ):
            categories.append("equity_line")

        # Completed offering detection
        if (
            PATTERNS["closing_completed"].search(text)
            and "completed_offering" not in categories
        ):
            categories.append("completed_offering")

    return list(set(categories))


# ── Persistence helper ───────────────────────────────────────────────────────

def update_filing_categories(
    session: Session,
    accession_number: str,
    categories: list[str],
) -> None:
    """Persist *categories* for the filing identified by *accession_number*.

    Updates the ``categories`` JSON column on the corresponding
    :class:`~backend.models.database.FilingIndex` row and commits.
    """
    filing_idx = (
        session.query(FilingIndex)
        .filter_by(accession_number=accession_number)
        .first()
    )
    if filing_idx:
        filing_idx.categories = json.dumps(categories)
        session.commit()
        logger.debug(
            "Updated categories for %s → %s",
            accession_number,
            categories,
        )
    else:
        logger.warning(
            "FilingIndex not found for accession %s — categories not saved",
            accession_number,
        )
