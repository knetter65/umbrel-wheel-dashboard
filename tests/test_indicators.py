from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.fixtures import synthetic_bars  # noqa: E402
from src.indicators import bollinger_bands, rsi, sma  # noqa: E402


class IndicatorTests(unittest.TestCase):
    def test_sma_has_expected_window_alignment(self):
        self.assertEqual(sma([1, 2, 3, 4], 3), [None, None, 2.0, 3.0])

    def test_bollinger_flat_window_has_zero_band_width(self):
        lower, middle, upper = bollinger_bands([5] * 20)
        self.assertEqual(lower[-1], 5.0)
        self.assertEqual(middle[-1], 5.0)
        self.assertEqual(upper[-1], 5.0)

    def test_wilder_rsi_rising_and_flat_edges(self):
        self.assertEqual(rsi(range(20), 14)[-1], 100.0)
        self.assertEqual(rsi([10] * 20, 14)[-1], 50.0)

    def test_synthetic_bars_are_deterministic_and_valid(self):
        first = synthetic_bars("ALFA")
        second = synthetic_bars("ALFA")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 260)
        for bar in first:
            self.assertGreaterEqual(bar["high"], max(bar["open"], bar["close"], bar["low"]))
            self.assertLessEqual(bar["low"], min(bar["open"], bar["close"], bar["high"]))


if __name__ == "__main__":
    unittest.main()