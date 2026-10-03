"""The client's applied recovery curves, for comparison only.

An applied curve gives the client's monthly recovery rate by month since default, t = 1, 2, ...
It is uploaded on one of two bases:

* ``face``: each month's cash as a share of the balance at default (the app's own basis, so the
  values along the curve add up to the cumulative recovery);
* ``outstanding``: each month's cash as a share of the balance still outstanding at the start
  of that month (a conditional rate).

Everything here works on the face basis; ``to_face`` converts an outstanding-basis curve first.
Nothing in this module feeds the extension itself.
"""
from __future__ import annotations

import numpy as np

BASES = ("face", "outstanding")
_EPS = 1e-9


def to_face(curve: np.ndarray, basis: str) -> np.ndarray:
    """Monthly cash as a share of the balance at default, whatever basis the curve came on."""
    c = np.asarray(curve, dtype=float)
    if basis == "face":
        return c
    if basis != "outstanding":
        raise ValueError(f"Unknown curve basis {basis!r}; expected face or outstanding")
    h = np.clip(c, 0.0, 1.0)
    survival = np.concatenate([[1.0], np.cumprod(1.0 - h)[:-1]])     # share outstanding at the start of month t
    return h * survival


def to_outstanding(c_face: np.ndarray) -> np.ndarray:
    """Monthly cash as a share of the balance still outstanding at the start of each month, from a
    face-basis curve: h(t) = c(t) / (1 - sum of c before t). nan where nothing is outstanding or c is nan."""
    c = np.asarray(c_face, dtype=float)
    filled = np.where(np.isfinite(c), c, 0.0)
    before = np.concatenate([[0.0], np.cumsum(filled)[:-1]])
    remaining = 1.0 - before
    with np.errstate(divide="ignore", invalid="ignore"):
        h = np.where(remaining > _EPS, c / remaining, np.nan)
    return h


def rate_at(c_face: np.ndarray, ts: int, max_bucket: int) -> tuple[np.ndarray, np.ndarray]:
    """The curve rolled forward to TermStep ts: cash in bucket b (b >= ts) as a share of the balance
    at ts, for b = ts..max_bucket. Returns (buckets, rates); rates are nan where the curve has no
    balance left or no value.

    This is the same roll-forward the engine uses for TermSteps beyond LastTS.
    """
    c = np.zeros(max(max_bucket, len(c_face)) + 1)
    c[1:len(c_face) + 1] = c_face
    b = np.arange(ts, max_bucket + 1)
    factor = 1.0 - c[1:ts].sum()
    rates = c[ts:max_bucket + 1] / factor if factor > _EPS else np.full(b.shape, np.nan)
    rates = np.where(b <= len(c_face), rates, np.nan)                 # beyond the uploaded months: unknown
    return b, rates


def implied_lgd(c_face: np.ndarray, v: float, max_bucket: int, target_ts: int) -> np.ndarray:
    """LGD implied by the client's curve at TermStep 1..target_ts, discounting each bucket for
    (b - ts + 1) months at the monthly factor v and counting buckets to max_bucket. nan where the
    curve's balance is exhausted."""
    width = max(max_bucket, target_ts)
    c = np.zeros(width + 2)
    c[1:len(c_face) + 1] = c_face[: width + 1]
    b = np.arange(width + 2, dtype=float)
    vb = v ** b
    in_mb = (b >= 1) & (b <= max_bucket)
    d_cum = np.cumsum(c * vb * in_mb)
    c_cum = np.cumsum(c)
    T = np.arange(1, target_ts + 1)
    pv = (d_cum[width + 1] - d_cum[T - 1]) / (v ** (T - 1.0))
    factor = 1.0 - (c_cum[T - 1] - c_cum[0])
    with np.errstate(divide="ignore", invalid="ignore"):
        lgd = np.where(factor > _EPS, 1.0 - pv / factor, np.nan)
    return lgd
