"""
BeautifulSoup-based parser for SEC filing HTML documents.

Handles iXBRL cleanup, table extraction, cover-page text extraction,
and structured key-value pair mining from HTML filings.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import pandas as pd
from bs4 import BeautifulSoup, NavigableString

logger = logging.getLogger(__name__)


class SECHTMLParser:
    """Parse and extract structured data from SEC filing HTML.

    Parameters
    ----------
    html_content:
        Raw HTML string from an SEC filing.

    Attributes
    ----------
    soup : BeautifulSoup
        Cleaned parse tree.
    full_text : str
        Flattened text content of the entire document.
    """

    def __init__(self, html_content: str) -> None:
        self.soup = BeautifulSoup(html_content, "lxml")
        self._clean()
        self.full_text: str = self.soup.get_text(separator="\n", strip=True)

    # ── Internal cleanup ─────────────────────────────────────────────────

    def _clean(self) -> None:
        """Remove noise from the parse tree while preserving visible text."""
        # ── Extract iXBRL facts before unwrapping ────────────────────────
        self.ixbrl_facts: list[dict[str, str | None]] = []
        for ix_tag in self.soup.find_all(re.compile(r"^ix:")):
            fact: dict[str, str | None] = {
                "tag": ix_tag.name,
                "name": ix_tag.get("name"),
                "value": ix_tag.get_text(strip=True) or ix_tag.get("value"),
                "format": ix_tag.get("format"),
                "scale": ix_tag.get("scale"),
                "unitRef": ix_tag.get("unitref") or ix_tag.get("unitRef"),
                "contextRef": ix_tag.get("contextref") or ix_tag.get("contextRef"),
                "decimals": ix_tag.get("decimals"),
            }
            self.ixbrl_facts.append(fact)

        # Unwrap iXBRL tags (``<ix:…>``) – keep their text content
        for ix_tag in self.soup.find_all(re.compile(r"^ix:")):
            ix_tag.unwrap()

        # Remove <script> and <style> elements entirely
        for tag in self.soup.find_all(["script", "style"]):
            tag.decompose()

        # Remove elements hidden via inline CSS
        for hidden in self.soup.find_all(
            style=re.compile(r"display\s*:\s*none", re.I)
        ):
            hidden.decompose()

    def get_ixbrl_fact(self, concept_name: str) -> Optional[dict[str, str | None]]:
        """Return the first iXBRL fact whose ``name`` ends with *concept_name*.

        Parameters
        ----------
        concept_name:
            The XBRL concept suffix to match (e.g.
            ``"EntityCommonStockSharesOutstanding"``).

        Returns
        -------
        dict or None
            The matching fact dict, or ``None`` if not found.
        """
        for fact in self.ixbrl_facts:
            name = fact.get("name") or ""
            if name.endswith(concept_name):
                return fact
        return None


    # ── Cover page ───────────────────────────────────────────────────────

    def get_cover_page_text(self, max_chars: int = 8_000) -> str:
        """Extract cover page text up to the first major section boundary.

        The cover page is defined as the text before any of the following
        markers (case-insensitive):

        - TABLE OF CONTENTS
        - ABOUT THIS PROSPECTUS
        - PROSPECTUS SUPPLEMENT SUMMARY
        - SUMMARY

        If no marker is found the first *max_chars* characters are returned.
        """
        markers = [
            "TABLE OF CONTENTS",
            "ABOUT THIS PROSPECTUS",
            "PROSPECTUS SUPPLEMENT SUMMARY",
            "SUMMARY",
        ]
        upper_text = self.full_text.upper()
        for marker in markers:
            idx = upper_text.find(marker)
            # Only treat it as a boundary if it appears after some content
            if idx > 500:
                return self.full_text[:idx]
        return self.full_text[:max_chars]

    # ── Table extraction ─────────────────────────────────────────────────

    def extract_all_tables(self) -> list[pd.DataFrame]:
        """Extract every ``<table>`` element as a :class:`~pandas.DataFrame`.

        Empty tables (all-whitespace rows) are skipped.  Column counts are
        normalized so that every row in a given DataFrame has the same number
        of columns.
        """
        tables: list[pd.DataFrame] = []
        for table in self.soup.find_all("table"):
            rows: list[list[str]] = []
            for tr in table.find_all("tr"):
                cells: list[str] = []
                for td in tr.find_all(["td", "th"]):
                    text = td.get_text(strip=True)
                    colspan = int(td.get("colspan", 1))
                    cells.append(text)
                    # Pad for merged columns
                    cells.extend([""] * (colspan - 1))
                if any(c.strip() for c in cells):
                    rows.append(cells)
            if rows:
                max_cols = max(len(r) for r in rows)
                rows = [r + [""] * (max_cols - len(r)) for r in rows]
                try:
                    tables.append(pd.DataFrame(rows))
                except Exception:  # noqa: BLE001
                    logger.debug("Skipped unparseable table", exc_info=True)
        return tables

    def find_offering_terms_table(self) -> Optional[pd.DataFrame]:
        """Locate the offering-terms / pricing table on a prospectus cover page.

        The heuristic scores each ``<table>`` by counting how many
        offering-related keywords appear in its text; the first table with
        ≥ 2 hits is returned.

        Returns ``None`` if no suitable table is found.
        """
        keywords = [
            "offering price",
            "exercise price",
            "net proceeds",
            "placement agent",
            "underwriting discount",
            "proceeds to us",
            "proceeds, before",
            "agent fees",
            "per share",
            "per warrant",
        ]

        for table in self.soup.find_all("table"):
            text_lower = table.get_text().lower()
            hits = sum(1 for kw in keywords if kw in text_lower)
            if hits >= 2:
                rows: list[list[str]] = []
                for tr in table.find_all("tr"):
                    cells = [
                        td.get_text(strip=True)
                        for td in tr.find_all(["td", "th"])
                    ]
                    if any(c.strip() for c in cells):
                        rows.append(cells)
                if rows:
                    max_cols = max(len(r) for r in rows)
                    rows = [r + [""] * (max_cols - len(r)) for r in rows]
                    return pd.DataFrame(rows)
        return None

    # ── Structured pair extraction ───────────────────────────────────────

    def extract_two_column_pairs(self) -> dict[str, str]:
        """Extract key→value pairs from every strict 2-column table.

        A table qualifies if *every* row contains exactly two ``<td>`` or
        ``<th>`` cells and the table has at least 2 rows.
        """
        pairs: dict[str, str] = {}
        for table in self.soup.find_all("table"):
            trs = table.find_all("tr")
            # Check that every non-empty row has exactly 2 cells
            is_two_col = all(
                len(tr.find_all(["td", "th"])) == 2
                for tr in trs
                if tr.find_all(["td", "th"])
            )
            if is_two_col and len(trs) >= 2:
                for tr in trs:
                    cells = tr.find_all(["td", "th"])
                    if len(cells) == 2:
                        key = cells[0].get_text(strip=True)
                        val = cells[1].get_text(strip=True)
                        if key:
                            pairs[key] = val
        return pairs

    def extract_bold_pairs(self) -> dict[str, str]:
        """Extract label→value pairs where the label is bold.

        Walks every ``<b>`` and ``<strong>`` tag and collects sibling text
        up to the next bold tag.
        """
        pairs: dict[str, str] = {}
        for bold in self.soup.find_all(["b", "strong"]):
            label = bold.get_text(strip=True)
            value_parts: list[str] = []
            for sibling in bold.next_siblings:
                if isinstance(sibling, NavigableString):
                    value_parts.append(str(sibling).strip())
                elif sibling.name in ("b", "strong"):
                    break
                else:
                    value_parts.append(sibling.get_text(strip=True))
            value = " ".join(v for v in value_parts if v).strip()
            if label and value:
                pairs[label.rstrip(":")] = value
        return pairs

    # ── Section search ───────────────────────────────────────────────────

    def find_section_text(
        self,
        section_title: str,
        max_chars: int = 5_000,
    ) -> Optional[str]:
        """Find the text content under a heading that contains *section_title*.

        The search looks at ``<h1>``–``<h6>``, ``<p>``, ``<div>``, ``<b>``,
        and ``<strong>`` tags.  Text is collected from subsequent siblings
        until the next heading tag or *max_chars* is reached.
        """
        heading_re = re.compile(r"^h[1-6]$")
        container_re = re.compile(r"^h[1-6]$|^p$|^div$|^b$|^strong$")
        title_lower = section_title.lower()

        for tag in self.soup.find_all(container_re):
            if title_lower in tag.get_text().lower():
                content: list[str] = []
                total_len = 0
                for sibling in tag.find_next_siblings():
                    if sibling.name and heading_re.match(sibling.name):
                        break
                    text = sibling.get_text(strip=True)
                    if text:
                        content.append(text)
                        total_len += len(text)
                    if total_len > max_chars:
                        break
                return "\n".join(content) if content else None
        return None
