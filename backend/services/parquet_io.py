"""
Parquet I/O helpers for SEC Analyzer.

Each data category is stored as one parquet file per ticker inside the
corresponding category directory (e.g. ``data_root/filings_atm/ACME.parquet``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Re-import at runtime to keep the module self-contained.
from backend.config import CATEGORY_DIRS, get_category_dir  # noqa: E402


# ── Write ────────────────────────────────────────────────────────────────────

def write_records_to_parquet(
    records: list[BaseModel],
    category: str,
    ticker: str,
    data_root: Path | str,
) -> Path:
    """Convert a list of Pydantic records to a DataFrame and write to parquet.

    Parameters
    ----------
    records:
        Pydantic model instances for a single ticker / category.
    category:
        Category key (must be present in ``CATEGORY_DIRS``).
    ticker:
        Ticker symbol – used as the parquet filename.
    data_root:
        Root data directory (e.g. ``D:\\tmp``).

    Returns
    -------
    Path
        Absolute path to the written parquet file.
    """
    data_root = Path(data_root)
    category_dir = get_category_dir(data_root, category)
    category_dir.mkdir(parents=True, exist_ok=True)

    # Convert Pydantic models to dicts — use by_alias=True so that
    # ``float_`` is emitted as ``float`` in the parquet column name.
    rows = [r.model_dump(by_alias=True) for r in records]
    df = pd.DataFrame(rows)

    parquet_path = category_dir / f"{ticker.upper()}.parquet"
    df.to_parquet(parquet_path, engine="pyarrow", index=False)
    logger.info(
        "Wrote %d records → %s",
        len(records),
        parquet_path,
    )
    return parquet_path


# ── Read ─────────────────────────────────────────────────────────────────────

def read_parquet_for_ticker(
    category: str,
    ticker: str,
    data_root: Path | str,
) -> pd.DataFrame | None:
    """Read the parquet file for a single ticker in a category.

    Returns ``None`` if the file does not exist.
    """
    data_root = Path(data_root)
    parquet_path = get_category_dir(data_root, category) / f"{ticker.upper()}.parquet"
    if not parquet_path.exists():
        return None
    return pd.read_parquet(parquet_path, engine="pyarrow")


# ── Discover ─────────────────────────────────────────────────────────────────

def list_analyzed_tickers(data_root: Path | str) -> dict[str, list[str]]:
    """Scan all category directories and return which tickers have been analyzed.

    Returns
    -------
    dict[str, list[str]]
        Mapping of ``{category: [ticker, ...]}`` for every category that has
        at least one parquet file.
    """
    data_root = Path(data_root)
    result: dict[str, list[str]] = {}

    for category, dir_name in CATEGORY_DIRS.items():
        cat_dir = data_root / dir_name
        if not cat_dir.is_dir():
            continue
        tickers = sorted(
            p.stem.upper()
            for p in cat_dir.glob("*.parquet")
            if p.is_file()
        )
        if tickers:
            result[category] = tickers

    return result
