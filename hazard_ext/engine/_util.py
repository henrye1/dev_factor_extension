"""Small numeric and text helpers shared by the engine modules."""
from __future__ import annotations

import math

import numpy as np

EPS = 1e-9          # the workbook's MAX(0.000000001, balance factor) and the "nothing outstanding" test


def clean_dict(d: dict) -> dict:
    """A copy of ``d`` that JSON can hold: numpy scalars become Python, non-finite floats become None."""
    out = {}
    for k, v in d.items():
        if isinstance(v, (np.floating, float)):
            v = float(v)
            out[k] = v if math.isfinite(v) else None
        elif isinstance(v, np.integer):
            out[k] = int(v)
        elif isinstance(v, np.bool_):
            out[k] = bool(v)
        else:
            out[k] = v
    return out


def clean_list(a) -> list:
    """An array as a JSON list: text and integers as they are, non-finite floats as None."""
    arr = np.asarray(a)
    if arr.dtype.kind in "US":
        return arr.tolist()
    if arr.dtype.kind in "iu":
        return arr.astype(int).tolist()
    arr = arr.astype(float)
    return [x if math.isfinite(x) else None for x in arr.tolist()]


def finite_or_none(x) -> float | None:
    """A float, or None when the value is missing or not finite."""
    if x is None:
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def slope(y: np.ndarray, x: np.ndarray) -> float:
    """Least-squares slope of y on x (Excel SLOPE). nan when undefined."""
    if x.size < 2:
        return float("nan")
    dx = x - x.mean()
    den = float((dx * dx).sum())
    if den == 0.0:
        return float("nan")
    return float((dx * (y - y.mean())).sum() / den)


def weighted_mean(w: np.ndarray, x: np.ndarray, wsum: float | None = None) -> float:
    """Σ w·x ÷ Σ w with nan in x counted as 0 (the workbooks' SUMPRODUCT ÷ SUM). nan when Σ w ≤ 0."""
    wsum = float(w.sum()) if wsum is None else wsum
    if wsum <= 0:
        return float("nan")
    return float((w * np.nan_to_num(x, nan=0.0)).sum() / wsum)


def max_abs(x: np.ndarray) -> float:
    """Largest |x| over the finite values; 0 when there are none."""
    x = x[np.isfinite(x)]
    return float(np.abs(x).max()) if x.size else 0.0


def ranges_text(values) -> str:
    """Compact text for a sorted list of integers: 2–5, 9, 12–14."""
    vals = [int(x) for x in values]
    out, start, prev = [], vals[0], vals[0]
    for x in vals[1:] + [None]:
        if x is not None and x == prev + 1:
            prev = x
            continue
        out.append(str(start) if start == prev else f"{start}–{prev}")
        if x is not None:
            start = prev = x
    return ", ".join(out)


def money_text(x: float) -> str:
    """Rand amount for a warning: R1.23bn, R45.6m or R12,345."""
    if x >= 1e9:
        return f"R{x / 1e9:.2f}bn"
    if x >= 1e6:
        return f"R{x / 1e6:.1f}m"
    return f"R{x:,.0f}"
