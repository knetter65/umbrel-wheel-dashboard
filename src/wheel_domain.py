"""Reference domain rules for the Wheel dashboard.

Money uses Decimal and signed cash-flow convention:
- inflow to the account: positive
- outflow from the account: negative

This module contains no broker connectivity and no order functionality.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

ZERO = Decimal("0")


class CycleState(StrEnum):
    PUT_OPEN = "PUT_OPEN"
    ASSIGNED_NO_CALL = "ASSIGNED_NO_CALL"
    COVERED_CALL_OPEN = "COVERED_CALL_OPEN"
    PARTIALLY_COVERED = "PARTIALLY_COVERED"
    CLOSED_PUT_ONLY = "CLOSED_PUT_ONLY"
    CLOSED_CALLED_AWAY = "CLOSED_CALLED_AWAY"
    CLOSED_STOCK_SOLD = "CLOSED_STOCK_SOLD"
    UNRESOLVED = "UNRESOLVED"


class ThesisState(StrEnum):
    INTACT = "INTACT"
    WATCH = "WATCH"
    THESIS_BROKEN = "THESIS_BROKEN"
    REVIEW_DUE = "REVIEW_DUE"


class DataQuality(StrEnum):
    LIVE = "LIVE"
    DELAYED = "DELAYED"
    STALE = "STALE"
    MISSING = "MISSING"


def D(value: Decimal | str | int) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def full_assignment_obligation(strike: Decimal | str, contracts: Decimal | str,
                               multiplier: Decimal | str = 100) -> Decimal:
    strike_d, contracts_d, multiplier_d = D(strike), D(contracts), D(multiplier)
    if strike_d < ZERO or contracts_d < ZERO or multiplier_d <= ZERO:
        raise ValueError("strike/contracts must be non-negative and multiplier positive")
    return strike_d * contracts_d * multiplier_d


def covered_call_coverage(shares: Decimal | str, short_call_contracts: Decimal | str,
                          multiplier: Decimal | str = 100) -> tuple[Decimal, Decimal, str]:
    shares_d, contracts_d, multiplier_d = D(shares), D(short_call_contracts), D(multiplier)
    if shares_d < ZERO or contracts_d < ZERO or multiplier_d <= ZERO:
        raise ValueError("shares/contracts must be non-negative and multiplier positive")
    required = contracts_d * multiplier_d
    covered = min(shares_d, required)
    deficit = max(ZERO, required - shares_d)
    status = "COVERED" if deficit == ZERO else ("PARTIAL" if covered > ZERO else "UNCOVERED")
    return covered, deficit, status


@dataclass(frozen=True)
class CycleCashFlows:
    """Inputs for break-even calculations for one canonical Wheel cycle.

    `stock_net_cashflow` is the signed sum of assignment/purchases, stock sales,
    stock commissions and stock taxes. Assignment/purchases are therefore negative.

    Closed option fields include only legs whose economic P&L is fixed by close,
    expiration, assignment or exercise. `open_option_cashflow` contains credits and
    debits already paid for currently open legs. `open_option_close_cost` is a
    positive conservative amount needed to close all open option liabilities now.
    """

    remaining_shares: Decimal
    stock_net_cashflow: Decimal
    closed_put_net_pnl: Decimal = ZERO
    closed_call_net_pnl: Decimal = ZERO
    open_option_cashflow: Decimal = ZERO
    dividends_net: Decimal = ZERO
    open_option_close_cost: Decimal = ZERO

    def __post_init__(self) -> None:
        if self.remaining_shares <= ZERO:
            raise ValueError("break-even requires positive remaining shares")
        if self.open_option_close_cost < ZERO:
            raise ValueError("open_option_close_cost must be a positive cost")

    @property
    def realized_option_pnl(self) -> Decimal:
        return self.closed_put_net_pnl + self.closed_call_net_pnl

    def broker_reconciliation_basis(self, broker_cost_basis: Decimal | str) -> Decimal:
        """Return supplied broker basis unchanged; never relabel it as economic basis."""
        basis = D(broker_cost_basis)
        if basis < ZERO:
            raise ValueError("broker cost basis cannot be negative")
        return basis

    def locked_break_even(self, *, include_dividends: bool = False) -> Decimal:
        """Cash-recovery threshold using only fixed/realized option results."""
        offsets = self.realized_option_pnl
        if include_dividends:
            offsets += self.dividends_net
        return (-self.stock_net_cashflow - offsets) / self.remaining_shares

    def cashflow_break_even(self, *, include_dividends: bool = False) -> Decimal:
        """Threshold including cash from open options; explicitly not fully realized."""
        offsets = self.realized_option_pnl + self.open_option_cashflow
        if include_dividends:
            offsets += self.dividends_net
        return (-self.stock_net_cashflow - offsets) / self.remaining_shares

    def liquidation_break_even(self, *, include_dividends: bool = False) -> Decimal:
        """Required stock sale price after conservatively buying back open options."""
        offsets = self.realized_option_pnl + self.open_option_cashflow
        if include_dividends:
            offsets += self.dividends_net
        return (-self.stock_net_cashflow - offsets + self.open_option_close_cost) / self.remaining_shares

    def projected_call_assignment_pnl(self, call_strike: Decimal | str,
                                      shares_called: Decimal | str,
                                      *, include_dividends: bool = False) -> Decimal:
        """Projected cycle cash P&L if covered shares are called at the strike.

        The open call credit is already in `open_option_cashflow`; no close cost is
        included because assignment terminates the option obligation.
        """
        called = D(shares_called)
        if called <= ZERO or called > self.remaining_shares:
            raise ValueError("shares_called must be positive and no greater than remaining shares")
        if called != self.remaining_shares:
            raise ValueError("partial call-away projection needs a price assumption for residual shares")
        total = (
            self.stock_net_cashflow
            + self.realized_option_pnl
            + self.open_option_cashflow
            + D(call_strike) * called
        )
        if include_dividends:
            total += self.dividends_net
        return total


def derive_cycle_state(*, open_put_contracts: Decimal | str, shares: Decimal | str,
                       open_short_call_contracts: Decimal | str,
                       cycle_closed_reason: str | None = None,
                       unresolved: bool = False,
                       multiplier: Decimal | str = 100) -> CycleState:
    if unresolved:
        return CycleState.UNRESOLVED
    puts, shares_d, calls = D(open_put_contracts), D(shares), D(open_short_call_contracts)
    if min(puts, shares_d, calls) < ZERO:
        raise ValueError("position quantities must be absolute non-negative values")
    if cycle_closed_reason:
        mapping = {
            "PUT_ONLY": CycleState.CLOSED_PUT_ONLY,
            "CALLED_AWAY": CycleState.CLOSED_CALLED_AWAY,
            "STOCK_SOLD": CycleState.CLOSED_STOCK_SOLD,
        }
        if cycle_closed_reason not in mapping:
            raise ValueError("unknown cycle_closed_reason")
        if puts or shares_d or calls:
            raise ValueError("closed cycle cannot retain open positions")
        return mapping[cycle_closed_reason]
    if puts and shares_d:
        return CycleState.UNRESOLVED
    if puts:
        return CycleState.PUT_OPEN
    if shares_d:
        if not calls:
            return CycleState.ASSIGNED_NO_CALL
        _, deficit, coverage = covered_call_coverage(shares_d, calls, multiplier)
        if coverage == "COVERED" and deficit == ZERO:
            return CycleState.COVERED_CALL_OPEN
        return CycleState.PARTIALLY_COVERED
    return CycleState.UNRESOLVED
