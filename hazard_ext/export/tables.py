"""What the exports share apart from Excel mechanics: the column definitions of the result
tables, the parameter labels, CSV output, the vintage wording and the final-basis marginals."""
from __future__ import annotations

import csv
import io

import numpy as np

from ..engine._util import EPS as _EPS

# (key, heading, format) – headings follow the example workbooks
RESULT_COLUMNS = [
    ("ts", "TermStep", "int"),
    ("exposure", "Exposure at ts (R)", "money"),
    ("lgd_file", "Original LGD (file)", "lgd"),
    ("lgd_replica", "Replica LGD (observed only)", "lgd"),
    ("tie_out", "Tie-out (replica − file PV basis)", "sci"),
    ("file_floor_gap", "File LGD floor (file LGD − (1 − CumulativeSumPV))", "lgd6"),
    ("lgd_exp", "LGD – exponential", "lgd"),
    ("lgd_power", "LGD – power", "lgd"),
    ("lgd_logn", "LGD – log-normal", "lgd"),
    ("lgd_selected", "LGD – SELECTED", "lgd"),
    ("pv_original", "Original PV recoveries", "lgd"),
    ("pv_selected", "Selected PV recoveries", "lgd"),
    ("uplift", "Uplift in recoveries", "lgd"),
    ("lgd_horizon", "LGD within Horizon – selected", "lgd"),
    ("undisc_observed", "Undiscounted recoveries – observed", "lgd"),
    ("undisc_selected", "Undiscounted recoveries – selected", "lgd"),
    ("last_cred", "Last credible bucket", "int"),
    ("buckets_added", "Buckets added", "int"),
]

LGD_TS_COLUMNS = [
    ("ts", "TermStep", "int"),
    ("exposure", "Exposure at ts (R) – observed rows", "money"),
    ("lgd_file", "Original LGD (file)", "lgd"),
    ("lgd_own", "Own-row LGD – selected method (ts ≤ LastTS)", "lgd"),
    ("balance_factor", "Balance factor at ts (base row, selected)", "lgd"),
    ("derived_exp", "Derived LGD – exponential", "lgd"),
    ("derived_power", "Derived LGD – power", "lgd"),
    ("derived_logn", "Derived LGD – log-normal", "lgd"),
    ("derived_selected", "Derived LGD – selected", "lgd"),
    ("lgd_final", "LGD FINAL (own row to LastTS, derived beyond)", "lgd"),
    ("validation", "Validation: own-row − derived", "lgd"),
    ("lgd_valuation_horizon", "LGD within valuation horizon (derived basis)", "lgd"),
    ("lgd_short_horizon", "FINAL LGD within short horizon", "lgd"),
    ("undisc_remaining", "Undiscounted remaining recovery – FINAL basis", "lgd"),
    ("source", "Source", "text"),
]

TAIL_COLUMNS = [
    ("ts", "TermStep", "int"),
    ("last_obs", "Last observed bucket", "int"),
    ("last_cred", "Last credible bucket", "int"),
    ("win_start", "Window start", "int"),
    ("win_end", "Window end", "int"),
    ("n_win", "Buckets in window", "int"),
    ("sum_obs", "Σ observed over window", "num"),
    ("scale_exp", "Scale – exponential", "num"),
    ("scale_power", "Scale – power", "num"),
    ("scale_logn", "Scale – log-normal", "num"),
]

TABLES = {
    "results": ("results", RESULT_COLUMNS),
    "lgd_ts": ("lgd_ts", LGD_TS_COLUMNS),
    "tail_fit": ("tail_fit", TAIL_COLUMNS),
}

PARAM_LABELS = [
    ("event_type", "EventType"),
    ("rate", "Discount rate p.a. (blank = implied by the file)"),
    ("target_ts", "Target TermStep"),
    ("max_bucket", "MaxBucket"),
    ("min_exposure_mode", "MinExposure basis (abs = Rand, pct = % of TermStep 1 opening exposure)"),
    ("min_exposure", "MinExposure (credibility cut)"),
    ("window", "Window W (buckets)"),
    ("fit_start", "FitStart bucket"),
    ("fit_solver", "Fit solver (lm = Levenberg–Marquardt weighted by bucket, ols = log-linear least squares)"),
    ("ref_ts", "Reference TermStep for λ / γ"),
    ("method", "Method (1 = exponential, 2 = power law, 3 = log-normal)"),
    ("horizon", "Horizon (months) for the short LGD"),
    ("horizon2", "Valuation horizon (months)"),
    ("lambda_override", "λ override (blank = fitted)"),
    ("gamma_override", "γ override (blank = fitted)"),
    ("mu_override", "Log-normal μ override (blank = fitted)"),
    ("sigma_override", "Log-normal σ override (blank = fitted)"),
    ("floor", "Hazard floor (per bucket)"),
    ("base_ts", "Base TermStep row for the derived LGD"),
    ("last_ts", "Last observed TermStep to use as-is (blank = last observed)"),
    ("vintage_start", "Vintages from (YYYY-MM; blank = all vintages)"),
    ("vintage_years", "Vintages within the last N years up to the report date (blank = all)"),
    ("report_date", "Report date (YYYY-MM; blank = the last month in the runoff data)"),
]

CONFIG_LABELS = [
    ("rate", "Discount rate used"),
    ("rate_implied", "Rate implied by the file"),
    ("v", "Monthly discount factor v"),
    ("min_exposure_abs", "MinExposure applied (R)"),
    ("opening_exposure", "TermStep 1 opening exposure (R)"),
    ("last_obs_file", "Last observed bucket in file"),
    ("fit_solver_label", "Fit solver"),
    ("lam_fit", "λ fitted"),
    ("gam_fit", "γ fitted"),
    ("lam", "λ used"),
    ("gam", "γ used"),
    ("mu_fit", "Log-normal μ fitted"),
    ("sigma_fit", "Log-normal σ fitted"),
    ("mu", "Log-normal μ used"),
    ("sigma", "Log-normal σ used"),
    ("logn_mode", "Log-normal mode (bucket of peak recovery)"),
    ("logn_median", "Log-normal median (bucket)"),
    ("half_life", "Half-life of the exponential tail (buckets)"),
    ("ref_last_cred", "Reference row last credible bucket"),
    ("fit_points", "Points in the decay regression"),
    ("last_ts", "LastTS applied"),
    ("method_label", "Selected method"),
    ("report_month", "Report date used"),
    ("vintage_start_effective", "Vintages from (effective; blank = all)"),
    ("vintage_first", "First vintage in the zip"),
    ("vintage_last", "Last vintage in the zip"),
    ("cohorts_included", "Cohorts included"),
    ("cohorts_total", "Cohorts in the zip"),
    ("exposure_share", "Share of at-default exposure included"),
    ("last_obs_unfiltered", "Last observed bucket with all vintages"),
]

AVERAGE_LABELS = [
    ("lgd_file", "Original LGD"),
    ("lgd_replica", "Replica LGD"),
    ("lgd_exp", "LGD exponential"),
    ("lgd_power", "LGD power"),
    ("lgd_logn", "LGD log-normal"),
    ("lgd_selected", "LGD SELECTED"),
    ("uplift", "Uplift (exposure-weighted)"),
    ("uplift_simple", "Uplift (simple average)"),
]


def headed(columns, cfg: dict | None):
    """The column definitions with the Original LGD heading adjusted for a vintage filter."""
    label = (cfg or {}).get("lgd_file_label")
    if not label:
        return columns
    return [(k, label if k == "lgd_file" else h, f) for k, h, f in columns]


def csv_table(payload: dict, table: str) -> str:
    """One result table as CSV text."""
    key, columns = TABLES[table]
    columns = headed(columns, payload.get("config"))
    data = payload[key]
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow([heading for _, heading, _ in columns])
    n = len(data[columns[0][0]])
    for i in range(n):
        w.writerow(["" if data[k][i] is None else data[k][i] for k, _, _ in columns])
    return out.getvalue()


def vintage_text(cfg: dict) -> str:
    """'All vintages' or the window a filtered run used, for headings and summaries."""
    if not cfg.get("vintage_filter"):
        return "All vintages"
    to = f" to {cfg['report_month']}" if cfg.get("report_month") else ""
    return (f"From {cfg['vintage_start_effective']}{to} ({cfg['cohorts_included']} of {cfg['cohorts_total']} cohorts, "
            f"{cfg['exposure_share']:.0%} of exposure)")


def vintage_note(cfg: dict) -> str:
    """The short form stored in a result's summary: '' for all vintages, else 'from 2016-08 (n of m cohorts)'."""
    if not cfg.get("vintage_filter"):
        return ""
    to = f" to {cfg['report_month']}" if cfg.get("report_month") else ""
    return f"from {cfg['vintage_start_effective']}{to} ({cfg['cohorts_included']} of {cfg['cohorts_total']} cohorts)"


def final_marginals(res, k_max: int) -> np.ndarray:
    """Marginal recovery in remaining step k = 1..k_max for TermStep 1..target, on the final basis:
    the row's own extended RecoveryPct up to LastTS, the rolled-forward base row beyond.
    Values are cash in bucket ts+k-1 as a share of the balance at ts; nan past MaxBucket."""
    cfg = res.config
    target, mb, last_ts, sel = int(cfg["target_ts"]), int(cfg["max_bucket"]), int(cfg["last_ts"]), int(cfg["method"]) - 1
    base_ts = int(res.params["base_ts"])
    width = res.base_curves.shape[1] - 1
    out = np.full((target, k_max), np.nan)
    c = res.base_curves[sel]
    c_cum = np.cumsum(c)
    for ts in range(1, target + 1):
        hi = min(mb, ts + k_max - 1)
        if hi < ts:
            continue
        n_k = hi - ts + 1
        if ts <= last_ts and ts <= res.n:
            row = res.ext[sel, ts, ts:hi + 1]
        else:
            pre = (c_cum[ts - 1] - c_cum[base_ts - 1]) if ts > base_ts else 0.0
            factor = 1.0 - pre
            if factor <= _EPS:
                continue
            seg = c[ts:min(hi, width) + 1] / factor
            row = np.full(n_k, np.nan)
            row[:len(seg)] = seg
        out[ts - 1, :n_k] = row[:n_k]
    return out
