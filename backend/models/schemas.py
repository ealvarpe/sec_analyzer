"""
Pydantic v2 schemas for the SEC Analyzer.

There is one schema per data category (10 total), plus request/status models
used by the API layer.  Every field carries a sensible default so that records
can be constructed with partial data.

The ``CATEGORY_SCHEMA_MAP`` at the bottom maps category name strings to their
corresponding Pydantic model class.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ═══════════════════════════════════════════════════════════════════════════════
# Data-category record schemas
# ═══════════════════════════════════════════════════════════════════════════════


class ATMRecord(BaseModel):
    """At-the-market offering record."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    ticker: str = ""
    date: str = ""
    filing_id: str = ""
    offering_type: str = "ATM"
    registration_status: str = ""
    filing_edgar_url: str = ""
    raw_data_json: str = ""
    security_name: str = ""
    remaining_atm_capacity: int = 0
    total_atm_capacity: int = 0
    atm_limited_by_baby_shelf: bool = False
    placement_agent: str = ""
    agreement_start_date: str = ""
    last_update_date: str = ""
    commission_rate: float | None = None
    shelf_registration_number: str = ""
    related_shelf_filing_url: str = ""
    shares_sold_to_date: int | None = None
    amount_raised_to_date: int | None = None
    filing_form: str = ""
    accession_number: str = ""
    source_downloaded_at: str = ""


class ShelfRecord(BaseModel):
    """Shelf registration record."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    ticker: str = ""
    date: str = ""
    filing_id: str = ""
    offering_type: str = "Shelf"
    registration_status: str = ""
    filing_edgar_url: str = ""
    raw_data_json: str = ""
    security_name: str = ""
    current_raisable_amount: int = 0
    total_shelf_capacity: int = 0
    baby_shelf_restriction: bool = False
    total_amount_raised: int = 0
    total_amount_raised_last_12_mo_under_ib6: int = 0
    outstanding_shares: int = 0
    float_: int = Field(default=0, alias="float")
    highest_60_day_close: float | None = None
    price_to_exceed_baby_shelf: float | None = None
    ib6_float_value: int = 0
    last_banker: str = ""
    effect_date: str = ""
    expiration_date: str = ""
    last_update_date: str = ""
    shelf_registration_number: str = ""
    filing_form: str = ""
    securities_registered: str = ""
    initial_filing_date: str = ""
    amendment_count: int = 0
    accession_number: str = ""
    is_asr: bool = False
    source_downloaded_at: str = ""


class WarrantRecord(BaseModel):
    """Warrant record."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    ticker: str = ""
    date: str = ""
    filing_id: str = ""
    offering_type: str = "Warrant"
    registration_status: str = ""
    filing_edgar_url: str = ""
    raw_data_json: str = ""
    security_name: str = ""
    remaining_warrants_outstanding: int = 0
    exercise_price: float | None = None
    total_warrants_issued: int = 0
    known_owners: str = ""
    underwriter_placement_agent: str = ""
    price_protection: str = ""
    pp_clause: str = ""
    issue_date: str = ""
    exercisable_date: str = ""
    expiration_date: str = ""
    last_update_date: str = ""
    warrant_type: str = ""
    shares_per_warrant: float | None = None
    cashless_exercise: bool | None = None
    anti_dilution_type: str = ""
    related_offering_date: str = ""
    accession_number: str = ""
    filing_form: str = ""
    source_downloaded_at: str = ""


class ConvertibleNoteRecord(BaseModel):
    """Convertible note record."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    ticker: str = ""
    date: str = ""
    filing_id: str = ""
    offering_type: str = "Convertible Note"
    registration_status: str = ""
    filing_edgar_url: str = ""
    raw_data_json: str = ""
    security_name: str = ""
    remaining_shares_to_be_issued_when_converted: int = 0
    remaining_principal_amount: int = 0
    conversion_price: float | None = None
    total_shares_issued_when_converted: int = 0
    total_principal_amount: int = 0
    known_owners: str = ""
    underwriter_placement_agent: str = ""
    price_protection: str = ""
    pp_clause: str = ""
    issue_date: str = ""
    convertible_date: str = ""
    maturity_date: str = ""
    last_update_date: str = ""
    interest_rate: float | None = None
    interest_payment_frequency: str = ""
    conversion_type: str = ""
    is_senior: bool | None = None
    redemption_date: str = ""
    redemption_price_pct: float | None = None
    initial_conversion_premium: float | None = None
    accession_number: str = ""
    filing_form: str = ""
    source_downloaded_at: str = ""


class ConvertiblePreferredRecord(BaseModel):
    """Convertible preferred stock record."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    ticker: str = ""
    date: str = ""
    filing_id: str = ""
    offering_type: str = "Convertible Preferred"
    registration_status: str = ""
    filing_edgar_url: str = ""
    raw_data_json: str = ""
    security_name: str = ""
    remaining_shares_to_be_issued_when_converted: int = 0
    remaining_dollar_amount: int = 0
    conversion_price: float | None = None
    total_shares_issued_when_converted: float | None = None
    total_dollar_amount_issued: int = 0
    known_owners: str = ""
    underwriter_placement_agent: str = ""
    price_protection: str = ""
    pp_clause: str = ""
    issue_date: str = ""
    convertible_date: str = ""
    maturity_date: str = ""
    last_update_date: str = ""
    series_designation: str = ""
    dividend_rate: float | None = None
    liquidation_preference: float | None = None
    voting_rights: str = ""
    mandatory_conversion: bool | None = None
    mandatory_conversion_date: str = ""
    shares_authorized: int | None = None
    accession_number: str = ""
    filing_form: str = ""
    source_downloaded_at: str = ""


class EquityLineRecord(BaseModel):
    """Equity line of credit record."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    ticker: str = ""
    date: str = ""
    filing_id: str = ""
    offering_type: str = "Equity Line"
    registration_status: str = ""
    filing_edgar_url: str = ""
    raw_data_json: str = ""
    security_name: str = ""
    remaining_equity_line_capacity: int = 0
    total_equity_line_capacity: int = 0
    agreement_start_date: str = ""
    agreement_end_date: str = ""
    last_update_date: str = ""
    owners: str = ""
    current_shares_equivalent: str = ""
    counterparty: str = ""
    pricing_mechanism: str = ""
    maximum_shares: int | None = None
    minimum_price_threshold: float | None = None
    commitment_fee_shares: int | None = None
    accession_number: str = ""
    filing_form: str = ""
    source_downloaded_at: str = ""


class S1OfferingRecord(BaseModel):
    """S-1 offering record."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    ticker: str = ""
    date: str = ""
    filing_id: str = ""
    offering_type: str = "S-1 Offering"
    registration_status: str = ""
    filing_edgar_url: str = ""
    raw_data_json: str = ""
    security_name: str = ""
    anticipated_deal_size: str = ""
    status: str = ""
    underwriter_placement_agent: str = ""
    s_1_filing_date: str = ""
    warrant_coverage: int = 0
    last_update_date: str = ""
    final_deal_size: str = ""
    final_pricing: str = ""
    final_shares_offered: int = 0
    final_warrant_coverage: int = 0
    exercise_price: str = ""
    offering_type_detail: str = ""
    lock_up_period_days: int | None = None
    over_allotment_shares: int | None = None
    over_allotment_pct: float | None = None
    price_range_low: float | None = None
    price_range_high: float | None = None
    use_of_proceeds: str = ""
    shares_by_selling_holders: int | None = None
    accession_number: str = ""
    filing_form: str = ""
    amendment_number: int | None = None
    source_downloaded_at: str = ""


class CashPositionRecord(BaseModel):
    """Cash position snapshot record."""

    model_config = ConfigDict(from_attributes=True)

    ticker: str = ""
    date: str = ""
    name: str = ""
    cash: float | None = None
    recent_offerings: int = 0
    quarterly_operating_cashflow: float | None = None
    last_cash_date: str = ""
    short_term_investments: float | None = None
    cash_and_investments_total: float | None = None
    total_current_assets: float | None = None
    total_current_liabilities: float | None = None
    working_capital: float | None = None
    total_debt: float | None = None
    quarterly_burn_rate: float | None = None
    months_of_cash_remaining: float | None = None
    filing_form: str = ""
    period_type: str = ""
    accession_number: str = ""
    source_downloaded_at: str = ""


class SharesOutstandingRecord(BaseModel):
    """Shares outstanding snapshot record."""

    model_config = ConfigDict(from_attributes=True)

    ticker: str = ""
    date: str = ""
    name: str = ""
    shares_outstanding: float | None = None
    current_outstanding: float | None = None
    is_current: bool = False
    authorized_shares: float | None = None
    preferred_shares_outstanding: float | None = None
    treasury_shares: float | None = None
    fully_diluted_shares: float | None = None
    options_outstanding: float | None = None
    warrants_outstanding_at_date: float | None = None
    pct_change_qoq: float | None = None
    filing_form: str = ""
    accession_number: str = ""
    source_downloaded_at: str = ""


class CompletedOfferingRecord(BaseModel):
    """Completed public/private offering record."""

    model_config = ConfigDict(from_attributes=True)

    ticker: str = ""
    date: str = ""
    offering_id: str = ""
    symbol: str = ""
    pricing_datetime: str = ""
    type: str = ""
    method: str = ""
    shares_equivalent: float | None = None
    price: float | None = None
    warrants: float | None = None
    offering_amount: int = 0
    bank: str = ""
    investors: str = ""
    pre_funded_warrants: int | None = None
    pre_funded_exercise_price: float | None = None
    placement_agent_warrants: int | None = None
    discount_to_market_pct: float | None = None
    gross_proceeds: float | None = None
    net_proceeds: float | None = None
    agent_commission_pct: float | None = None
    closing_date: str = ""
    registration_type: str = ""
    accession_number: str = ""
    filing_form: str = ""
    related_shelf_number: str = ""
    source_downloaded_at: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# API / orchestration schemas
# ═══════════════════════════════════════════════════════════════════════════════


class AnalysisRequest(BaseModel):
    """Payload sent from the UI to kick off an analysis run."""

    model_config = ConfigDict(from_attributes=True)

    tickers: list[str]
    data_root: str = r"D:\tmp"
    date_from: str = "2020-01-01"
    date_to: str = Field(default_factory=lambda: date.today().isoformat())


class AnalysisStatus(BaseModel):
    """Real-time status update for a single ticker during an analysis run."""

    model_config = ConfigDict(from_attributes=True)

    ticker: str = ""
    status: str = ""
    progress: float = 0.0
    current_category: str | None = None
    categories_done: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# Category → schema class map
# ═══════════════════════════════════════════════════════════════════════════════

CATEGORY_SCHEMA_MAP: dict[str, type[BaseModel]] = {
    "atm": ATMRecord,
    "shelf": ShelfRecord,
    "warrant": WarrantRecord,
    "convertible_note": ConvertibleNoteRecord,
    "convertible_preferred": ConvertiblePreferredRecord,
    "equity_line": EquityLineRecord,
    "s1_offering": S1OfferingRecord,
    "cash_position": CashPositionRecord,
    "shares_outstanding": SharesOutstandingRecord,
    "completed_offering": CompletedOfferingRecord,
}
