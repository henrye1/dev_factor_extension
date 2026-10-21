"""Project summary workbook: every zip under every scenario, with the analytics a reviewer
needs without opening each result.

Sheets
  Summary              one row per zip and scenario: headline LGDs, horizon LGDs, decay parameters,
                       credibility, tie-out and file-floor diagnostics
  LGD_by_TS            long table: zip, scenario, TermStep, final LGD and its components
  LGD_final_wide       final LGD by TermStep, one column per zip and scenario (chart-ready)
  Scenario_deltas      each scenario against the first: exposure-weighted and by TermStep
  Cumulative_recovery  cumulative undiscounted recovery from TermStep 1 by remaining step, next to
                       the client's applied curve where present
  Exposure_credibility per zip and scenario by TermStep: exposure, last observed and credible
                       bucket, buckets added, file LGD floor gap
  Charts               final LGD by TermStep for every scenario, one chart per zip
  M_<zip>_<scenario>   marginal recoveries by TermStep for the remaining steps (final basis):
                       rows TermStep, columns remaining step 1..K, as % of the balance at that TermStep
"""
from __future__ import annotations

import re

import numpy as np

from ..engine.core import ExtensionResult
from ._xlsx import (build_xlsx, formats, marginals_title, unique_sheet_name, write_cells, write_headings,
                    write_marginals_sheet, write_num)
from .tables import final_marginals, vintage_text

__all__ = ["build_summary_workbook", "final_marginals"]     # final_marginals kept here for older imports

SERIES_COLOURS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]

SUMMARY_HEADINGS = [
    "Zip", "Category", "Scenario", "Out of date", "Method", "Vintages", "Discount rate",
    "Σ exposure at ts (weights, R)", "Observed TermSteps", "Target TermStep", "MaxBucket", "MinExposure applied (R)",
    "Original LGD", "Replica LGD", "LGD exponential", "LGD power law", "LGD log-normal",
    "LGD SELECTED", "Uplift (weighted)", "Uplift (simple)",
    "LGD within 120 months (weighted, derived basis)", "LGD within 12 months (weighted, selected)",
    "λ fitted", "γ fitted", "λ used", "γ used", "Half-life (months)", "Reference row last credible bucket",
    "Regression points", "LastTS applied", "Tie-out (max abs)", "File LGD floored (TermSteps)",
    "Max validation (own − derived)", "Balance factor at Target", "Warnings", "Computed at",
    "Log-normal μ fitted", "Log-normal σ fitted", "Log-normal μ used", "Log-normal σ used",
    "Cohorts included", "Cohorts in zip", "Share of at-default exposure"]
# Summary columns shown to four decimals; the rest are whole numbers unless named in _SUMMARY_FMT
_SUMMARY_LGD_COLS = set(range(12, 27)) | {32, 33} | set(range(36, 40))
_SUMMARY_FMT = {6: "pct", 7: "money", 11: "money", 30: "sci", 42: "pct"}


def _wavg(w: np.ndarray, x: np.ndarray) -> float:
    """Exposure-weighted mean over the TermSteps both arrays cover (the LGD table may stop
    before the last observed TermStep when the Target is lower), ignoring blanks."""
    m = min(len(w), len(x))
    w = np.asarray(w, dtype=float)[:m]
    x = np.asarray(x, dtype=float)[:m]
    ok = np.isfinite(x) & np.isfinite(w)
    s = float(w[ok].sum())
    return float((w[ok] * x[ok]).sum() / s) if s > 0 else float("nan")


def _horizon_avg(res: ExtensionResult) -> float:
    """Exposure-weighted LGD within the valuation horizon over the observed TermSteps."""
    return _wavg(res.results["exposure"], res.lgd_ts["lgd_valuation_horizon"][:res.n])


def _sheet_name(used: set, *parts: str) -> str:
    return unique_sheet_name(used, "_".join(re.sub(r"[^A-Za-z0-9]+", "", p)[:12] for p in parts))


def build_summary_workbook(project_name: str, items: list[dict], applied: dict[str, np.ndarray],
                           k_max: int = 120) -> bytes:
    """items: one dict per computed result with keys
         zip, category, scenario, scenario_id, dataset_id, stale, computed_at, applied_label, res (ExtensionResult)
       applied: label -> face-basis monthly curve (the client's applied curves)."""
    items = sorted(items, key=lambda it: (it["zip"], it["scenario"]))
    return build_xlsx(lambda wb: _fill(wb, project_name, items, applied, k_max))


def _fill(wb, project_name, items, applied, k_max) -> None:
    f = formats(wb)
    scenarios = list(dict.fromkeys(it["scenario"] for it in items))
    zips = list(dict.fromkeys(it["zip"] for it in items))

    # ============================================================== Summary
    ws = wb.add_worksheet("Summary")
    ws.write_string(0, 0, f"LGD tail extension – {project_name}", f["title"])
    ws.write_string(1, 0, "One row per zip and scenario. Weighted figures use the opening exposure at each TermStep. "
                          "Horizon LGDs count recoveries within 120 and 12 months of each TermStep.", f["note"])
    write_headings(ws, 3, SUMMARY_HEADINGS, f["head"], 48)

    def summary_fmt(c):
        return f[_SUMMARY_FMT[c]] if c in _SUMMARY_FMT else f["lgd"] if c in _SUMMARY_LGD_COLS else f["int"]

    for i, it in enumerate(items):
        res: ExtensionResult = it["res"]
        cfg, avg, R = res.config, res.averages, res.results
        row = [it["zip"], it["category"], it["scenario"], "yes" if it["stale"] else "", cfg["method_label"],
               vintage_text(cfg), cfg["rate"], avg["exposure_total"], res.n, cfg["target_ts"], cfg["max_bucket"],
               cfg["min_exposure_abs"], avg["lgd_file"], avg["lgd_replica"], avg["lgd_exp"], avg["lgd_power"],
               avg["lgd_logn"], avg["lgd_selected"], avg["uplift"], avg["uplift_simple"],
               _horizon_avg(res), _wavg(R["exposure"], R["lgd_horizon"]),
               cfg["lam_fit"], cfg["gam_fit"], cfg["lam"], cfg["gam"], cfg["half_life"], cfg["ref_last_cred"],
               cfg["fit_points"], cfg["last_ts"], max(abs(avg["tie_max"]), abs(avg["tie_min"])),
               avg.get("file_floored_count", 0), res.lgd_ts_summary["validation_max"],
               res.lgd_ts_summary["balance_factor_target"], len(res.warnings), it["computed_at"],
               cfg["mu_fit"], cfg["sigma_fit"], cfg["mu"], cfg["sigma"],
               cfg["cohorts_included"], cfg["cohorts_total"], cfg["exposure_share"]]
        write_cells(ws, 4 + i, row, summary_fmt)
    ws.set_column(0, 2, 18)
    ws.set_column(3, 42, 13)
    ws.freeze_panes(4, 3)

    # ============================================================ LGD_by_TS
    ws = wb.add_worksheet("LGD_by_TS")
    heads = ["Zip", "Scenario", "TermStep", "Source", "LGD final", "Original LGD (file)", "Own-row LGD",
             "Derived – exponential", "Derived – power law", "Derived – log-normal",
             "Validation (own − derived)", "Balance factor", "LGD within 120 months", "LGD within 12 months",
             "Undiscounted remaining recovery", "Exposure at ts (R)"]
    keys = ["lgd_final", "lgd_file", "lgd_own", "derived_exp", "derived_power", "derived_logn", "validation",
            "balance_factor", "lgd_valuation_horizon", "lgd_short_horizon", "undisc_remaining"]
    write_headings(ws, 0, heads, f["head"], 40)
    r = 1
    for it in items:
        L = it["res"].lgd_ts
        for i in range(len(L["ts"])):
            ws.write_string(r, 0, it["zip"])
            ws.write_string(r, 1, it["scenario"])
            ws.write_number(r, 2, int(L["ts"][i]))
            ws.write_string(r, 3, str(L["source"][i]))
            for c, key in enumerate(keys, start=4):
                write_num(ws, r, c, L[key][i], f["lgd"])
            write_num(ws, r, 15, L["exposure"][i], f["money"])
            r += 1
    ws.set_column(0, 1, 18)
    ws.set_column(2, 15, 14)
    ws.freeze_panes(1, 3)
    ws.autofilter(0, 0, max(r - 1, 1), len(heads) - 1)

    # ======================================================= LGD_final_wide
    ws = wb.add_worksheet("LGD_final_wide")
    ws.write_string(0, 0, "Final LGD by TermStep. One column per zip and scenario.", f["note"])
    ws.write_string(1, 0, "TermStep", f["head"])
    for c, it in enumerate(items, start=1):
        ws.write_string(1, c, f"{it['zip']} | {it['scenario']}", f["head"])
    T_max = max(len(it["res"].lgd_ts["ts"]) for it in items)
    ws.set_row(1, 40)
    for i in range(T_max):
        ws.write_number(2 + i, 0, i + 1)
        for c, it in enumerate(items, start=1):
            L = it["res"].lgd_ts
            if i < len(L["ts"]):
                write_num(ws, 2 + i, c, L["lgd_final"][i], f["lgd"])
    ws.set_column(0, 0, 10)
    ws.set_column(1, len(items), 16)
    ws.freeze_panes(2, 1)

    # ======================================================= Scenario_deltas
    ws = wb.add_worksheet("Scenario_deltas")
    base_name = scenarios[0] if scenarios else ""
    ws.write_string(0, 0, f"Each scenario against \"{base_name}\" (the first scenario). Negative = lower LGD than the "
                          f"base scenario.", f["note"])
    heads = ["Zip", "Scenario", "Base scenario", "LGD selected (weighted)", "Base LGD selected (weighted)",
             "Delta (weighted)", "LGD within 120 months (weighted)", "Base within 120 months",
             "Delta within 120 months", "Max |delta| by TermStep", "TermStep of max |delta|"]
    write_headings(ws, 2, heads, f["head"], 40)
    by_key = {(it["zip"], it["scenario"]): it for it in items}
    r = 3
    delta_series = []
    for it in items:
        base = by_key.get((it["zip"], base_name))
        if it["scenario"] == base_name or base is None:
            continue
        a, b = it["res"], base["res"]
        n = min(len(a.lgd_ts["ts"]), len(b.lgd_ts["ts"]))
        d = np.asarray(a.lgd_ts["lgd_final"][:n]) - np.asarray(b.lgd_ts["lgd_final"][:n])
        k = int(np.nanargmax(np.abs(d))) if n and np.isfinite(d).any() else 0
        h120a, h120b = _horizon_avg(a), _horizon_avg(b)
        row = [it["zip"], it["scenario"], base_name, a.averages["lgd_selected"], b.averages["lgd_selected"],
               a.averages["lgd_selected"] - b.averages["lgd_selected"], h120a, h120b, h120a - h120b,
               float(d[k]) if n else None, k + 1 if n else None]
        write_cells(ws, r, row, lambda c: f["int"] if c == 10 else f["lgd"])
        delta_series.append((f"{it['zip']} | {it['scenario']} − {base_name}", d))
        r += 1
    r += 2
    ws.write_string(r, 0, "Delta in final LGD by TermStep", f["bold"])
    r += 1
    ws.write_string(r, 0, "TermStep", f["head"])
    for c, (name, _) in enumerate(delta_series, start=1):
        ws.write_string(r, c, name, f["head"])
    ws.set_row(r, 40)
    for i in range(T_max):
        ws.write_number(r + 1 + i, 0, i + 1)
        for c, (_, d) in enumerate(delta_series, start=1):
            if i < len(d):
                write_num(ws, r + 1 + i, c, d[i], f["lgd"])
    ws.set_column(0, 2, 18)
    ws.set_column(3, max(10, len(delta_series)), 16)

    # ==================================================== Cumulative_recovery
    ws = wb.add_worksheet("Cumulative_recovery")
    ws.write_string(0, 0, "Cumulative undiscounted recovery from TermStep 1 by remaining step, as a share of the balance at "
                          "default: the selected extended row of each zip and scenario, and the client's applied curve "
                          "(face basis) where one exists.", f["note"])
    cols = []
    for it in items:
        res = it["res"]
        sel = int(res.config["method"]) - 1
        mb = int(res.config["max_bucket"])
        cols.append((f"{it['zip']} | {it['scenario']}", np.cumsum(res.ext[sel, 1, 1:mb + 1])))
    for label in dict.fromkeys(it.get("applied_label") for it in items):
        if label and label in applied:
            cols.append((f"Client applied {label}", np.cumsum(applied[label])))
    ws.write_string(1, 0, "Remaining step", f["head"])
    for c, (name, _) in enumerate(cols, start=1):
        ws.write_string(1, c, name, f["head"])
    ws.set_row(1, 40)
    K = max((len(v) for _, v in cols), default=0)
    for i in range(K):
        ws.write_number(2 + i, 0, i + 1)
        for c, (_, v) in enumerate(cols, start=1):
            if i < len(v):
                write_num(ws, 2 + i, c, v[i], f["lgd"])
    ws.set_column(0, 0, 14)
    ws.set_column(1, max(1, len(cols)), 18)
    ws.freeze_panes(2, 1)

    # =================================================== Exposure_credibility
    ws = wb.add_worksheet("Exposure_credibility")
    heads = ["Zip", "Scenario", "TermStep", "Exposure at ts (R)", "Last observed bucket", "Last credible bucket",
             "Buckets in window", "Buckets added", "Original LGD (file)", "Replica LGD",
             "File LGD floor gap", "Tie-out"]
    # (table, key, format): R = Results, T = Tail_Fit
    cols = [("R", "exposure", "money"), ("T", "last_obs", "int"), ("R", "last_cred", "int"), ("T", "n_win", "int"),
            ("R", "buckets_added", "int"), ("R", "lgd_file", "lgd"), ("R", "lgd_replica", "lgd"),
            ("R", "file_floor_gap", "lgd6"), ("R", "tie_out", "sci")]
    write_headings(ws, 0, heads, f["head"], 40)
    r = 1
    for it in items:
        res = it["res"]
        tables = {"R": res.results, "T": res.tail_fit}
        for i in range(res.n):
            ws.write_string(r, 0, it["zip"])
            ws.write_string(r, 1, it["scenario"])
            ws.write_number(r, 2, int(res.results["ts"][i]))
            for c, (tab, key, fmt) in enumerate(cols, start=3):
                write_num(ws, r, c, tables[tab][key][i], f[fmt])
            r += 1
    ws.set_column(0, 1, 18)
    ws.set_column(2, 11, 14)
    ws.freeze_panes(1, 3)
    ws.autofilter(0, 0, max(r - 1, 1), len(heads) - 1)

    # =============================================================== Charts
    wsc = wb.add_worksheet("Charts")
    wsc.write_string(0, 0, "Final LGD by TermStep for every scenario, one chart per zip (data on LGD_final_wide)", f["bold"])
    for zi, z in enumerate(zips):
        ch = wb.add_chart({"type": "scatter", "subtype": "straight"})
        k = 0
        for c, it in enumerate(items, start=1):
            if it["zip"] != z:
                continue
            ch.add_series({"name": it["scenario"], "categories": ["LGD_final_wide", 2, 0, 1 + T_max, 0],
                           "values": ["LGD_final_wide", 2, c, 1 + T_max, c], "marker": {"type": "none"},
                           "line": {"width": 1.75, "color": SERIES_COLOURS[k % len(SERIES_COLOURS)]}})
            k += 1
        ch.set_title({"name": f"{z}: final LGD by TermStep"})
        ch.set_x_axis({"name": "TermStep", "min": 0})
        ch.set_y_axis({"name": "LGD", "num_format": "0.00"})
        ch.set_size({"width": 640, "height": 340})
        wsc.insert_chart(2 + (zi // 2) * 18, (zi % 2) * 10, ch)

    # ======================================================= marginal sheets
    used: set = set()
    for it in items:
        ws = wb.add_worksheet(_sheet_name(used, "M", it["zip"], it["scenario"]))
        write_marginals_sheet(ws, f, marginals_title(f"{it['zip']} | {it['scenario']}", it["res"]), it["res"], k_max)
