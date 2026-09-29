"""Deterministic technical indicators used by the fixture dashboard."""
from __future__ import annotations

from statistics import fmean, pstdev
from typing import Iterable


def _values(values: Iterable[float]) -> list[float]:
    return [float(v) for v in values]


def sma(values: Iterable[float], period: int) -> list[float | None]:
    series = _values(values)
    if period <= 0:
        raise ValueError("period must be positive")
    result: list[float | None] = [None] * len(series)
    for idx in range(period - 1, len(series)):
        result[idx] = fmean(series[idx - period + 1 : idx + 1])
    return result


def bollinger_bands(
    values: Iterable[float], period: int = 20, standard_deviations: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    series = _values(values)
    if period <= 0 or standard_deviations <= 0:
        raise ValueError("period and standard_deviations must be positive")
    middle = sma(series, period)
    lower: list[float | None] = [None] * len(series)
    upper: list[float | None] = [None] * len(series)
    for idx in range(period - 1, len(series)):
        window = series[idx - period + 1 : idx + 1]
        sigma = pstdev(window)
        lower[idx] = middle[idx] - standard_deviations * sigma  # type: ignore[operator]
        upper[idx] = middle[idx] + standard_deviations * sigma  # type: ignore[operator]
    return lower, middle, upper


def rsi(values: Iterable[float], period: int = 14) -> list[float | None]:
    """Wilder RSI; flat windows return neutral 50 rather than an undefined value."""
    series = _values(values)
    if period <= 0:
        raise ValueError("period must be positive")
    result: list[float | None] = [None] * len(series)
    if len(series) <= period:
        return result

    changes = [series[i] - series[i - 1] for i in range(1, len(series))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain = fmean(gains[:period])
    avg_loss = fmean(losses[:period])

    def score(gain: float, loss: float) -> float:
        if gain == 0 and loss == 0:
            return 50.0
        if loss == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    result[period] = score(avg_gain, avg_loss)
    for change_index in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[change_index]) / period
        avg_loss = (avg_loss * (period - 1) + losses[change_index]) / period
        result[change_index + 1] = score(avg_gain, avg_loss)
    return result
