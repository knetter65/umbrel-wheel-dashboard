"""Sanitized deterministic fixtures for the Phase-1 dashboard."""
from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def load_snapshot() -> dict[str, Any]:
    return json.loads((ROOT / "fixtures" / "dashboard_snapshot.json").read_text())


def synthetic_bars(symbol: str, count: int = 260) -> list[dict[str, Any]]:
    """Generate stable weekday OHLCV bars; these are explicitly not market data."""
    parameters = {
        "ALFA": (38.0, 0.035, 0.0),
        "BETA": (30.0, 0.020, 1.2),
        "GAMMA": (22.0, 0.010, 2.4),
    }
    if symbol not in parameters:
        raise KeyError(f"unknown fixture symbol: {symbol}")
    start, trend, phase = parameters[symbol]
    days: list[date] = []
    cursor = date(2025, 9, 29)
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)

    bars = []
    previous_close = start
    for idx, session in enumerate(days):
        center = start + trend * idx + math.sin(idx / 8 + phase) * 1.8 + math.sin(idx / 21) * 0.9
        open_price = previous_close + math.sin(idx * 1.31 + phase) * 0.35
        close = center + math.cos(idx * 0.83 + phase) * 0.45
        high = max(open_price, close) + 0.55 + abs(math.sin(idx / 5)) * 0.35
        low = min(open_price, close) - 0.55 - abs(math.cos(idx / 6)) * 0.35
        bars.append(
            {
                "date": session.isoformat(),
                "open": round(open_price, 3),
                "high": round(high, 3),
                "low": round(low, 3),
                "close": round(close, 3),
                "volume": 700_000 + (idx % 23) * 31_000 + int(abs(math.sin(idx / 7)) * 500_000),
            }
        )
        previous_close = close
    return bars
