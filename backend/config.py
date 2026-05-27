"""
SEC Analyzer — application configuration.

All path helpers accept a *data_root* so the user can override the default
storage location on a per-analysis-run basis.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

# ── SEC / Edgar identity ─────────────────────────────────────────────────────
SEC_IDENTITY: str = "SECAnalyzer admin@secanalyzer.local"

# ── Default data paths ───────────────────────────────────────────────────────
DEFAULT_DATA_ROOT: Path = Path(r"D:\tmp")

# ── Default date range ───────────────────────────────────────────────────────
DEFAULT_DATE_FROM: str = "2020-01-01"
DEFAULT_DATE_TO: str = date.today().isoformat()

# ── Filing forms to search for ───────────────────────────────────────────────
FILING_FORMS: list[str] = [
    "S-3",
    "S-3/A",
    "S-3ASR",
    "S-1",
    "S-1/A",
    "8-K",
    "10-K",
    "10-K/A",
    "10-Q",
    "10-Q/A",
    "424B2",
    "424B3",
    "424B4",
    "424B5",
    "424B7",
    "EFFECT",
    "POS AM",
    "FWP",
]

# ── Category → directory name mapping ────────────────────────────────────────
CATEGORY_DIRS: dict[str, str] = {
    "atm": "filings_atm",
    "shelf": "filings_shelf",
    "warrant": "filings_warrant",
    "convertible_note": "filings_convertible_note",
    "convertible_preferred": "filings_convertible_preferred",
    "equity_line": "filings_equity_line",
    "s1_offering": "filings_s1_offering",
    "cash_position": "filings_cash_position",
    "shares_outstanding": "filings_shares_outstanding",
    "completed_offering": "filings_completed_offering",
}

# ── Database ─────────────────────────────────────────────────────────────────
DB_FILENAME: str = "sec_analyzer.db"


# ── Path helpers ─────────────────────────────────────────────────────────────

def get_db_path(data_root: Path | str = DEFAULT_DATA_ROOT) -> Path:
    """Return the full path to the SQLite database file."""
    return Path(data_root) / DB_FILENAME


def get_category_dir(data_root: Path | str, category: str) -> Path:
    """Return the directory for a given filing category.

    Raises ``KeyError`` if *category* is not in :data:`CATEGORY_DIRS`.
    """
    return Path(data_root) / CATEGORY_DIRS[category]


def get_cache_dir(data_root: Path | str = DEFAULT_DATA_ROOT) -> Path:
    """Return the path to the filing-text cache directory."""
    return Path(data_root) / "sec_cache"
