"""Hazard-rate bucket extension engine.

A numpy re-statement of the formula-driven framework workbook
(Hazard_Obs -> Tail_Fit -> Ext_Exp / Ext_Power / Ext_LogN -> Results -> LGD_300).
All triangle arrays are 1-indexed: element [ts, b]; index 0 is unused padding.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .params import Params
from .parse import RecoveryData, VintageError

SHAPES = ("exp", "power", "logn")
SHAPE_LABELS = {"exp": "Exponential", "power": "Power law", "logn": "Log-normal"}
_EPS = 1e-9   # the workbook's MAX(0.000000001, balance factor)


class EngineError(ValueError):
    """The parameters cannot be applied to this dataset."""


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
            "config": _clean(self.config),
            "n": self.n,
            "averages": _clean(self.averages),
            "results": {k: _clean_list(v) for k, v in self.results.items()},
            "tail_fit": {k: _clean_list(v) for k, v in self.tail_fit.items()},
            "lgd_ts": {k: _clean_list(v) for k, v in self.lgd_ts.items()},
            "lgd_ts_summary": _clean(self.lgd_ts_summary),
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
        if obs_end >= ts:
            observed[: obs_end - ts + 1] = self.R[ts, ts:obs_end + 1]
            exposure_tail = self.E[ts, ts:obs_end + 1]
            observed[: obs_end - ts + 1][exposure_tail <= 0] = np.nan
        exposure = np.full(b.shape, np.nan)
        if obs_end >= ts:
            exposure[: obs_end - ts + 1] = self.E[ts, ts:obs_end + 1]
        return {
            "ts": ts,
            "bucket": b.tolist(),
            "observed": _clean_list(observed),
            "exposure": _clean_list(exposure),
            "exp": _clean_list(self.ext[0, ts, ts:mb + 1]),
            "power": _clean_list(self.ext[1, ts, ts:mb + 1]),
            "logn": _clean_list(self.ext[2, ts, ts:mb + 1]),
            "last_cred": int(self.tail_fit["last_cred"][ts - 1]),
            "last_obs": int(self.tail_fit["last_obs"][ts - 1]),
        }


def _clean(d: dict) -> dict:
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


def _clean_list(a) -> list:
    arr = np.asarray(a)
    if arr.dtype.kind in "US":
        return arr.tolist()
    if arr.dtype.kind in "iu":
        return arr.astype(int).tolist()
    arr = arr.astype(float)
    return [x if math.isfinite(x) else None for x in arr.tolist()]


def _slope(y: np.ndarray, x: np.ndarray) -> float:
    """Least-squares slope of y on x (Excel SLOPE). nan when undefined."""
    if x.size < 2:
        return float("nan")
    dx = x - x.mean()
    den = float((dx * dx).sum())
    if den == 0.0:
        return float("nan")
    return float((dx * (y - y.mean())).sum() / den)


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
        mu = _slope(y + x * x / (2.0 * sigma * sigma), x) * sigma * sigma if fx.size else float("nan")
    elif mu_override is not None:
        mu = float(mu_override)
        a2 = _slope(y, x * x - 2.0 * mu * x) if fx.size else float("nan")
        sigma = math.sqrt(-1.0 / (2.0 * a2)) if math.isfinite(a2) and a2 < 0 else float("nan")
        if math.isfinite(a2) and a2 >= 0:
            out["problem"] = "not_concave"
    else:
        mu, sigma = out["mu_fit"], out["sigma_fit"]
    out["mu"], out["sigma"] = mu, sigma
    return out


def _ranges(values) -> str:
    """Compact text for a sorted list of integers: 2-5, 9, 12-14."""
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


def _last_true(mask: np.ndarray) -> np.ndarray:
    """Per row of a (rows, cols) mask whose column index is the bucket: the largest
    column index that is True, 0 when none."""
    cols = np.arange(mask.shape[1])
    return np.where(mask, cols, 0).max(axis=1)


def _money(x: float) -> str:
    if x >= 1e9:
        return f"R{x / 1e9:.2f}bn"
    if x >= 1e6:
        return f"R{x / 1e6:.1f}m"
    return f"R{x:,.0f}"


def compute(data: RecoveryData, params: Params) -> ExtensionResult:
    """Run the extension framework for one dataset under one parameter set."""
    # A shape that cannot be computed (undefined decay) carries nan through its own columns by
    # design; the selected shape is checked explicitly below.
    with np.errstate(invalid="ignore", over="ignore", divide="ignore"):
        return _compute(data, params)


def _compute(data: RecoveryData, params: Params) -> ExtensionResult:
    p = params
    warnings: list[str] = []
    try:
        v_start, vinfo = data.resolve_vintages(p.event_type, p.vintage_start, p.vintage_years)
        tri = data.triangles(p.event_type, v_start)
    except VintageError as exc:
        raise EngineError(str(exc)) from None
    n, R, E = tri.n, tri.R, tri.E
    filtered = bool(vinfo["vintage_filter"])
    full = data.triangles(p.event_type) if filtered else tri
    if filtered and not (E > 0).any():
        raise EngineError(f"Vintages from {vinfo['vintage_start_effective']} leave no exposure in the triangle; "
                          f"choose an earlier start")

    if p.ref_ts > n:
        raise EngineError(f"Reference TermStep {p.ref_ts} is beyond the observed range 1..{n}")
    if p.base_ts > n:
        raise EngineError(f"Base TermStep {p.base_ts} is beyond the observed range 1..{n}")

    rate = tri.implied_rate if p.rate is None else float(p.rate)
    v = (1.0 + rate) ** (-1.0 / 12.0)
    mb = int(p.max_bucket)
    width = max(mb, n)
    target = int(p.target_ts)
    size = max(width, target) + 2            # padded so every index used below exists

    opening = float(E[1, 1])
    min_exp = p.min_exposure if p.min_exposure_mode == "abs" else p.min_exposure / 100.0 * opening

    ts_idx = np.arange(n + 1)                # 0..n
    b_obs = np.arange(n + 1)                 # bucket index over observed columns

    # ---------------------------------------------------------------- Tail_Fit
    obs_mask = E > 0
    obs_mask[:, 0] = False
    last_obs = _last_true(obs_mask)
    cred_mask = E >= min_exp
    cred_mask[:, 0] = False
    cred = _last_true(cred_mask)
    last_cred = np.where(cred >= ts_idx, cred, last_obs)
    win_start = np.maximum(ts_idx, last_cred - p.window + 1)
    win_end = last_cred.copy()
    n_win = np.where(last_cred < ts_idx, 0, win_end - win_start + 1)

    in_win = (b_obs[None, :] >= win_start[:, None]) & (b_obs[None, :] <= win_end[:, None])
    in_win[:, 0] = False
    in_win &= (n_win > 0)[:, None]
    sum_obs = (R * in_win).sum(axis=1)

    # decay parameters from the reference row
    ref_last_cred = int(last_cred[p.ref_ts])
    lo, hi = sorted((int(p.fit_start), ref_last_cred))
    lo, hi = max(lo, 1), min(hi, n)
    fit_b = np.arange(lo, hi + 1) if hi >= lo else np.array([], dtype=int)
    fit_r = R[p.ref_ts, fit_b] if fit_b.size else np.array([])
    pos = fit_r > 0
    fx, fy = fit_b[pos].astype(float), np.log(fit_r[pos])
    lam_fit = -_slope(fy, fx)
    gam_fit = -_slope(fy, np.log(fx)) if fx.size else float("nan")
    if fx.size < 3:
        warnings.append(
            f"Only {fx.size} positive point(s) between FitStart {p.fit_start} and the reference row's "
            f"last credible bucket {ref_last_cred}; the fitted λ, γ and the log-normal μ / σ are unreliable")
    lam = lam_fit if p.lambda_override is None else float(p.lambda_override)
    gam = gam_fit if p.gamma_override is None else float(p.gamma_override)
    ln = _logn_fit(fx, fy, p.mu_override, p.sigma_override)
    mu, sigma = ln["mu"], ln["sigma"]
    if ln["problem"] == "not_concave":
        warnings.append(
            "The log-normal shape cannot be fitted: ln RecoveryPct + ln b is not concave in ln b over the "
            "regression window (the quadratic coefficient is not negative), so μ and σ are undefined. "
            "Lower FitStart, set μ and σ overrides, or use another method")

    # shapes by bucket 1..size-1
    bb = np.arange(size, dtype=float)
    shapes = np.zeros((3, size))
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        shapes[0, 1:] = np.exp(-lam * bb[1:])
        shapes[1, 1:] = bb[1:] ** (-gam)
        if math.isfinite(mu) and math.isfinite(sigma) and sigma > 0:
            lb = np.log(bb[1:])
            shapes[2, 1:] = np.exp(-((lb - mu) ** 2) / (2.0 * sigma * sigma)) / bb[1:]
        else:
            shapes[2, :] = np.nan
    vb = v ** bb
    vb[0] = 0.0

    # scale per TermStep and shape: sum of observed over window / sum of shape over window
    scale = np.zeros((3, n + 1))
    for k in range(3):
        den = (shapes[k, : n + 1][None, :] * in_win).sum(axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            s = np.where(n_win > 0, sum_obs / den, 0.0)
        bad = (n_win > 0) & ~np.isfinite(s) & np.isfinite(den)
        if bad[1:].any():
            first = int(np.nonzero(bad)[0][0])
            warnings.append(
                f"{SHAPE_LABELS[SHAPES[k]]}: the shape is zero across the anchor window of TermStep "
                f"{first} (and {int(bad.sum()) - 1} other row(s)); the tail there is set to the floor")
            s = np.where(bad, 0.0, s)
        scale[k] = s

    # ------------------------------------------------------ extended triangles
    b_all = np.arange(size)
    below = b_all[None, :] < ts_idx[:, None]                  # b < ts
    keep_obs = b_all[None, :] <= last_cred[:, None]           # b <= last credible
    R_wide = np.zeros((n + 1, size))
    R_wide[:, : n + 1] = R
    ext = np.zeros((3, n + 1, size))
    for k in range(3):
        tail = np.maximum(p.floor, scale[k][:, None] * shapes[k][None, :])
        e = np.where(keep_obs, R_wide, tail)
        e[below] = 0.0
        e[:, 0] = 0.0
        e[0, :] = 0.0
        ext[k] = e

    in_mb = (b_all <= mb) & (b_all >= 1)
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
        what = "λ" if sel == 0 else "γ" if sel == 1 else "μ / σ"
        raise EngineError(
            f"The selected method cannot be computed because {what} is undefined. "
            f"Lower FitStart, set an override, or choose another method")

    # ----------------------------------------------------------------- Results
    s_ = slice(1, n + 1)
    exposure = np.diagonal(E).copy()
    orig = tri.lgd_file.copy()
    unfloored = tri.lgd_unfloored
    tie = lgd_obs - unfloored                  # the engine's replica against the file's own PV
    floor_gap = orig - unfloored               # > 0 where the risk suite floored the file LGD
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
        "lgd_horizon": lgd_h[sel, s_],
        "undisc_observed": und_obs[s_],
        "undisc_selected": und_ext[sel, s_],
        "last_cred": last_cred[s_],
        "buckets_added": mb - np.maximum(last_cred, ts_idx - 1)[s_],
    }

    w = exposure[s_]
    wsum = float(w.sum())

    def wavg(col):
        if wsum <= 0:
            return float("nan")
        return float((w * np.nan_to_num(col, nan=0.0)).sum() / wsum)

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
        warnings.append(
            f"Tie-out is {tie_abs:.6f}: the replica does not reproduce {source}'s LGD. "
            f"The discount rate used ({rate:.4%}) differs from the rate implied by the file "
            f"({tri.implied_rate:.4%})" if abs(rate - tri.implied_rate) > 1e-9 else
            f"Tie-out is {tie_abs:.6f}: the replica does not reproduce {source}'s LGD")

    floored = np.nonzero(np.nan_to_num(floor_gap[s_]) > 1e-12)[0] + 1
    averages["file_floored_count"] = int(floored.size)
    if floored.size:
        if filtered:
            warnings.append(
                f"The LGD of the vintage subset is floored at the previous TermStep's LGD, as the risk suite "
                f"does, for {floored.size} TermStep(s) ({_ranges(floored)}); the largest gap is "
                f"{float(np.nanmax(floor_gap[s_])):.6f}. The tie-out is measured on 1 − CumulativeSumPV, "
                f"and the extended LGD is not floored")
        else:
            warnings.append(
                f"The file's LGD is floored at the previous TermStep's LGD for {floored.size} TermStep(s) "
                f"({_ranges(floored)}); the largest gap is {float(np.nanmax(floor_gap[s_])):.6f}. "
                f"'Original LGD' shows the file value; the tie-out is measured on 1 − CumulativeSumPV, "
                f"and the extended LGD is not floored")

    tail_fit = {
        "ts": ts_idx[s_],
        "last_obs": last_obs[s_],
        "last_cred": last_cred[s_],
        "win_start": win_start[s_],
        "win_end": win_end[s_],
        "n_win": n_win[s_],
        "sum_obs": sum_obs[s_],
        "scale_exp": scale[0, s_],
        "scale_power": scale[1, s_],
        "scale_logn": scale[2, s_],
    }

    # ------------------------------------------------ LGD to the Target TermStep
    has_data = np.nonzero(exposure[1:] > 0)[0]
    last_data_ts = int(has_data[-1] + 1) if has_data.size else 0
    last_ts = last_data_ts if p.last_ts is None else int(p.last_ts)

    base = np.zeros((3, size))
    base[:, : width + 1] = ext[:, p.base_ts, : width + 1]     # the triangle is `width` buckets wide
    T = np.arange(1, target + 1)
    c_cum = np.cumsum(base, axis=1)                                  # sum c(1..b)
    d_cum = np.cumsum(base * (vb * in_mb)[None, :], axis=1)          # sum c v^b over b<=MaxBucket
    u_cum = np.cumsum(base * in_mb[None, :], axis=1)

    pre = np.where(T[None, :] > p.base_ts,
                   c_cum[:, T - 1] - c_cum[:, p.base_ts - 1][:, None], 0.0)
    factor = 1.0 - pre                                               # (3, target)
    denom = np.maximum(_EPS, factor)
    vt = v ** (T - 1.0)
    end_all = np.full(target, size - 1)

    def window_pv(end):                                              # sum over ts..end
        return d_cum[:, end] - d_cum[:, T - 1]

    derived = 1.0 - window_pv(end_all) / vt[None, :] / denom
    end_h2 = np.minimum(size - 1, np.minimum(mb, T + p.horizon2 - 1))
    end_h2 = np.maximum(end_h2, T - 1)
    derived_h2 = 1.0 - window_pv(end_h2) / vt[None, :] / denom
    end_h1 = np.minimum(size - 1, np.minimum(mb, T + p.horizon - 1))
    end_h1 = np.maximum(end_h1, T - 1)
    derived_h1 = 1.0 - window_pv(end_h1) / vt[None, :] / denom
    derived_und = (u_cum[:, end_all] - u_cum[:, T - 1]) / denom

    own = np.full(target, np.nan)
    own_h = np.full(target, np.nan)
    own_und = np.full(target, np.nan)
    exp_t = np.full(target, np.nan)
    orig_t = np.full(target, np.nan)
    m = min(target, n)
    exp_t[:m] = exposure[1:m + 1]
    orig_t[:m] = orig[1:m + 1]
    k_own = min(m, max(last_ts, 0))
    own[:k_own] = lgd_ext[sel, 1:k_own + 1]
    own_h[:k_own] = lgd_h[sel, 1:k_own + 1]
    own_und[:k_own] = und_ext[sel, 1:k_own + 1]
    is_own = np.isfinite(own)

    lgd_ts = {
        "ts": T,
        "exposure": exp_t,
        "lgd_file": orig_t,
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
    lgd_ts_summary = {
        "validation_max": float(val.max()) if val.size else float("nan"),
        "validation_min": float(val.min()) if val.size else float("nan"),
        "balance_factor_target": float(factor[sel, -1]),
        "last_ts": last_ts,
        "target_ts": target,
    }

    # ---------------------------------------------------------------- warnings
    if filtered:
        warnings.insert(0,
            f"Vintages from {vinfo['vintage_start_effective']}: {vinfo['cohorts_included']} of "
            f"{vinfo['cohorts_total']} cohorts, {vinfo['exposure_share']:.0%} of the at-default exposure. "
            f"The last observed bucket is {tri.last_obs_file} against {full.last_obs_file} with all vintages, "
            f"and the opening exposure {_money(opening)} against {_money(float(full.E[1, 1]))}, so a "
            f"percentage MinExposure is relative to the subset")
        if p.min_exposure_mode == "abs" and int(n_win[p.ref_ts]) < p.window:
            warnings.append(
                f"With vintages from {vinfo['vintage_start_effective']} only {int(n_win[p.ref_ts])} credible "
                f"bucket(s) remain on the reference row against a window of {p.window}: the Rand MinExposure "
                f"of {_money(float(min_exp))} cuts credibility early on the smaller book. Consider stating "
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
    if float(factor[sel].min()) <= _EPS:
        first = int(T[np.nonzero(factor[sel] <= _EPS)[0][0]])
        warnings.append(
            f"The base-row balance factor reaches zero at TermStep {first}; derived LGD beyond "
            f"that point is not meaningful")
    if last_ts > last_data_ts:
        warnings.append(
            f"LastTS {last_ts} is beyond the last observed TermStep with data ({last_data_ts})")

    logn_ok = math.isfinite(mu) and math.isfinite(sigma)
    config = {
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
        "lam_fit": lam_fit,
        "gam_fit": gam_fit,
        "lam": lam,
        "gam": gam,
        "mu_fit": ln["mu_fit"],
        "sigma_fit": ln["sigma_fit"],
        "mu": mu,
        "sigma": sigma,
        "logn_mode": math.exp(mu - sigma * sigma) if logn_ok else float("nan"),
        "logn_median": math.exp(mu) if logn_ok else float("nan"),
        "logn_points": int(ln["points"]),
        "ref_last_cred": ref_last_cred,
        "fit_points": int(fx.size),
        "half_life": math.log(2.0) / lam if lam and math.isfinite(lam) and lam != 0 else float("nan"),
        "last_ts": last_ts,
        "last_data_ts": last_data_ts,
        "method": p.method,
        "method_label": SHAPE_LABELS[SHAPES[sel]],
        "vintage_filter": filtered,
        "vintage_start_effective": vinfo["vintage_start_effective"],
        "vintage_first": vinfo["vintage_first"],
        "vintage_last": vinfo["vintage_last"],
        "cohorts_included": vinfo["cohorts_included"],
        "cohorts_total": vinfo["cohorts_total"],
        "exposure_share": vinfo["exposure_share"],
        "last_obs_unfiltered": full.last_obs_file,
        "lgd_file_label": (f"LGD (vintages from {vinfo['vintage_start_effective']})" if filtered
                           else "Original LGD (file)"),
    }

    return ExtensionResult(
        params=p.model_dump(), config=config, n=n, width=size - 1, R=R, E=E,
        shapes=shapes, vb=vb, ext=ext, tail_fit=tail_fit, results=results,
        averages=averages, lgd_ts=lgd_ts, lgd_ts_summary=lgd_ts_summary,
        base_curves=base, warnings=warnings,
        shape_stats={"lgd": lgd_ext, "lgd_h": lgd_h, "und": und_ext, "lgd_obs": lgd_obs,
                     "und_obs": und_obs, "tri_width": width},
    )


SHAPE_FORMULAS = {
    "exp": "s(b) = e^(−λb)",
    "power": "s(b) = b^(−γ)",
    "logn": "s(b) = (1/b) · exp(−(ln b − μ)² / (2σ²))",
}


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

    def val(x):
        x = None if x is None else float(x)
        return None if x is None or not math.isfinite(x) else x

    rows = []
    for k, key in enumerate(SHAPES):
        if key == "exp":
            params = [{"name": "λ", "fitted": val(cfg["lam_fit"]), "used": val(cfg["lam"]),
                       "override": p.get("lambda_override") is not None}]
            derived = {"half_life": val(cfg["half_life"])}
        elif key == "power":
            params = [{"name": "γ", "fitted": val(cfg["gam_fit"]), "used": val(cfg["gam"]),
                       "override": p.get("gamma_override") is not None}]
            derived = {}
        else:
            params = [{"name": "μ", "fitted": val(cfg["mu_fit"]), "used": val(cfg["mu"]),
                       "override": p.get("mu_override") is not None},
                      {"name": "σ", "fitted": val(cfg["sigma_fit"]), "used": val(cfg["sigma"]),
                       "override": p.get("sigma_override") is not None}]
            derived = {"peak_bucket": val(cfg["logn_mode"]), "median_bucket": val(cfg["logn_median"])}
        scale_key = {"exp": "scale_exp", "power": "scale_power", "logn": "scale_logn"}[key]
        rows.append({
            "shape": key, "label": SHAPE_LABELS[key], "formula": SHAPE_FORMULAS[key],
            "selected": k == int(cfg["method"]) - 1, "params": params, "derived": derived,
            "scale_ts": val(T[scale_key][i]), "scale_base": val(T[scale_key][b]),
            "last_cred_ts": int(T["last_cred"][i]),
            "fit_window": {"from": int(p["fit_start"]), "to": int(cfg["ref_last_cred"]), "ref_ts": int(p["ref_ts"]),
                           "points": int(cfg["logn_points"] if key == "logn" else cfg["fit_points"])},
            "floor": float(p["floor"]),
        })
    return rows
