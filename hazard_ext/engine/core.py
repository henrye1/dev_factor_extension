"""Hazard-rate bucket extension engine.

A numpy re-statement of the formula-driven framework workbook. The calculation runs in the
same order as the workbook's sheets, one function per stage:

    observed triangles   (Hazard_Obs, or the vintage rebuild)        -> _observed
    credible window      (Tail_Fit, per TermStep)                    -> _credible_window
    decay parameters     (Config λ / γ / μ / σ, OLS or LM)           -> _fit_decay
    shapes and scales    (Tail_Fit rows 5-9 and scale columns)       -> _shape_values, _scales
    extended triangles   (Ext_Exp / Ext_Power / Ext_LogN)            -> _extend
    PV and LGD per row   (Ext_* right-hand columns, Results)         -> _row_lgds, _results
    LGD to the Target    (LGD_300 / LGD_Target)                      -> _lgd_to_target

All triangle arrays are 1-indexed: element [ts, b]; index 0 is unused padding.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ._util import EPS as _EPS
from ._util import clean_dict, clean_list, finite_or_none, money_text, ranges_text, slope, weighted_mean
from .fit import MAX_ITER, generic_starts, lm_fit_multi
from .params import Params
from .parse import RecoveryData, Triangles, VintageError

SHAPES = ("exp", "power", "logn")
SHAPE_LABELS = {"exp": "Exponential", "power": "Power law", "logn": "Log-normal"}
SHAPE_FORMULAS = {
    "exp": "s(b) = e^(−λb)",
    "power": "s(b) = b^(−γ)",
    "logn": "s(b) = (1/b) · exp(−(ln b − μ)² / (2σ²))",
}
SOLVER_LABELS = {"lm": "Levenberg–Marquardt, weighted by bucket", "ols": "Log-linear least squares"}

_LM_GENERIC_START = {"exp": [0.05], "power": [2.0], "logn": [3.0, 1.0]}
_LM_PROBLEMS = {"too_few_points": "fewer than {k1} positive points in the regression window",
                "no_convergence": f"the fit did not converge in {MAX_ITER} iterations",
                "bad_start": "the fit could not be started"}


class EngineError(ValueError):
    """The parameters cannot be applied to this dataset."""


# ============================================================================ result object
@dataclass
class ExtensionResult:
    params: dict
    config: dict                 # effective and derived Config values
    n: int                       # highest observed TermStep / bucket
    width: int                   # number of bucket columns in the extended triangles
    R: np.ndarray                # observed RecoveryPct      (n+1, n+1)
    E: np.ndarray                # observed ExposureBucket   (n+1, n+1)
    shapes: np.ndarray           # (3, width+1) shape values by bucket
    vb: np.ndarray               # (width+1,) v^b
    ext: np.ndarray              # (3, n+1, width+1) extended RecoveryPct
    tail_fit: dict               # column -> array over ts 1..n
    results: dict                # column -> array over ts 1..n
    averages: dict
    lgd_ts: dict                 # column -> array over ts 1..target
    lgd_ts_summary: dict
    base_curves: np.ndarray      # (3, width+1) c(b) of the base row
    shape_stats: dict = field(default_factory=dict)   # per-shape arrays over ts 0..n (lgd, lgd_h, und)
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "params": self.params,
            "config": clean_dict(self.config),
            "n": self.n,
            "averages": clean_dict(self.averages),
            "results": {k: clean_list(v) for k, v in self.results.items()},
            "tail_fit": {k: clean_list(v) for k, v in self.tail_fit.items()},
            "lgd_ts": {k: clean_list(v) for k, v in self.lgd_ts.items()},
            "lgd_ts_summary": clean_dict(self.lgd_ts_summary),
            "warnings": list(self.warnings),
        }

    def curve(self, ts: int) -> dict:
        """Observed and extended RecoveryPct along one TermStep row, for charting."""
        if not 1 <= ts <= self.n:
            raise EngineError(f"TermStep {ts} is not observed (1..{self.n})")
        mb = min(int(self.config["max_bucket"]), self.width)
        b = np.arange(ts, mb + 1)
        obs_end = min(self.n, mb)
        observed = np.full(b.shape, np.nan)
        exposure = np.full(b.shape, np.nan)
        if obs_end >= ts:
            k = obs_end - ts + 1
            exposure[:k] = self.E[ts, ts:obs_end + 1]
            row = self.R[ts, ts:obs_end + 1].astype(float)
            row[exposure[:k] <= 0] = np.nan             # a bucket with no exposure was not observed
            observed[:k] = row
        return {
            "ts": ts,
            "bucket": b.tolist(),
            "observed": clean_list(observed),
            "exposure": clean_list(exposure),
            "exp": clean_list(self.ext[0, ts, ts:mb + 1]),
            "power": clean_list(self.ext[1, ts, ts:mb + 1]),
            "logn": clean_list(self.ext[2, ts, ts:mb + 1]),
            "last_cred": int(self.tail_fit["last_cred"][ts - 1]),
            "last_obs": int(self.tail_fit["last_obs"][ts - 1]),
        }


# ============================================================================ stage records
@dataclass
class _Observed:
    tri: Triangles
    full: Triangles              # the unfiltered triangles (== tri without a vintage filter)
    vinfo: dict
    filtered: bool


@dataclass
class _Window:
    last_obs: np.ndarray         # per ts 0..n
    last_cred: np.ndarray
    win_start: np.ndarray
    win_end: np.ndarray
    n_win: np.ndarray
    in_win: np.ndarray           # (n+1, n+1) bucket mask of each row's anchor window
    sum_obs: np.ndarray


@dataclass
class _Decay:
    lam_fit: float
    gam_fit: float
    lam: float
    gam: float
    mu_fit: float
    sigma_fit: float
    mu: float
    sigma: float
    logn_points: int
    fit_points: int
    ref_last_cred: int
    fit_info: dict


# ============================================================================ entry point
def compute(data: RecoveryData, params: Params) -> ExtensionResult:
    """Run the extension framework for one dataset under one parameter set."""
    # A shape that cannot be computed (undefined decay) carries nan through its own columns by
    # design; the selected shape is checked explicitly in _row_lgds.
    with np.errstate(invalid="ignore", over="ignore", divide="ignore"):
        return _compute(data, params)


def _compute(data: RecoveryData, p: Params) -> ExtensionResult:
    warnings: list[str] = []
    obs = _observed(data, p)
    tri = obs.tri
    n, R, E = tri.n, tri.R, tri.E
    if p.ref_ts > n:
        raise EngineError(f"Reference TermStep {p.ref_ts} is beyond the observed range 1..{n}")
    if p.base_ts > n:
        raise EngineError(f"Base TermStep {p.base_ts} is beyond the observed range 1..{n}")

    rate = tri.implied_rate if p.rate is None else float(p.rate)
    v = (1.0 + rate) ** (-1.0 / 12.0)
    mb = int(p.max_bucket)
    width = max(mb, n)                       # bucket columns of the extended triangles
    target = int(p.target_ts)
    size = max(width, target) + 2            # padded so every index used below exists
    opening = float(E[1, 1])
    min_exp = p.min_exposure if p.min_exposure_mode == "abs" else p.min_exposure / 100.0 * opening

    win = _credible_window(R, E, n, min_exp, p.window)
    decay = _fit_decay(R, win.last_cred, n, p, warnings)
    shapes, vb = _shape_values(decay, v, size)
    scale = _scales(shapes, win, n, warnings)
    ext = _extend(R, shapes, scale, win.last_cred, n, size, p.floor)

    b_all = np.arange(size)
    in_mb = (b_all <= mb) & (b_all >= 1)
    rows = _row_lgds(ext, R, vb, in_mb, v, n, p)
    results, averages = _results(tri, rows, win, mb, n, p, rate, obs.filtered, warnings)
    lgd_ts, lgd_ts_summary, base, last_ts, last_data_ts = _lgd_to_target(ext, rows, tri, vb, in_mb, v, n,
                                                                         width, size, p)

    _add_setting_warnings(warnings, obs, win, p, opening, min_exp, mb, target, last_ts, last_data_ts,
                          lgd_ts["balance_factor"], lgd_ts["ts"])
    config = _config(data, tri, obs, decay, p, rate, v, mb, target, min_exp, opening, last_ts, last_data_ts)
    tail_fit = {
        "ts": np.arange(1, n + 1),
        "last_obs": win.last_obs[1:],
        "last_cred": win.last_cred[1:],
        "win_start": win.win_start[1:],
        "win_end": win.win_end[1:],
        "n_win": win.n_win[1:],
        "sum_obs": win.sum_obs[1:],
        "scale_exp": scale[0, 1:],
        "scale_power": scale[1, 1:],
        "scale_logn": scale[2, 1:],
    }
    return ExtensionResult(
        params=p.model_dump(), config=config, n=n, width=size - 1, R=R, E=E,
        shapes=shapes, vb=vb, ext=ext, tail_fit=tail_fit, results=results,
        averages=averages, lgd_ts=lgd_ts, lgd_ts_summary=lgd_ts_summary,
        base_curves=base, warnings=warnings,
        shape_stats={"lgd": rows["lgd_ext"], "lgd_h": rows["lgd_h"], "und": rows["und_ext"],
                     "lgd_obs": rows["lgd_obs"], "und_obs": rows["und_obs"], "tri_width": width},
    )


# ============================================================================ stages
def _observed(data: RecoveryData, p: Params) -> _Observed:
    """The observed triangles for the EventType: the file's, or rebuilt for the chosen vintages."""
    try:
        v_start, vinfo = data.resolve_vintages(p.event_type, p.vintage_start, p.vintage_years, p.report_date)
        tri = data.triangles(p.event_type, v_start)
    except VintageError as exc:
        raise EngineError(str(exc)) from None
    filtered = bool(vinfo["vintage_filter"])
    full = data.triangles(p.event_type) if filtered else tri
    if filtered and not (tri.E > 0).any():
        raise EngineError(f"Vintages from {vinfo['vintage_start_effective']} leave no exposure in the triangle; "
                          f"choose an earlier start")
    return _Observed(tri=tri, full=full, vinfo=vinfo, filtered=filtered)


def _last_true(mask: np.ndarray) -> np.ndarray:
    """Per row of a (rows, cols) mask whose column index is the bucket: the largest
    column index that is True, 0 when none."""
    cols = np.arange(mask.shape[1])
    return np.where(mask, cols, 0).max(axis=1)


def _credible_window(R: np.ndarray, E: np.ndarray, n: int, min_exp: float, window: int) -> _Window:
    """Tail_Fit per TermStep: last observed and last credible bucket, and the anchor window
    (the last W credible buckets, never before ts)."""
    ts_idx = np.arange(n + 1)
    obs_mask = E > 0
    obs_mask[:, 0] = False
    last_obs = _last_true(obs_mask)
    cred_mask = E >= min_exp
    cred_mask[:, 0] = False
    cred = _last_true(cred_mask)
    last_cred = np.where(cred >= ts_idx, cred, last_obs)         # no credible bucket: fall back
    win_start = np.maximum(ts_idx, last_cred - window + 1)
    win_end = last_cred.copy()
    n_win = np.where(last_cred < ts_idx, 0, win_end - win_start + 1)

    b_obs = np.arange(n + 1)
    in_win = (b_obs[None, :] >= win_start[:, None]) & (b_obs[None, :] <= win_end[:, None])
    in_win[:, 0] = False
    in_win &= (n_win > 0)[:, None]
    sum_obs = (R * in_win).sum(axis=1)
    return _Window(last_obs, last_cred, win_start, win_end, n_win, in_win, sum_obs)


def _logn_fit(fx: np.ndarray, fy: np.ndarray, mu_override, sigma_override) -> dict:
    """Log-normal shape (1/b)·exp(−(ln b − μ)²/(2σ²)) fitted to the same points as λ and γ.

    Taking logs, y = ln R + ln b = a0 + a1·x + a2·x² with x = ln b, an ordinary least-squares
    fit with one more regressor than the λ / γ slope. σ² = −1/(2·a2) and μ = a1·σ², valid when
    a2 < 0 (the tail is concave in ln b). With one parameter given, the other comes from a
    single-regressor SLOPE on the transformed points, as in the formula workbook.
    Returns mu_fit, sigma_fit (the full fit), mu, sigma (the values used), a2, points, problem.
    """
    out = {"mu_fit": float("nan"), "sigma_fit": float("nan"), "a2": float("nan"), "points": 0, "problem": ""}
    x = np.log(fx) if fx.size else np.array([])
    y = fy + x
    if fx.size >= 3:
        A = np.column_stack([np.ones_like(x), x, x * x])
        a = np.linalg.lstsq(A, y, rcond=None)[0]
        out["a2"] = float(a[2])
        if a[2] < 0:
            s2 = -1.0 / (2.0 * a[2])
            out["mu_fit"], out["sigma_fit"], out["points"] = float(a[1] * s2), float(math.sqrt(s2)), int(fx.size)
        else:
            out["problem"] = "not_concave"
    if mu_override is not None and sigma_override is not None:
        mu, sigma = float(mu_override), float(sigma_override)
    elif sigma_override is not None:
        sigma = float(sigma_override)
        mu = slope(y + x * x / (2.0 * sigma * sigma), x) * sigma * sigma if fx.size else float("nan")
    elif mu_override is not None:
        mu = float(mu_override)
        a2 = slope(y, x * x - 2.0 * mu * x) if fx.size else float("nan")
        sigma = math.sqrt(-1.0 / (2.0 * a2)) if math.isfinite(a2) and a2 < 0 else float("nan")
        if math.isfinite(a2) and a2 >= 0:
            out["problem"] = "not_concave"
    else:
        mu, sigma = out["mu_fit"], out["sigma_fit"]
    out["mu"], out["sigma"] = mu, sigma
    return out


def _lm_shape(shape: str, fx: np.ndarray, fr: np.ndarray, ols_start: list, overrides: list, info: dict):
    """Fit one shape by weighted Levenberg–Marquardt. Returns (fitted, used, warning).

    ``fitted`` is the full fit with every parameter free (nan when it fails); ``used`` is the
    fit with any override held fixed, or the override itself when every parameter is given.
    Both are scalars for exp / power and (μ, σ) tuples for logn.
    """
    k = len(ols_start)
    start = [s if (s is not None and math.isfinite(s)) else g for s, g in zip(ols_start, _LM_GENERIC_START[shape])]
    if shape == "logn" and not (start[1] > 0):
        start[1] = _LM_GENERIC_START["logn"][1]
    nan = float("nan")

    def run(fixed_vals):
        fixed = [fv is not None for fv in fixed_vals]
        if all(fixed):
            return [float(v) for v in fixed_vals], {"converged": True, "iterations": 0, "problem": "", "wsse": nan}
        # the log-linear estimate first, then a fixed grid, so a poor start cannot strand the fit
        starts = [start] + (generic_starts(shape, fx) if fx.size else [])
        starts = [[fv if fv is not None else s for fv, s in zip(fixed_vals, st)] for st in starts]
        res = lm_fit_multi(shape, fx, fr, starts, fixed)
        return [float(v) for v in res["theta"]], res

    full, res_full = run([None] * k)
    if all(o is None for o in overrides):
        used, res_used = full, res_full              # no override: the "used" fit is the full fit
    else:
        used, res_used = run(list(overrides))
    info[shape] = {"iterations": int(res_used["iterations"]), "converged": bool(res_used["converged"]),
                   "wsse": float(res_used["wsse"]) if math.isfinite(res_used["wsse"]) else None,
                   "problem": res_used["problem"]}
    warning = ""
    problem = res_used["problem"] or res_full["problem"]
    if problem:
        why = _LM_PROBLEMS.get(problem, problem).format(k1=k + 1)
        warning = (f"The weighted Levenberg–Marquardt fit of the {SHAPE_LABELS[shape]} shape is undefined: {why}. "
                   f"Lower FitStart, set an override, or choose another method")
    if k == 1:
        return full[0], used[0], warning
    return tuple(full), tuple(used), warning


def _rising_warning(name: str, fitted: float, how: str, p: Params, ref_last_cred: int, points: int) -> str:
    tail = "exponential" if name == "λ" else "power-law"
    return (f"The fitted {name} is {fitted:.4f}: the {how} on the reference row rises instead of decaying "
            f"over FitStart {p.fit_start} to bucket {ref_last_cred} ({points} points), so the "
            f"{tail} tail is undefined. Lower FitStart, use more vintages, set an override, or choose another method")


def _drop_rising(lam_fit, gam_fit, lam, gam, how, p, ref_last_cred, points, warnings):
    """A fitted decay that is not a decay (the fit slopes upward, usually from a handful of points
    on a short triangle) would make recoveries grow without limit. It is undefined, not a
    forecast; an override is the user's own choice and is left alone."""
    for name, fitted, over in (("λ", lam_fit, p.lambda_override), ("γ", gam_fit, p.gamma_override)):
        if over is None and math.isfinite(fitted) and fitted <= 0:
            warnings.append(_rising_warning(name, fitted, how, p, ref_last_cred, points))
    if p.lambda_override is None and math.isfinite(lam) and lam <= 0:
        lam = float("nan")
    if p.gamma_override is None and math.isfinite(gam) and gam <= 0:
        gam = float("nan")
    return lam, gam


def _fit_decay(R: np.ndarray, last_cred: np.ndarray, n: int, p: Params, warnings: list[str]) -> _Decay:
    """λ, γ, μ and σ from the reference row over [FitStart, its last credible bucket], RecoveryPct > 0.

    The log-linear (OLS) estimates are always computed; under the LM solver they are only the
    starting point and every fitted and used value is replaced by the weighted LM result."""
    ref_last_cred = int(last_cred[p.ref_ts])
    lo, hi = sorted((int(p.fit_start), ref_last_cred))
    lo, hi = max(lo, 1), min(hi, n)
    fit_b = np.arange(lo, hi + 1) if hi >= lo else np.array([], dtype=int)
    fit_r = R[p.ref_ts, fit_b] if fit_b.size else np.array([])
    pos = fit_r > 0
    fx, fy = fit_b[pos].astype(float), np.log(fit_r[pos])
    points = int(fx.size)

    lam_fit = -slope(fy, fx)
    gam_fit = -slope(fy, np.log(fx)) if fx.size else float("nan")
    if fx.size < 3:
        warnings.append(
            f"Only {fx.size} positive point(s) between FitStart {p.fit_start} and the reference row's "
            f"last credible bucket {ref_last_cred}; the fitted λ, γ and the log-normal μ / σ are unreliable")
    lam = lam_fit if p.lambda_override is None else float(p.lambda_override)
    gam = gam_fit if p.gamma_override is None else float(p.gamma_override)
    ln = _logn_fit(fx, fy, p.mu_override, p.sigma_override)
    mu, sigma = ln["mu"], ln["sigma"]
    mu_fit, sigma_fit, logn_points = ln["mu_fit"], ln["sigma_fit"], int(ln["points"])
    fit_info: dict = {}

    if p.fit_solver == "ols":
        lam, gam = _drop_rising(lam_fit, gam_fit, lam, gam, "regression", p, ref_last_cred, points, warnings)
        if ln["problem"] == "not_concave":
            warnings.append(
                "The log-normal shape cannot be fitted: ln RecoveryPct + ln b is not concave in ln b over the "
                "regression window (the quadratic coefficient is not negative), so μ and σ are undefined. "
                "Lower FitStart, set μ and σ overrides, or use another method")
    else:
        # Weighted Levenberg–Marquardt on the recovery scale, weights ∝ bucket number, as the
        # client's curve fit.
        fr = fit_r[pos].astype(float)
        lam_fit, lam, w1 = _lm_shape("exp", fx, fr, [lam_fit], [p.lambda_override], fit_info)
        gam_fit, gam, w2 = _lm_shape("power", fx, fr, [gam_fit], [p.gamma_override], fit_info)
        (mu_fit, sigma_fit), (mu, sigma), w3 = _lm_shape("logn", fx, fr, [mu_fit, sigma_fit],
                                                         [p.mu_override, p.sigma_override], fit_info)
        logn_points = points if math.isfinite(mu_fit) else 0
        lam_fit, gam_fit, lam, gam = float(lam_fit), float(gam_fit), float(lam), float(gam)
        warnings.extend(w for w in (w1, w2, w3) if w)
        lam, gam = _drop_rising(lam_fit, gam_fit, lam, gam, "weighted fit", p, ref_last_cred, points, warnings)

    return _Decay(lam_fit=lam_fit, gam_fit=gam_fit, lam=lam, gam=gam, mu_fit=mu_fit, sigma_fit=sigma_fit,
                  mu=mu, sigma=sigma, logn_points=logn_points, fit_points=points,
                  ref_last_cred=ref_last_cred, fit_info=fit_info)


def _shape_values(d: _Decay, v: float, size: int) -> tuple[np.ndarray, np.ndarray]:
    """The three shapes s(b) and the discount v^b for buckets 0..size-1 (bucket 0 is padding)."""
    bb = np.arange(size, dtype=float)
    shapes = np.zeros((3, size))
    shapes[0, 1:] = np.exp(-d.lam * bb[1:])
    shapes[1, 1:] = bb[1:] ** (-d.gam)
    if math.isfinite(d.mu) and math.isfinite(d.sigma) and d.sigma > 0:
        lb = np.log(bb[1:])
        shapes[2, 1:] = np.exp(-((lb - d.mu) ** 2) / (2.0 * d.sigma * d.sigma)) / bb[1:]
    else:
        shapes[2, :] = np.nan
    vb = v ** bb
    vb[0] = 0.0
    return shapes, vb


def _scales(shapes: np.ndarray, win: _Window, n: int, warnings: list[str]) -> np.ndarray:
    """Scale per shape and TermStep: Σ observed over the anchor window ÷ Σ shape over the window."""
    scale = np.zeros((3, n + 1))
    for k in range(3):
        den = (shapes[k, : n + 1][None, :] * win.in_win).sum(axis=1)
        s = np.where(win.n_win > 0, win.sum_obs / den, 0.0)
        bad = (win.n_win > 0) & ~np.isfinite(s) & np.isfinite(den)
        rows = np.nonzero(bad[1:])[0] + 1                # row 0 is padding and never reported
        if rows.size:
            warnings.append(
                f"{SHAPE_LABELS[SHAPES[k]]}: the shape is zero across the anchor window of TermStep "
                f"{int(rows[0])} (and {rows.size - 1} other row(s)); the tail there is set to the floor")
            s = np.where(bad, 0.0, s)
        scale[k] = s
    return scale


def _extend(R, shapes, scale, last_cred, n, size, floor) -> np.ndarray:
    """Extended RecoveryPct per shape: observed up to the last credible bucket, then
    max(floor, scale × shape); 0 where b < ts."""
    ts_idx = np.arange(n + 1)
    b_all = np.arange(size)
    below = b_all[None, :] < ts_idx[:, None]
    keep_obs = b_all[None, :] <= last_cred[:, None]
    R_wide = np.zeros((n + 1, size))
    R_wide[:, : n + 1] = R
    ext = np.zeros((3, n + 1, size))
    for k in range(3):
        tail = np.maximum(floor, scale[k][:, None] * shapes[k][None, :])
        e = np.where(keep_obs, R_wide, tail)
        e[below] = 0.0
        e[:, 0] = 0.0
        e[0, :] = 0.0
        ext[k] = e
    return ext


def _row_lgds(ext, R, vb, in_mb, v, n, p: Params) -> dict:
    """PV, LGD and undiscounted recovery of every TermStep row, per shape and observed-only."""
    ts_idx = np.arange(n + 1)
    b_all = np.arange(ext.shape[2])
    mb = int(p.max_bucket)
    disc = v ** (ts_idx - 1.0)                                # v^(ts-1)
    pv_ext = (ext * (vb * in_mb)[None, None, :]).sum(axis=2) / disc[None, :]
    lgd_ext = 1.0 - pv_ext
    pv_obs = (R * vb[: n + 1][None, :]).sum(axis=1) / disc
    lgd_obs = 1.0 - pv_obs
    in_h = in_mb[None, :] & (b_all[None, :] <= np.minimum(mb, ts_idx + p.horizon - 1)[:, None])
    pv_h = (ext * (vb[None, :] * in_h)[None, :, :]).sum(axis=2) / disc[None, :]
    lgd_h = 1.0 - pv_h
    und_ext = (ext * in_mb[None, None, :]).sum(axis=2)
    und_obs = R.sum(axis=1)

    sel = p.method - 1
    if not np.isfinite(lgd_ext[sel, 1:]).all():
        what = ("λ", "γ", "μ / σ")[sel]
        raise EngineError(
            f"The selected method cannot be computed because {what} is undefined. "
            f"Lower FitStart, set an override, or choose another method")
    return {"lgd_ext": lgd_ext, "lgd_obs": lgd_obs, "lgd_h": lgd_h, "und_ext": und_ext, "und_obs": und_obs}


def _results(tri: Triangles, rows: dict, win: _Window, mb: int, n: int, p: Params, rate: float,
             filtered: bool, warnings: list[str]) -> tuple[dict, dict]:
    """The Results table per TermStep 1..n and its exposure-weighted averages."""
    sel = p.method - 1
    lgd_ext, lgd_obs = rows["lgd_ext"], rows["lgd_obs"]
    ts_idx = np.arange(n + 1)
    s_ = slice(1, n + 1)
    exposure = np.diagonal(tri.E).copy()
    orig = tri.lgd_file.copy()
    tie = lgd_obs - tri.lgd_unfloored              # the engine's replica against the file's own PV
    floor_gap = orig - tri.lgd_unfloored           # > 0 where the risk suite floored the file LGD
    pv_orig = 1.0 - lgd_obs
    pv_sel = 1.0 - lgd_ext[sel]
    uplift = pv_sel - pv_orig
    results = {
        "ts": ts_idx[s_],
        "exposure": exposure[s_],
        "lgd_file": orig[s_],
        "lgd_replica": lgd_obs[s_],
        "tie_out": tie[s_],
        "file_floor_gap": floor_gap[s_],
        "lgd_exp": lgd_ext[0, s_],
        "lgd_power": lgd_ext[1, s_],
        "lgd_logn": lgd_ext[2, s_],
        "lgd_selected": lgd_ext[sel, s_],
        "pv_original": pv_orig[s_],
        "pv_selected": pv_sel[s_],
        "uplift": uplift[s_],
        "lgd_horizon": rows["lgd_h"][sel, s_],
        "undisc_observed": rows["und_obs"][s_],
        "undisc_selected": rows["und_ext"][sel, s_],
        "last_cred": win.last_cred[s_],
        "buckets_added": mb - np.maximum(win.last_cred, ts_idx - 1)[s_],
    }

    w = exposure[s_]
    wsum = float(w.sum())

    def wavg(col):
        return weighted_mean(w, col, wsum)

    def wavg_strict(col):
        # a shape that could not be computed stays blank instead of becoming 0
        return wavg(col) if np.isfinite(col).all() else float("nan")

    tie_valid = tie[s_][np.isfinite(tie[s_])]
    averages = {
        "exposure_total": wsum,
        "lgd_file": wavg(orig[s_]),
        "lgd_replica": wavg(lgd_obs[s_]),
        "lgd_exp": wavg_strict(lgd_ext[0, s_]),
        "lgd_power": wavg_strict(lgd_ext[1, s_]),
        "lgd_logn": wavg_strict(lgd_ext[2, s_]),
        "lgd_selected": wavg(lgd_ext[sel, s_]),
        "uplift": wavg(uplift[s_]),
        "uplift_simple": float(uplift[s_].mean()),
        "tie_max": float(tie_valid.max()) if tie_valid.size else float("nan"),
        "tie_min": float(tie_valid.min()) if tie_valid.size else float("nan"),
    }

    tie_abs = max(abs(averages["tie_max"]), abs(averages["tie_min"])) if tie_valid.size else 0.0
    source = "the rebuilt vintage subset" if filtered else "the file"
    if tie_abs > 1e-6:
        msg = f"Tie-out is {tie_abs:.6f}: the replica does not reproduce {source}'s LGD"
        if abs(rate - tri.implied_rate) > 1e-9:
            msg += (f". The discount rate used ({rate:.4%}) differs from the rate implied by the file "
                    f"({tri.implied_rate:.4%})")
        warnings.append(msg)

    floored = np.nonzero(np.nan_to_num(floor_gap[s_]) > 1e-12)[0] + 1
    averages["file_floored_count"] = int(floored.size)
    if floored.size:
        gap = float(np.nanmax(floor_gap[s_]))
        if filtered:
            warnings.append(
                f"The LGD of the vintage subset is floored at the previous TermStep's LGD, as the risk suite "
                f"does, for {floored.size} TermStep(s) ({ranges_text(floored)}); the largest gap is "
                f"{gap:.6f}. The tie-out is measured on 1 − CumulativeSumPV, and the extended LGD is not floored")
        else:
            warnings.append(
                f"The file's LGD is floored at the previous TermStep's LGD for {floored.size} TermStep(s) "
                f"({ranges_text(floored)}); the largest gap is {gap:.6f}. 'Original LGD' shows the file value; "
                f"the tie-out is measured on 1 − CumulativeSumPV, and the extended LGD is not floored")
    return results, averages


def _lgd_to_target(ext, rows, tri, vb, in_mb, v, n, width, size, p: Params):
    """LGD for TermStep 1..Target: each row's own extended LGD up to LastTS, beyond it the base
    row's extended cash curve c(b) rolled forward (RecoveryPct(ts, b) = c(b) ÷ balance factor)."""
    sel = p.method - 1
    mb, target = int(p.max_bucket), int(p.target_ts)
    exposure = np.diagonal(tri.E)
    has_data = np.nonzero(exposure[1:] > 0)[0]
    last_data_ts = int(has_data[-1] + 1) if has_data.size else 0
    last_ts = last_data_ts if p.last_ts is None else int(p.last_ts)

    base = np.zeros((3, size))
    base[:, : width + 1] = ext[:, p.base_ts, : width + 1]     # the triangle is `width` buckets wide
    T = np.arange(1, target + 1)
    c_cum = np.cumsum(base, axis=1)                                  # Σ c(1..b)
    d_cum = np.cumsum(base * (vb * in_mb)[None, :], axis=1)          # Σ c v^b over b ≤ MaxBucket
    u_cum = np.cumsum(base * in_mb[None, :], axis=1)

    pre = np.where(T[None, :] > p.base_ts, c_cum[:, T - 1] - c_cum[:, p.base_ts - 1][:, None], 0.0)
    factor = 1.0 - pre                                               # (3, target)
    denom = np.maximum(_EPS, factor)
    vt = v ** (T - 1.0)

    def derived_lgd(end):                                            # PV over buckets ts..end
        return 1.0 - (d_cum[:, end] - d_cum[:, T - 1]) / vt[None, :] / denom

    def horizon_end(h):
        return np.maximum(np.minimum(size - 1, np.minimum(mb, T + h - 1)), T - 1)

    end_all = np.full(target, size - 1)
    derived = derived_lgd(end_all)
    derived_h2 = derived_lgd(horizon_end(p.horizon2))
    derived_h1 = derived_lgd(horizon_end(p.horizon))
    derived_und = (u_cum[:, end_all] - u_cum[:, T - 1]) / denom

    m = min(target, n)
    k_own = min(m, max(last_ts, 0))

    def own_rows(arr):                                               # the row's own figure, ts ≤ LastTS
        out = np.full(target, np.nan)
        out[:k_own] = arr[sel, 1:k_own + 1]
        return out

    def observed(arr):                                               # an observed column, ts ≤ n
        out = np.full(target, np.nan)
        out[:m] = arr[1:m + 1]
        return out

    own, own_h, own_und = own_rows(rows["lgd_ext"]), own_rows(rows["lgd_h"]), own_rows(rows["und_ext"])
    is_own = np.isfinite(own)
    lgd_ts = {
        "ts": T,
        "exposure": observed(exposure),
        "lgd_file": observed(tri.lgd_file),
        "lgd_own": own,
        "balance_factor": factor[sel],
        "derived_exp": derived[0],
        "derived_power": derived[1],
        "derived_logn": derived[2],
        "derived_selected": derived[sel],
        "lgd_final": np.where(is_own, own, derived[sel]),
        "validation": own - derived[sel],
        "lgd_valuation_horizon": derived_h2[sel],
        "lgd_short_horizon": np.where(is_own, own_h, derived_h1[sel]),
        "undisc_remaining": np.where(is_own, own_und, derived_und[sel]),
        "source": np.where(is_own, "own row", "derived"),
    }
    val = lgd_ts["validation"][is_own]
    summary = {
        "validation_max": float(val.max()) if val.size else float("nan"),
        "validation_min": float(val.min()) if val.size else float("nan"),
        "balance_factor_target": float(factor[sel, -1]),
        "last_ts": last_ts,
        "target_ts": target,
    }
    return lgd_ts, summary, base, last_ts, last_data_ts


def _add_setting_warnings(warnings, obs: _Observed, win: _Window, p: Params, opening, min_exp, mb, target,
                          last_ts, last_data_ts, factor_sel, T) -> None:
    """Warnings about the vintage window and the chosen MaxBucket / Target / LastTS."""
    tri, vinfo = obs.tri, obs.vinfo
    if obs.filtered:
        warnings.insert(0,
            f"Vintages from {vinfo['vintage_start_effective']} to the report date {vinfo['report_month']}: "
            f"{vinfo['cohorts_included']} of "
            f"{vinfo['cohorts_total']} cohorts, {vinfo['exposure_share']:.0%} of the at-default exposure. "
            f"The last observed bucket is {tri.last_obs_file} against {obs.full.last_obs_file} with all vintages, "
            f"and the opening exposure {money_text(opening)} against {money_text(float(obs.full.E[1, 1]))}, so a "
            f"percentage MinExposure is relative to the subset")
        if p.min_exposure_mode == "abs" and int(win.n_win[p.ref_ts]) < p.window:
            warnings.append(
                f"With vintages from {vinfo['vintage_start_effective']} only {int(win.n_win[p.ref_ts])} credible "
                f"bucket(s) remain on the reference row against a window of {p.window}: the Rand MinExposure "
                f"of {money_text(float(min_exp))} cuts credibility early on the smaller book. Consider stating "
                f"MinExposure as a percentage of the opening exposure")
    if mb < tri.last_obs_file:
        warnings.append(
            f"MaxBucket {mb} is below the last observed bucket {tri.last_obs_file}; observed "
            f"recoveries beyond MaxBucket are left out of the extended LGD")
    if target < last_data_ts:
        warnings.append(
            f"Target TermStep {target} is below the last observed TermStep {last_data_ts}; "
            f"the LGD table stops at {target}")
    if target > mb:
        warnings.append(
            f"Target TermStep {target} is beyond MaxBucket {mb}; TermSteps after {mb} have no "
            f"buckets left and show LGD 1")
    elif mb - target + 1 < p.horizon2:
        warnings.append(
            f"At the Target TermStep only {mb - target + 1} bucket(s) remain before MaxBucket, "
            f"fewer than the valuation horizon of {p.horizon2}")
    if float(factor_sel.min()) <= _EPS:
        first = int(T[np.nonzero(factor_sel <= _EPS)[0][0]])
        warnings.append(
            f"The base-row balance factor reaches zero at TermStep {first}; derived LGD beyond "
            f"that point is not meaningful")
    if last_ts > last_data_ts:
        warnings.append(
            f"LastTS {last_ts} is beyond the last observed TermStep with data ({last_data_ts})")


def _config(data, tri, obs: _Observed, d: _Decay, p: Params, rate, v, mb, target, min_exp, opening,
            last_ts, last_data_ts) -> dict:
    """The effective and derived Config values reported with every result."""
    vinfo = obs.vinfo
    logn_ok = math.isfinite(d.mu) and math.isfinite(d.sigma)
    lam = d.lam
    return {
        "event_type": p.event_type,
        "category": data.category,
        "rate": rate,
        "rate_implied": tri.implied_rate,
        "v": v,
        "max_bucket": mb,
        "target_ts": target,
        "min_exposure_abs": float(min_exp),
        "opening_exposure": opening,
        "last_obs_file": tri.last_obs_file,
        "fit_solver": p.fit_solver,
        "fit_solver_label": SOLVER_LABELS[p.fit_solver],
        "fit_info": d.fit_info,
        "lam_fit": d.lam_fit,
        "gam_fit": d.gam_fit,
        "lam": lam,
        "gam": d.gam,
        "mu_fit": d.mu_fit,
        "sigma_fit": d.sigma_fit,
        "mu": d.mu,
        "sigma": d.sigma,
        "logn_mode": math.exp(d.mu - d.sigma * d.sigma) if logn_ok else float("nan"),
        "logn_median": math.exp(d.mu) if logn_ok else float("nan"),
        "logn_points": d.logn_points,
        "ref_last_cred": d.ref_last_cred,
        "fit_points": d.fit_points,
        "half_life": math.log(2.0) / lam if lam and math.isfinite(lam) and lam != 0 else float("nan"),
        "last_ts": last_ts,
        "last_data_ts": last_data_ts,
        "method": p.method,
        "method_label": SHAPE_LABELS[SHAPES[p.method - 1]],
        "vintage_filter": obs.filtered,
        "vintage_start_effective": vinfo["vintage_start_effective"],
        "report_month": vinfo["report_month"],
        "vintage_first": vinfo["vintage_first"],
        "vintage_last": vinfo["vintage_last"],
        "cohorts_included": vinfo["cohorts_included"],
        "cohorts_total": vinfo["cohorts_total"],
        "exposure_share": vinfo["exposure_share"],
        "last_obs_unfiltered": obs.full.last_obs_file,
        "lgd_file_label": (f"LGD (vintages from {vinfo['vintage_start_effective']})" if obs.filtered
                           else "Original LGD (file)"),
    }


# ============================================================================ curve parameters
def curve_parameters(res: ExtensionResult, ts: int = 1) -> list[dict]:
    """The parameters behind each forecast curve, for publishing next to the curves.

    One entry per shape: the formula, each parameter with its fitted and used value (and
    whether an override replaced the fit), the level (scale) that anchors the shape on the
    window of TermStep ``ts`` and on the base row, and the derived figures (half-life, peak
    bucket, median). The extended RecoveryPct of row ts beyond its last credible bucket is
    max(floor, scale(ts) × s(b)).
    """
    cfg, p, T = res.config, res.params, res.tail_fit
    i = max(0, min(res.n, int(ts)) - 1)
    b = max(0, min(res.n, int(p["base_ts"])) - 1)
    val = finite_or_none

    def param(name, fit_key, used_key, override_key):
        return {"name": name, "fitted": val(cfg[fit_key]), "used": val(cfg[used_key]),
                "override": p.get(override_key) is not None}

    spec = {
        "exp": ([param("λ", "lam_fit", "lam", "lambda_override")], {"half_life": val(cfg["half_life"])}),
        "power": ([param("γ", "gam_fit", "gam", "gamma_override")], {}),
        "logn": ([param("μ", "mu_fit", "mu", "mu_override"), param("σ", "sigma_fit", "sigma", "sigma_override")],
                 {"peak_bucket": val(cfg["logn_mode"]), "median_bucket": val(cfg["logn_median"])}),
    }
    rows = []
    for k, key in enumerate(SHAPES):
        params, derived = spec[key]
        scale = T[f"scale_{key}"]
        rows.append({
            "shape": key, "label": SHAPE_LABELS[key], "formula": SHAPE_FORMULAS[key],
            "selected": k == int(cfg["method"]) - 1, "params": params, "derived": derived,
            "scale_ts": val(scale[i]), "scale_base": val(scale[b]),
            "last_cred_ts": int(T["last_cred"][i]),
            "fit_window": {"from": int(p["fit_start"]), "to": int(cfg["ref_last_cred"]), "ref_ts": int(p["ref_ts"]),
                           "points": int(cfg["logn_points"] if key == "logn" else cfg["fit_points"])},
            "floor": float(p["floor"]),
        })
    return rows
