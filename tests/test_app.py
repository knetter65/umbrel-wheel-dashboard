from __future__ import annotations

import json
import unittest
from decimal import Decimal

from plotly.utils import PlotlyJSONEncoder

from src.app import (
    IS_ACTIVE,
    IS_FIXTURE,
    SNAPSHOT,
    assigned_put_strike,
    build_recovery_panel,
    build_technical_figure,
    create_app,
)
from src.wheel_domain import CycleCashFlows


class DashboardSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.client = cls.app.server.test_client()

    def test_home_and_dash_layout_are_served(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Wheel Dashboard", response.data)
        health = self.client.get("/healthz")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(
            health.get_json(),
            {"status": "ok", "namespace": "wheel-dashboard", "mode": "read-only"},
        )
        layout = self.client.get("/_dash-layout")
        self.assertEqual(layout.status_code, 200)
        payload = layout.get_json()
        self.assertIsInstance(payload, dict)
        for path in ("/assets/styles.css", "/assets/manifest.webmanifest"):
            asset_response = self.client.get(path)
            self.assertEqual(asset_response.status_code, 200)
            asset_response.close()

    def test_visible_layout_has_required_sections_and_no_trade_controls(self):
        layout_text = json.dumps(
            self.client.get("/_dash-layout").get_json(), ensure_ascii=False
        ).lower()
        for required in (
            "portefeuille",
            "open posities",
            "wheel-cycli",
            "p&l & cashflow",
            "gerealiseerde trading-p&l",
            "open optiecashflow",
            "locked break-even",
            "techniek",
            "datakwaliteit",
            "bronnen en freshness",
            "reconciliatiepunten",
            "scheduler readiness",
            "automatische refresh",
            "laatste schedulerproef",
        ):
            self.assertIn(required, layout_text)
        for forbidden in ("place order", "submit order", "transmit order", "buy to open"):
            self.assertNotIn(forbidden, layout_text)

    def test_assigned_metrics_are_recomputed_not_trusted_blindly(self):
        cycle = next(c for c in SNAPSHOT["cycles"] if Decimal(c["remaining_shares"]) > 0)
        flows = CycleCashFlows(
            remaining_shares=Decimal(cycle["remaining_shares"]),
            stock_net_cashflow=Decimal(cycle["stock_net_cashflow"]),
            closed_put_net_pnl=Decimal(cycle["realized_put_pnl"]),
            closed_call_net_pnl=Decimal(cycle["realized_call_pnl"]),
            open_option_cashflow=Decimal(cycle["open_option_cashflow"]),
            dividends_net=Decimal(cycle["dividends_net"]),
            open_option_close_cost=Decimal(cycle["open_option_close_cost"]),
        )
        self.assertEqual(Decimal(cycle["locked_break_even"]), flows.locked_break_even())
        self.assertEqual(Decimal(cycle["cashflow_break_even"]), flows.cashflow_break_even())
        self.assertEqual(Decimal(cycle["liquidation_break_even"]), flows.liquidation_break_even())

    def test_chart_contains_candles_bollinger_rsi_and_hoverable_recovery_lines(self):
        cycle = next(c for c in SNAPSHOT["cycles"] if Decimal(c["remaining_shares"]) > 0)
        symbol = cycle["underlying"]
        figure = build_technical_figure(symbol)
        names = {trace.name for trace in figure.data}
        self.assertTrue({symbol, "BB upper", "BB lower", "SMA20", "SMA50", "SMA200", "Volume", "RSI14"}.issubset(names))
        self.assertGreaterEqual(len(figure.data[0].x), 200)
        references = {trace.meta["reference_level"] for trace in figure.data if trace.meta}
        self.assertIn("Oorspronkelijke putstrike", references)
        self.assertIn("Liquidation break-even", references)
        self.assertFalse(any(annotation.text == "Originele strike" for annotation in figure.layout.annotations))

    def test_recovery_panel_separates_assignment_and_break_even_meanings(self):
        cycle = next(c for c in SNAPSHOT["cycles"] if Decimal(c["remaining_shares"]) > 0)
        panel_text = json.dumps(
            build_recovery_panel(cycle["underlying"]).to_plotly_json(),
            ensure_ascii=False,
            cls=PlotlyJSONEncoder,
        )
        for label in (
            "Oorspronkelijke putstrike",
            "Assignmentprijs",
            "Brokerbasis",
            "Locked break-even",
            "Cashflow break-even",
            "Liquidation break-even",
            "Open callstrike",
            "Resultaat bij call assignment",
        ):
            self.assertIn(label, panel_text)
        assigned = [leg for leg in cycle["option_legs"] if leg["side"] == "PUT" and leg["state"] == "ASSIGNED"]
        self.assertEqual(assigned_put_strike(cycle), assigned[-1]["strike"] if assigned else None)

    def test_source_badge_matches_selected_database(self):
        layout_text = json.dumps(
            self.client.get("/_dash-layout").get_json(), ensure_ascii=False
        )
        expected = (
            "FASE 1 · FIXTURE"
            if IS_FIXTURE
            else ("ACTIEF · READ-ONLY" if IS_ACTIVE else "STAGING · READ-ONLY")
        )
        self.assertIn(expected, layout_text)
        self.assertIn("leeftijd", layout_text)


if __name__ == "__main__":
    unittest.main()