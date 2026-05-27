"""
Comprehensive regex pattern library for SEC filing text extraction.

Provides compiled regex patterns for extracting structured data from SEC
filings, plus helper functions for normalizing extracted values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class ExtractedAmount:
    """A dollar amount extracted from filing text."""

    value: float
    raw: str
    multiplier: str = ""  # 'million', 'billion', etc.


# ── Value helpers ────────────────────────────────────────────────────────────

_MULTIPLIERS: dict[str, float] = {
    "thousand": 1e3,
    "million": 1e6,
    "billion": 1e9,
}


def normalize_amount(value_str: str, multiplier: str = "") -> float:
    """Convert a raw numeric string + multiplier word to a ``float``.

    Examples
    --------
    >>> normalize_amount("1,234.56", "million")
    1234560000.0
    >>> normalize_amount("$500")
    500.0
    """
    cleaned = value_str.replace(",", "").replace("$", "").strip()
    value = float(cleaned)
    if multiplier:
        factor = _MULTIPLIERS.get(multiplier.lower().strip(), 1.0)
        value *= factor
    return value


def clean_number(s: str) -> float:
    """Clean an arbitrary numeric string to a ``float``.

    Strips dollar signs, commas, whitespace, and parenthetical negation.

    Examples
    --------
    >>> clean_number("($1,234.56)")
    -1234.56
    >>> clean_number("5,000")
    5000.0
    """
    s = s.strip()
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    s = s.replace(",", "").replace("$", "").replace(" ", "").strip()
    if not s or s == "-":
        return 0.0
    value = float(s)
    return -value if negative else value


# ── Date helpers ─────────────────────────────────────────────────────────────

_DATE_FORMATS: list[str] = [
    "%B %d, %Y",   # January 15, 2024
    "%B %d %Y",    # January 15 2024
    "%b %d, %Y",   # Jan 15, 2024
    "%b %d %Y",    # Jan 15 2024
    "%m/%d/%Y",    # 01/15/2024
    "%Y-%m-%d",    # 2024-01-15
    "%d %B %Y",    # 15 January 2024
    "%B%d, %Y",    # January15, 2024  (no space)
]


def parse_date(date_str: str) -> Optional[str]:
    """Parse various date formats to ``YYYY-MM-DD``.

    Falls back to returning *date_str* as-is when none of the known
    formats match.
    """
    date_str = date_str.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return date_str  # return as-is when we can't parse


# ── Regex match helpers ──────────────────────────────────────────────────────

def extract_first_match(
    pattern: str | re.Pattern[str],
    text: str,
    group: int = 1,
) -> Optional[str]:
    """Return the first capture-group match of *pattern* in *text*."""
    if isinstance(pattern, str):
        pattern = re.compile(pattern, re.IGNORECASE)
    match = pattern.search(text)
    return match.group(group) if match else None


def extract_all_matches(
    pattern: str | re.Pattern[str],
    text: str,
    group: int = 1,
) -> list[str]:
    """Return every capture-group match of *pattern* in *text*."""
    if isinstance(pattern, str):
        pattern = re.compile(pattern, re.IGNORECASE)
    return [m.group(group) for m in pattern.finditer(text)]


# ── Compiled pattern library ─────────────────────────────────────────────────

PATTERNS: dict[str, re.Pattern[str]] = {
    # ── Dollar amounts ───────────────────────────────────────────────────
    "dollar_amount": re.compile(
        r"\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion|thousand)?", re.I
    ),
    "up_to_amount": re.compile(
        r"[Uu]p\s+to\s+\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion)?", re.I
    ),
    "aggregate_amount": re.compile(
        r"(?:aggregate|total)\s+(?:offering\s+)?(?:price|amount|proceeds)"
        r"\s+(?:of\s+)?(?:up\s+to\s+)?\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion)?",
        re.I,
    ),

    # ── ATM ──────────────────────────────────────────────────────────────
    "atm_keyword": re.compile(r"at[- ]the[- ]market|\bATM\b", re.I),
    "atm_program": re.compile(
        r"at[- ]the[- ]market\s+(?:offering|equity\s+offering|program|issuance)"
        r"\s+(?:program\s+)?(?:of\s+)?(?:up\s+to\s+)?\$?\s*([\d,]+(?:\.\d+)?)"
        r"\s*(million|billion)?",
        re.I,
    ),
    "sales_agent": re.compile(
        r"(?:[Ss]ales\s+[Aa]gent|[Pp]lacement\s+[Aa]gent|[Aa]gent)[:\s]+"
        r"([A-Z][\w\s,&\.]+?)"
        r"(?:\.|\n|$|has\s+agreed|is\s+acting|are\s+acting)"
    ),
    "commission_rate": re.compile(
        r"(?:commission|compensation|fee)\s+(?:of\s+|equal\s+to\s+)?"
        r"(?:up\s+to\s+)?(\d+(?:\.\d+)?)\s*%",
        re.I,
    ),

    # ── Shelf ────────────────────────────────────────────────────────────
    "shelf_amount": re.compile(
        r"(?:shelf\s+registration|universal\s+shelf)\s+(?:statement\s+)?"
        r"(?:of\s+)?(?:up\s+to\s+)?\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion)?",
        re.I,
    ),
    "file_number": re.compile(
        r"(?:file\s+(?:no|number|#)[:\.]?\s*|Registration\s+No\s*[:\.]?\s*)(333-\d+)",
        re.I,
    ),
    "effective_date": re.compile(
        r"(?:declared\s+effective|became\s+effective|effective\s+(?:on|as\s+of))"
        r"\s+([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})",
        re.I,
    ),

    # ── Warrants ─────────────────────────────────────────────────────────
    "exercise_price": re.compile(
        r"exercise\s+price\s+(?:of\s+|equal\s+to\s+|is\s+)?"
        r"\$\s*([\d,.]+)\s*(?:per\s+(?:share|warrant))?",
        re.I,
    ),
    "warrant_count": re.compile(
        r"(?:warrants?\s+to\s+purchase\s+(?:up\s+to\s+)?"
        r"|(?:issue|issu\w+)\s+(?:up\s+to\s+)?)?"
        r"([\d,]+)\s+(?:common\s+)?warrants?",
        re.I,
    ),
    "warrant_shares": re.compile(
        r"warrants?\s+to\s+purchase\s+(?:up\s+to\s+)?([\d,]+)\s+shares",
        re.I,
    ),
    "warrant_expiry": re.compile(
        r"(?:warrants?\s+)?(?:expir\w+|exercisable\s+until)"
        r"\s+(?:on\s+)?([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})",
        re.I,
    ),
    "warrant_exercisable": re.compile(
        r"exercisable\s+(?:on\s+and\s+after\s+|beginning\s+|from\s+)"
        r"([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})",
        re.I,
    ),
    "pre_funded_warrant": re.compile(r"pre[- ]?funded\s+warrants?", re.I),
    "cashless_exercise": re.compile(
        r"cashless\s+exercise|net\s+(?:share\s+)?exercise", re.I
    ),

    # ── Convertible Notes ────────────────────────────────────────────────
    "conversion_price": re.compile(
        r"(?:initial\s+)?conversion\s+price\s+"
        r"(?:of\s+|equal\s+to\s+|is\s+|approximately\s+)?"
        r"\$\s*([\d,.]+)\s*(?:per\s+share)?",
        re.I,
    ),
    "principal_amount": re.compile(
        r"(?:aggregate\s+)?principal\s+(?:amount\s+)?(?:of\s+)?"
        r"\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion)?",
        re.I,
    ),
    "maturity_date": re.compile(
        r"(?:matur\w+|mature|maturing)\s+(?:date\s+)?"
        r"(?:of\s+|on\s+|is\s+)?([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})",
        re.I,
    ),
    "interest_rate": re.compile(
        r"(?:bears?\s+interest(?:\s+[\w\s]{1,15}?)?|interest\s+(?:rate\s+)?(?:of\s+|at\s+)?|coupon\s+(?:rate\s+)?(?:of\s+)?)\s*(\d+(?:\.\d+)?)\s*%\s*(?:per\s+annum)?",
        re.I,
    ),
    "convertible_note_kw": re.compile(
        r"convertible\s+(?:senior\s+)?(?:subordinated\s+)?(?:unsecured\s+)?notes?",
        re.I,
    ),
    "senior_note": re.compile(r"senior\s+(?:convertible\s+)?notes?", re.I),

    # ── Convertible Preferred ────────────────────────────────────────────
    "convertible_preferred_kw": re.compile(
        r"(?:series\s+[A-Z]\s+)?convertible\s+preferred\s+stock", re.I
    ),
    "series_designation": re.compile(
        r"Series\s+([A-Z](?:-\d+)?)\s+(?:Convertible\s+)?Preferred", re.I
    ),
    "dividend_rate": re.compile(
        r"dividend\s+(?:rate\s+)?(?:of\s+|equal\s+to\s+)?(\d+(?:\.\d+)?)\s*%",
        re.I,
    ),
    "liquidation_preference": re.compile(
        r"liquidation\s+preference\s+(?:of\s+)?\$\s*([\d,.]+)\s*(?:per\s+share)?",
        re.I,
    ),

    # ── Equity Line ──────────────────────────────────────────────────────
    "equity_line_kw": re.compile(
        r"(?:equity\s+line|(?:common\s+stock\s+)?purchase\s+agreement"
        r"|committed\s+equity\s+facility|equity\s+distribution)",
        re.I,
    ),
    "equity_line_capacity": re.compile(
        r"(?:equity\s+line|purchase\s+agreement|committed\s+equity)"
        r"\s+[\w\s]*?(?:up\s+to\s+|of\s+|for\s+)?"
        r"\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion)?",
        re.I,
    ),
    "eloc_counterparty": re.compile(
        r"(?:entered\s+into|agreement\s+with)\s+"
        r"([A-Z][\w\s,]+?)"
        r"(?:\s+(?:pursuant|under|whereby|providing|for))",
        re.I,
    ),
    "vwap_pricing": re.compile(
        r"(\d+(?:\.\d+)?)\s*%\s*(?:of\s+)?(?:the\s+)?"
        r"(?:daily\s+)?(?:volume[- ]?weighted\s+average\s+price|VWAP)",
        re.I,
    ),
    "floor_price": re.compile(
        r"(?:floor|minimum)\s+(?:price\s+)?(?:of\s+)?\$\s*([\d,.]+)", re.I
    ),

    # ── S-1 Offerings ────────────────────────────────────────────────────
    "shares_offered": re.compile(
        r"([\d,]+)\s+shares?\s+of\s+(?:our\s+)?"
        r"(?:common\s+stock|Class\s+[A-Z]\s+common\s+stock)",
        re.I,
    ),
    "offering_price": re.compile(
        r"(?:initial\s+)?(?:public\s+)?offering\s+price\s+"
        r"(?:per\s+(?:share|unit)\s+)?(?:of\s+|is\s+)?\$\s*([\d,.]+)",
        re.I,
    ),
    "price_range": re.compile(
        r"between\s+\$\s*([\d,.]+)\s+and\s+\$\s*([\d,.]+)\s+per\s+share",
        re.I,
    ),
    "lock_up": re.compile(
        r"lock[- ]?up\s+(?:period\s+)?(?:of\s+)?(\d+)\s*(?:day|calendar)",
        re.I,
    ),
    "over_allotment": re.compile(
        r"(?:over[- ]?allotment|greenshoe)\s+(?:option\s+)?"
        r"(?:of\s+|to\s+purchase\s+)?(?:up\s+to\s+)?"
        r"([\d,]+)\s+(?:additional\s+)?shares?",
        re.I,
    ),

    # ── Completed Offerings ──────────────────────────────────────────────
    "closing_completed": re.compile(
        r"(?:closed?|completed?|consummated?)\s+(?:the\s+)?"
        r"(?:offering|issuance|sale|placement)",
        re.I,
    ),
    "closing_date": re.compile(
        r"(?:clos(?:e|ing|ed)\s+(?:on|date))\s+(?:or\s+about\s+)?"
        r"([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})",
        re.I,
    ),
    "net_proceeds": re.compile(
        r"net\s+proceeds\s+(?:of\s+)?(?:approximately\s+)?"
        r"\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion)?",
        re.I,
    ),
    "gross_proceeds": re.compile(
        r"gross\s+proceeds\s+(?:of\s+)?(?:approximately\s+)?"
        r"\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion)?",
        re.I,
    ),

    # ── General ──────────────────────────────────────────────────────────
    "date_mdy": re.compile(r"([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})"),
    "date_ymd": re.compile(r"(\d{4}-\d{2}-\d{2})"),
    "underwriter": re.compile(
        r"(?:[Uu]nderwriter|[Bb]ook[- ]?running\s+[Mm]anager|[Ll]ead\s+[Mm]anager)"
        r"[:\s]+([A-Z][\w\s,&\.]+?)(?:\.|\n|$)"
    ),
}


# ── High-level detection helpers ─────────────────────────────────────────────

def detect_offering_type(text: str) -> str:
    """Classify the dominant offering type mentioned in *text*.

    Returns one of: ``'ATM'``, ``'RDO'``, ``'Underwritten'``,
    ``'Private Placement'``, ``'Convertible Note'``, ``'Convertible Preferred'``,
    ``'Warrant Exercise'``, ``'Shelf'``, ``'S-1 IPO/Follow-On'``, ``'Equity Line'``,
    or ``'Unknown'``.
    """
    text_lower = text.lower()

    # Check for Convertible Preferred first to avoid general "convertible" note clash
    if (
        PATTERNS["convertible_preferred_kw"].search(text)
        or "convertible preferred" in text_lower
    ):
        return "Convertible Preferred"

    # Check for Convertible Note before ATM to avoid matching exclusion covenants
    if PATTERNS["convertible_note_kw"].search(text):
        return "Convertible Note"

    # ATM check (now safe from convertible note false matches)
    if PATTERNS["atm_keyword"].search(text_lower) or "atm agreement" in text_lower or "atm prospectus" in text_lower:
        return "ATM"

    if "registered direct offering" in text_lower or "registered direct" in text_lower:
        return "RDO"

    if "private placement" in text_lower or "privately placed" in text_lower:
        return "Private Placement"

    if (
        "warrant exercise" in text_lower
        or "exercise of warrants" in text_lower
        or "exercised warrants" in text_lower
    ):
        return "Warrant Exercise"

    if PATTERNS["equity_line_kw"].search(text):
        return "Equity Line"

    if "underwritten" in text_lower or "underwriting agreement" in text_lower:
        return "Underwritten"

    if (
        "initial public offering" in text_lower
        or "ipo" in text_lower
        or re.search(r"\bS-1\b", text)
    ):
        return "S-1 IPO/Follow-On"

    if "shelf registration" in text_lower or "universal shelf" in text_lower:
        return "Shelf"

    return "Unknown"


def detect_filing_keywords(text: str) -> dict[str, bool]:
    """Scan *text* for the presence of major SEC filing keywords.

    Returns a dict with boolean flags:

    - ``has_atm``
    - ``has_warrant``
    - ``has_convertible_note``
    - ``has_convertible_preferred``
    - ``has_equity_line``
    - ``has_offering_closing``
    """
    return {
        "has_atm": bool(PATTERNS["atm_keyword"].search(text)),
        "has_warrant": "warrant" in text.lower(),
        "has_convertible_note": bool(PATTERNS["convertible_note_kw"].search(text)),
        "has_convertible_preferred": bool(
            PATTERNS["convertible_preferred_kw"].search(text)
        ),
        "has_equity_line": bool(PATTERNS["equity_line_kw"].search(text)),
        "has_offering_closing": bool(PATTERNS["closing_completed"].search(text)),
    }


def extract_dollar_amount(
    text: str,
    pattern_name: str = "up_to_amount",
) -> Optional[float]:
    """Extract and normalize the first dollar amount matched by *pattern_name*.

    Parameters
    ----------
    text:
        Source text to search.
    pattern_name:
        Key into :data:`PATTERNS` (default ``'up_to_amount'``).

    Returns
    -------
    float or None
        The normalized dollar value, or ``None`` if no match.
    """
    pat = PATTERNS.get(pattern_name)
    if pat is None:
        return None

    match = pat.search(text)
    if not match:
        return None

    try:
        value_str = match.group(1)
        multiplier = match.group(2) or "" if match.lastindex and match.lastindex >= 2 else ""
        return normalize_amount(value_str, multiplier)
    except (IndexError, ValueError):
        return None
