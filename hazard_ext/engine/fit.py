"""Weighted non-linear least-squares fit of the three tail shapes (Levenberg–Marquardt).

The client's curve fit minimises the weighted squared error on the recovery scale, with each
month weighted by its month number so that later months count more, and solves it with the
Levenberg–Marquardt algorithm. This module does the same for the challenger's tail shapes:

    minimise  Σ_b  w_b · (c · s(b; θ) − R_b)²,   w_b = b,

over the points of the regression window (the reference row from FitStart to its last
credible bucket, RecoveryPct > 0). ``c`` is the level of the curve and is discarded: the engine
re-levels every TermStep row on its own anchor window. Only the shape parameters θ are kept:
λ (exponential), γ (power law), or μ and σ (log-normal). A parameter given as an override is
held fixed and the others are solved around it.

Pure numpy, deterministic, no starting-value input: the fit starts from the log-linear
least-squares estimate (the hand-built workbooks' SLOPE fit) when that exists and from a
generic point otherwise.
"""
from __future__ import annotations

import math

import numpy as np

MAX_ITER = 500
TOL = 1e-13


def _model(shape: str, b: np.ndarray, theta: np.ndarray):
    """Shape value s(b; θ) and its derivatives with respect to each element of θ (c excluded)."""
    if shape == "exp":
        lam = theta[0]
        s = np.exp(-lam * b)
        return s, [-b * s]
    if shape == "power":
        gam = theta[0]
        s = b ** (-gam)
        return s, [-np.log(b) * s]
    mu, sigma = theta
    lb = np.log(b)
    z = (lb - mu) / sigma
    s = np.exp(-0.5 * z * z) / b
    return s, [s * (lb - mu) / (sigma * sigma), s * (lb - mu) ** 2 / sigma ** 3]


# Generous bounds that stop a fit wandering into a degenerate region (a log-normal with σ in the
# thousands is the shape 1/b, which the data cannot distinguish from any other huge σ).
BOUNDS = {"exp": [(-10.0, 10.0)], "power": [(-50.0, 50.0)], "logn": [(-20.0, 30.0), (0.02, 10.0)]}


def _valid(shape: str, theta: np.ndarray) -> bool:
    if not np.all(np.isfinite(theta)):
        return False
    return all(lo <= t <= hi for t, (lo, hi) in zip(theta, BOUNDS[shape]))


def generic_starts(shape: str, b: np.ndarray) -> list[list[float]]:
    """A small deterministic grid of starting points covering the plausible shapes."""
    if shape == "exp":
        return [[0.005], [0.02], [0.05], [0.1], [0.2]]
    if shape == "power":
        return [[0.5], [1.0], [2.0], [3.0], [5.0]]
    lb = np.log(np.asarray(b, dtype=float))
    mus = [float(np.quantile(lb, q)) for q in (0.1, 0.5, 0.9)] + [float(lb.max()) + 1.0]
    return [[m, s] for m in mus for s in (0.3, 0.6, 1.0, 1.6)]


def lm_fit_multi(shape: str, b: np.ndarray, r: np.ndarray, starts, fixed) -> dict:
    """Run lm_fit from several starting points and keep the best converged solution (lowest
    weighted squared error). Deterministic: the same starts give the same answer."""
    fixed = np.asarray(fixed, dtype=bool)
    best = None
    for st in starts:
        st = np.array(st, dtype=float)
        if not _valid(shape, st):
            continue
        out = lm_fit(shape, b, r, st, fixed)
        if out["problem"]:
            if best is None:
                best = out
            continue
        if best is None or best["problem"] or out["wsse"] < best["wsse"] * (1 - 1e-12):
            best = out
    if best is None:
        best = {"theta": np.full(len(fixed), np.nan), "c": float("nan"), "wsse": float("nan"),
                "iterations": 0, "converged": False, "problem": "bad_start"}
    return best


def lm_fit(shape: str, b: np.ndarray, r: np.ndarray, start: np.ndarray, fixed: np.ndarray) -> dict:
    """Fit c · s(b; θ) to r by weighted Levenberg–Marquardt, weights w = b.

    start: initial θ (λ | γ | [μ, σ]); fixed: boolean mask of θ entries held at their start value.
    Returns {"theta", "c", "wsse", "iterations", "converged", "problem"}. On failure theta is nan.
    """
    b = np.asarray(b, dtype=float)
    r = np.asarray(r, dtype=float)
    theta = np.array(start, dtype=float)
    fixed = np.asarray(fixed, dtype=bool)
    free = ~fixed
    out = {"theta": np.full(theta.shape, np.nan), "c": float("nan"), "wsse": float("nan"),
           "iterations": 0, "converged": False, "problem": ""}
    k = int(free.sum()) + 1                     # c plus the free shape parameters
    if b.size < k:
        out["problem"] = "too_few_points"
        return out
    if not _valid(shape, theta):
        out["problem"] = "bad_start"
        return out
    w = b / b.mean()                            # weight ∝ bucket number, scaled to mean 1

    def level(s):
        den = float((w * s * s).sum())
        return float((w * s * r).sum() / den) if den > 0 else float("nan")

    def cost(th):
        s, _ = _model(shape, b, th)
        if not np.all(np.isfinite(s)):
            return float("inf"), float("nan"), s
        c = level(s)
        if not math.isfinite(c):
            return float("inf"), c, s
        e = c * s - r
        return float((w * e * e).sum()), c, s

    f, c, s = cost(theta)
    if not math.isfinite(f):
        out["problem"] = "bad_start"
        return out
    damping = 1e-3
    it = 0
    for it in range(1, MAX_ITER + 1):
        s, ds = _model(shape, b, theta)
        cols = [s] + [c * d for d, fr in zip(ds, free) if fr]
        J = np.column_stack(cols)                      # ∂(c·s)/∂[c, θ_free]
        e = c * s - r
        JtW = J.T * w
        H = JtW @ J
        g = JtW @ e
        step_taken = False
        for _ in range(60):
            A = H + damping * np.diag(np.diag(H))
            try:
                delta = np.linalg.solve(A, -g)
            except np.linalg.LinAlgError:
                damping *= 10.0
                continue
            trial = theta.copy()
            trial[free] = theta[free] + delta[1:]
            if not _valid(shape, trial):
                damping *= 10.0
                continue
            f_trial, c_trial, _ = cost(trial)        # c re-solved exactly at the trial point
            if f_trial < f:
                rel = (f - f_trial) / max(f, 1e-300)
                theta, f, c = trial, f_trial, c_trial
                damping = max(damping / 3.0, 1e-15)
                step_taken = True
                if rel < TOL or float(np.abs(delta[1:]).max()) < 1e-12 * (1.0 + float(np.abs(theta[free]).max(initial=0.0))):
                    out["converged"] = True
                break
            damping *= 4.0
        if not step_taken:
            out["converged"] = True                 # no improving step exists: at a minimum
            break
        if out["converged"]:
            break
    out.update({"theta": theta, "c": c, "wsse": f, "iterations": it})
    if not out["converged"]:
        out["problem"] = "no_convergence"
        out["theta"] = np.full(theta.shape, np.nan)
    return out


def weighted_sse(shape: str, b: np.ndarray, r: np.ndarray, theta) -> float:
    """The weighted squared error of the best-levelled shape, for comparing fits."""
    b = np.asarray(b, dtype=float)
    r = np.asarray(r, dtype=float)
    w = b / b.mean()
    s, _ = _model(shape, b, np.asarray(theta, dtype=float))
    den = float((w * s * s).sum())
    c = float((w * s * r).sum() / den)
    e = c * s - r
    return float((w * e * e).sum())
