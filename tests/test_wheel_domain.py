from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wheel_domain import (  # noqa: E402
    CycleCashFlows,
    CycleState,
    covered_call_coverage,
    derive_cycle_state,
    full_assignment_obligation,
)


class WheelDomainTests(unittest.TestCase):
    def test_assignment_obligation_uses_multiplier_and_all_contracts(self):
        self.assertEqual(full_assignment_obligation("16.5", 2), Decimal("3300.0"))

    def test_covered_call_coverage_detects_partial_cover(self):
        covered, deficit, status = covered_call_coverage(150, 2)
        self.assertEqual(covered, Decimal("150"))
        self.assertEqual(deficit, Decimal("50"))
        self.assertEqual(status, "PARTIAL")

    def test_assignment_break_evens_are_separated(self):
        # 100 shares assigned at $50; closed put +$100, closed call +$120,
        # current open call credit +$80, conservative buyback cost $130, dividend $25.
        flows = CycleCashFlows(
            remaining_shares=Decimal("100"),
            stock_net_cashflow=Decimal("-5000"),
            closed_put_net_pnl=Decimal("100"),
            closed_call_net_pnl=Decimal("120"),
            open_option_cashflow=Decimal("80"),
            dividends_net=Decimal("25"),
            open_option_close_cost=Decimal("130"),
        )
        self.assertEqual(flows.locked_break_even(), Decimal("47.8"))
        self.assertEqual(flows.cashflow_break_even(), Decimal("47"))
        self.assertEqual(flows.liquidation_break_even(), Decimal("48.3"))
        self.assertEqual(flows.locked_break_even(include_dividends=True), Decimal("47.55"))

    def test_roll_debit_is_preserved_in_locked_break_even(self):
        # Old put +100 then -150 to close; new assigned put +200: net +150.
        flows = CycleCashFlows(
            remaining_shares=Decimal("100"),
            stock_net_cashflow=Decimal("-1000"),
            closed_put_net_pnl=Decimal("150"),
        )
        self.assertEqual(flows.locked_break_even(), Decimal("8.5"))

    def test_open_put_credit_is_not_locked_profit(self):
        flows = CycleCashFlows(
            remaining_shares=Decimal("100"),
            stock_net_cashflow=Decimal("-2000"),
            open_option_cashflow=Decimal("75"),
            open_option_close_cost=Decimal("110"),
        )
        self.assertEqual(flows.locked_break_even(), Decimal("20"))
        self.assertEqual(flows.cashflow_break_even(), Decimal("19.25"))
        self.assertEqual(flows.liquidation_break_even(), Decimal("20.35"))

    def test_partial_stock_sale_changes_recovery_threshold_not_broker_basis(self):
        # Assignment -$5,000, then 50 shares sold for $2,700. The remaining
        # 50 shares need $44 each to recover the cycle after $100 put P&L.
        flows = CycleCashFlows(
            remaining_shares=Decimal("50"),
            stock_net_cashflow=Decimal("-2300"),
            closed_put_net_pnl=Decimal("100"),
        )
        self.assertEqual(flows.locked_break_even(), Decimal("44"))
        self.assertEqual(flows.broker_reconciliation_basis("50"), Decimal("50"))

    def test_call_assignment_projection_uses_open_credit_once(self):
        flows = CycleCashFlows(
            remaining_shares=Decimal("100"),
            stock_net_cashflow=Decimal("-5000"),
            closed_put_net_pnl=Decimal("100"),
            open_option_cashflow=Decimal("80"),
        )
        self.assertEqual(flows.projected_call_assignment_pnl("52", 100), Decimal("380"))

    def test_cycle_state_is_derived_from_authoritative_positions(self):
        self.assertEqual(
            derive_cycle_state(open_put_contracts=1, shares=0, open_short_call_contracts=0),
            CycleState.PUT_OPEN,
        )
        self.assertEqual(
            derive_cycle_state(open_put_contracts=0, shares=100, open_short_call_contracts=1),
            CycleState.COVERED_CALL_OPEN,
        )
        self.assertEqual(
            derive_cycle_state(open_put_contracts=0, shares=150, open_short_call_contracts=2),
            CycleState.PARTIALLY_COVERED,
        )

    def test_worthless_put_and_call_assignment_are_closed_cycle_states(self):
        self.assertEqual(
            derive_cycle_state(
                open_put_contracts=0,
                shares=0,
                open_short_call_contracts=0,
                cycle_closed_reason="PUT_ONLY",
            ),
            CycleState.CLOSED_PUT_ONLY,
        )
        self.assertEqual(
            derive_cycle_state(
                open_put_contracts=0,
                shares=0,
                open_short_call_contracts=0,
                cycle_closed_reason="CALLED_AWAY",
            ),
            CycleState.CLOSED_CALLED_AWAY,
        )

    def test_simultaneous_open_put_and_shares_requires_reconciliation(self):
        self.assertEqual(
            derive_cycle_state(open_put_contracts=1, shares=100, open_short_call_contracts=0),
            CycleState.UNRESOLVED,
        )

    def test_closed_cycle_cannot_retain_positions(self):
        with self.assertRaises(ValueError):
            derive_cycle_state(
                open_put_contracts=0,
                shares=100,
                open_short_call_contracts=0,
                cycle_closed_reason="STOCK_SOLD",
            )

    def test_break_even_requires_remaining_shares(self):
        with self.assertRaises(ValueError):
            CycleCashFlows(remaining_shares=Decimal("0"), stock_net_cashflow=Decimal("0"))


if __name__ == "__main__":
    unittest.main()
