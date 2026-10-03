"""Project summary workbook: every zip under every scenario, with the analytics a reviewer
needs without opening each result.

Sheets
  Summary              one row per zip and scenario: headline LGDs, horizon LGDs, decay parameters,
                       credibility, tie-out and file-floor diagnostics
  LGD_by_TS            long table: zip, scenario, TermStep, final LGD and its components
  LGD_final_wide       final LGD by TermStep, one column per zip and scenario (chart-ready)
  Scenario_deltas      each scenario against the first: exposure-weighted and by TermStep
  Cumulative_recovery  cumulative undiscounted recovery from TermStep 1 by remaining step, next to
                       the reference curve and the client's applied curve where present
  Exposure_credibility per zip and scenario by TermStep: exposure, last observed and credible
                       bucket, buckets added, file LGD floor gap
  Charts               final LGD by TermStep for every scenario, one chart per zip
  M_<zip>_<scenario>   marginal recoveries by TermStep for the remaining steps (final basis):
                       rows TermStep, columns remaining step 1..K, as % of the balance at that TermStep
"""
from __future__ import annotations

import math
import os
import re
import tempfile

import numpy as np
import xlsxwriter

from ..engine.core import ExtensionResult

NAVY = "#1F3A5F"
_EPS = 1e-9


def final_marginals(res: ExtensionResult, k_max: int) -> np.ndarray:
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


def _wavg(w: np.ndarray, x: np.ndarray) -> float:
    """Exposure-weighted mean over the TermSteps both arrays cover (the LGD table may stop
    before the last observed TermStep when the Target is lower)."""
    m = min(len(w), len(x))
    w = np.asarray(w, dtype=float)[:m]
    x = np.asarray(x, dtype=float)[:m]
    ok = np.isfinite(x) & np.isfinite(w)
    s = float(w[ok].sum())
    return float((w[ok] * x[ok]).sum() / s) if s > 0 else float("nan")


def _sheet_name(used: set, *parts: str) -> str:
    raw = "_".join(re.sub(r"[^A-Za-z0-9]+", "", p)[:12] for p in parts)
    name = raw[:31] or "sheet"
    i = 2
    while name in used:
        suffix = f"_{i}"
        name = raw[:31 - len(suffix)] + suffix
        i += 1
    used.add(name)
    return name


def build_summary_workbook(project_name: str, items: list[dict], applied: dict[str, np.ndarray],
                           k_max: int = 120) -> bytes:
    """items: one dict per computed result with keys
         zip, category, scenario, scenario_id, dataset_id, stale, computed_at, applied_label, res (ExtensionResult)
       applied: label -> face-basis monthly curve (the client's applied curves)."""
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    try:
        wb = xlsxwriter.Workbook(path, {"constant_memory": True, "nan_inf_to_errors": True,
                                        "strings_to_formulas": False, "strings_to_urls": False})
        f = {
            "title": wb.add_format({"bold": True, "font_size": 14, "font_color": NAVY}),
            "bold": wb.add_format({"bold": True}),
            "note": wb.add_format({"italic": True, "font_color": "#555555"}),
            "head": wb.add_format({"bold": True, "bg_color": NAVY, "font_color": "white", "text_wrap": True,
                                   "valign": "top", "border": 1}),
            "lgd": wb.add_format({"num_format": "0.0000"}),
            "lgd6": wb.add_format({"num_format": "0.000000"}),
            "pct": wb.add_format({"num_format": "0.00%"}),
            "money": wb.add_format({"num_format": "#,##0"}),
            "int": wb.add_format({"num_format": "0"}),
            "sci": wb.add_format({"num_format": "0.00E+00"}),
            "tri": wb.add_format({"num_format": "0.00000%"}),
        }
        items = sorted(items, key=lambda it: (it["zip"], it["scenario"]))
        scenarios = []
        for it in items:
            if it["scenario"] not in scenarios:
                scenarios.append(it["scenario"])
        zips = []
        for it in items:
            if it["zip"] not in zips:
                zips.append(it["zip"])

        def num(ws, r, c, v, fmt=None):
            if v is None:
                return
            if isinstance(v, (float, np.floating)):
                if not math.isfinite(v):
                    return
                ws.write_number(r, c, float(v), fmt)
            else:
                ws.write_number(r, c, float(v), fmt)

        # ============================================================== Summary
        ws = wb.add_worksheet("Summary")
        ws.write_string(0, 0, f"LGD tail extension – {project_name}", f["title"])
        ws.write_string(1, 0, "One row per zip and scenario. Weighted figures use the opening exposure at each TermStep. "
                              "Horizon LGDs count recoveries within 120 and 12 months of each TermStep.", f["note"])
        heads = ["Zip", "Category", "Scenario", "Out of date", "Method", "Reference curve", "Discount rate",
                 "Σ exposure at ts (weights, R)", "Observed TermSteps", "Target TermStep", "MaxBucket", "MinExposure applied (R)",
                 "Original LGD", "Replica LGD", "LGD exponential", "LGD power law", "LGD reference curve shape",
                 "LGD SELECTED", "Uplift (weighted)", "Uplift (simple)",
                 "LGD within 120 months (weighted, derived basis)", "LGD within 12 months (weighted, selected)",
                 "λ fitted", "γ fitted", "λ used", "γ used", "Half-life (months)", "Reference row last credible bucket",
                 "Regression points", "LastTS applied", "Tie-out (max abs)", "File LGD floored (TermSteps)",
                 "Max validation (own − derived)", "Balance factor at Target", "Warnings", "Computed at"]
        ws.set_row(3, 48)
        for c, h_ in enumerate(heads):
            ws.write_string(3, c, h_, f["head"])
        fmts = {6: f["pct"], 7: f["money"], 11: f["money"], 30: f["sci"]}
        for i, it in enumerate(items):
            res: ExtensionResult = it["res"]
            cfg, avg, R, L = res.config, res.averages, res.results, res.lgd_ts
            w = R["exposure"]
            n = res.n
            row = [it["zip"], it["category"], it["scenario"], "yes" if it["stale"] else "", cfg["method_label"],
                   cfg["client_cohort"] or "", cfg["rate"], avg["exposure_total"], n, cfg["target_ts"], cfg["max_bucket"],
                   cfg["min_exposure_abs"], avg["lgd_file"], avg["lgd_replica"], avg["lgd_exp"], avg["lgd_power"],
                   avg["lgd_client"], avg["lgd_selected"], avg["uplift"], avg["uplift_simple"],
                   _wavg(w, L["lgd_valuation_horizon"][:n]), _wavg(w, R["lgd_horizon"]),
                   cfg["lam_fit"], cfg["gam_fit"], cfg["lam"], cfg["gam"], cfg["half_life"], cfg["ref_last_cred"],
                   cfg["fit_points"], cfg["last_ts"], max(abs(avg["tie_max"]), abs(avg["tie_min"])),
                   avg.get("file_floored_count", 0), res.lgd_ts_summary["validation_max"],
                   res.lgd_ts_summary["balance_factor_target"], len(res.warnings), it["computed_at"]]
            r = 4 + i
            for c, v in enumerate(row):
                if isinstance(v, str):
                    ws.write_string(r, c, v)
                else:
                    num(ws, r, c, v, fmts.get(c, f["lgd"] if 12 <= c <= 21 or 22 <= c <= 26 or 32 <= c <= 33 else f["int"]))
        ws.set_column(0, 2, 18)
        ws.set_column(3, 35, 13)
        ws.freeze_panes(4, 3)

        # ============================================================ LGD_by_TS
        ws = wb.add_worksheet("LGD_by_TS")
        heads = ["Zip", "Scenario", "TermStep", "Source", "LGD final", "Original LGD (file)", "Own-row LGD",
                 "Derived – exponential", "Derived – power law", "Derived – reference curve shape",
                 "Validation (own − derived)", "Balance factor", "LGD within 120 months", "LGD within 12 months",
                 "Undiscounted remaining recovery", "Exposure at ts (R)"]
        ws.set_row(0, 40)
        for c, h_ in enumerate(heads):
            ws.write_string(0, c, h_, f["head"])
        r = 1
        for it in items:
            L = it["res"].lgd_ts
            T = len(L["ts"])
            for i in range(T):
                ws.write_string(r, 0, it["zip"])
                ws.write_string(r, 1, it["scenario"])
                ws.write_number(r, 2, int(L["ts"][i]))
                ws.write_string(r, 3, str(L["source"][i]))
                for c, key in enumerate(["lgd_final", "lgd_file", "lgd_own", "derived_exp", "derived_power",
                                         "derived_client", "validation", "balance_factor", "lgd_valuation_horizon",
                                         "lgd_short_horizon", "undisc_remaining"], start=4):
                    num(ws, r, c, L[key][i], f["lgd"])
                num(ws, r, 15, L["exposure"][i], f["money"])
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
                    num(ws, 2 + i, c, L["lgd_final"][i], f["lgd"])
        ws.set_column(0, 0, 10)
        ws.set_column(1, len(items), 16)
        ws.freeze_panes(2, 1)
        wide_rows = T_max

        # ======================================================= Scenario_deltas
        ws = wb.add_worksheet("Scenario_deltas")
        base_name = scenarios[0] if scenarios else ""
        ws.write_string(0, 0, f"Each scenario against \"{base_name}\" (the first scenario). Negative = lower LGD than the base scenario.", f["note"])
        heads = ["Zip", "Scenario", "Base scenario", "LGD selected (weighted)", "Base LGD selected (weighted)", "Delta (weighted)",
                 "LGD within 120 months (weighted)", "Base within 120 months", "Delta within 120 months",
                 "Max |delta| by TermStep", "TermStep of max |delta|"]
        ws.set_row(2, 40)
        for c, h_ in enumerate(heads):
            ws.write_string(2, c, h_, f["head"])
        by_key = {(it["zip"], it["scenario"]): it for it in items}
        r = 3
        delta_series = []
        for it in items:
            if it["scenario"] == base_name:
                continue
            base = by_key.get((it["zip"], base_name))
            if base is None:
                continue
            a, b = it["res"], base["res"]
            n = min(len(a.lgd_ts["ts"]), len(b.lgd_ts["ts"]))
            d = np.asarray(a.lgd_ts["lgd_final"][:n]) - np.asarray(b.lgd_ts["lgd_final"][:n])
            k = int(np.nanargmax(np.abs(d))) if n and np.isfinite(d).any() else 0
            h120a = _wavg(a.results["exposure"], a.lgd_ts["lgd_valuation_horizon"][:a.n])
            h120b = _wavg(b.results["exposure"], b.lgd_ts["lgd_valuation_horizon"][:b.n])
            row = [it["zip"], it["scenario"], base_name, a.averages["lgd_selected"], b.averages["lgd_selected"],
                   a.averages["lgd_selected"] - b.averages["lgd_selected"], h120a, h120b, h120a - h120b,
                   float(d[k]) if n else None, k + 1 if n else None]
            for c, v in enumerate(row):
                if isinstance(v, str):
                    ws.write_string(r, c, v)
                else:
                    num(ws, r, c, v, f["int"] if c == 10 else f["lgd"])
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
                    num(ws, r + 1 + i, c, d[i], f["lgd"])
        ws.set_column(0, 2, 18)
        ws.set_column(3, max(10, len(delta_series)), 16)

        # ==================================================== Cumulative_recovery
        ws = wb.add_worksheet("Cumulative_recovery")
        ws.write_string(0, 0, "Cumulative undiscounted recovery from TermStep 1 by remaining step, as a share of the balance at "
                              "default: the selected extended row of each zip and scenario, the reference curve as supplied, and "
                              "the client's applied curve (face basis) where one exists.", f["note"])
        cols = []
        for it in items:
            res = it["res"]
            sel = int(res.config["method"]) - 1
            mb = int(res.config["max_bucket"])
            cols.append((f"{it['zip']} | {it['scenario']}", np.cumsum(res.ext[sel, 1, 1:mb + 1])))
        seen = set()
        for it in items:
            res = it["res"]
            label = res.config["client_cohort"]
            if label and ("ref", label) not in seen:
                seen.add(("ref", label))
                cols.append((f"Reference curve {label}", np.cumsum(res.shapes[2, 1:int(res.config['max_bucket']) + 1])))
            ap = it.get("applied_label")
            if ap and ap in applied and ("app", ap) not in seen:
                seen.add(("app", ap))
                cols.append((f"Client applied {ap}", np.cumsum(applied[ap])))
        ws.write_string(1, 0, "Remaining step", f["head"])
        for c, (name, _) in enumerate(cols, start=1):
            ws.write_string(1, c, name, f["head"])
        ws.set_row(1, 40)
        K = max((len(v) for _, v in cols), default=0)
        for i in range(K):
            ws.write_number(2 + i, 0, i + 1)
            for c, (_, v) in enumerate(cols, start=1):
                if i < len(v):
                    num(ws, 2 + i, c, v[i], f["lgd"])
        ws.set_column(0, 0, 14)
        ws.set_column(1, max(1, len(cols)), 18)
        ws.freeze_panes(2, 1)

        # =================================================== Exposure_credibility
        ws = wb.add_worksheet("Exposure_credibility")
        heads = ["Zip", "Scenario", "TermStep", "Exposure at ts (R)", "Last observed bucket", "Last credible bucket",
                 "Buckets in window", "Buckets added", "Original LGD (file)", "Replica LGD",
                 "File LGD floor gap", "Tie-out"]
        ws.set_row(0, 40)
        for c, h_ in enumerate(heads):
            ws.write_string(0, c, h_, f["head"])
        r = 1
        for it in items:
            res = it["res"]
            R, Tf = res.results, res.tail_fit
            for i in range(res.n):
                ws.write_string(r, 0, it["zip"])
                ws.write_string(r, 1, it["scenario"])
                ws.write_number(r, 2, int(R["ts"][i]))
                num(ws, r, 3, R["exposure"][i], f["money"])
                num(ws, r, 4, Tf["last_obs"][i], f["int"])
                num(ws, r, 5, R["last_cred"][i], f["int"])
                num(ws, r, 6, Tf["n_win"][i], f["int"])
                num(ws, r, 7, R["buckets_added"][i], f["int"])
                num(ws, r, 8, R["lgd_file"][i], f["lgd"])
                num(ws, r, 9, R["lgd_replica"][i], f["lgd"])
                num(ws, r, 10, R["file_floor_gap"][i], f["lgd6"])
                num(ws, r, 11, R["tie_out"][i], f["sci"])
                r += 1
        ws.set_column(0, 1, 18)
        ws.set_column(2, 11, 14)
        ws.freeze_panes(1, 3)
        ws.autofilter(0, 0, max(r - 1, 1), len(heads) - 1)

        # =============================================================== Charts
        wsc = wb.add_worksheet("Charts")
        wsc.write_string(0, 0, "Final LGD by TermStep for every scenario, one chart per zip (data on LGD_final_wide)", f["bold"])
        colours = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
        for zi, z in enumerate(zips):
            ch = wb.add_chart({"type": "scatter", "subtype": "straight"})
            k = 0
            for c, it in enumerate(items, start=1):
                if it["zip"] != z:
                    continue
                ch.add_series({"name": it["scenario"], "categories": ["LGD_final_wide", 2, 0, 1 + wide_rows, 0],
                               "values": ["LGD_final_wide", 2, c, 1 + wide_rows, c], "marker": {"type": "none"},
                               "line": {"width": 1.75, "color": colours[k % len(colours)]}})
                k += 1
            ch.set_title({"name": f"{z}: final LGD by TermStep"})
            ch.set_x_axis({"name": "TermStep", "min": 0})
            ch.set_y_axis({"name": "LGD", "num_format": "0.00"})
            ch.set_size({"width": 640, "height": 340})
            wsc.insert_chart(2 + (zi // 2) * 18, (zi % 2) * 10, ch)

        # ======================================================= marginal sheets
        used: set = set()
        for it in items:
            res = it["res"]
            name = _sheet_name(used, "M", it["zip"], it["scenario"])
            ws = wb.add_worksheet(name)
            M = final_marginals(res, k_max)
            L = res.lgd_ts
            ws.write_string(0, 0, f"{it['zip']} | {it['scenario']}: marginal recoveries by TermStep for the remaining steps "
                                  f"(final basis: own extended row to LastTS {res.config['last_ts']}, base row rolled forward "
                                  f"beyond). Cash in remaining step k as % of the balance at the TermStep; blank past "
                                  f"MaxBucket {res.config['max_bucket']}.", f["note"])
            ws.write_string(1, 0, "TermStep", f["head"])
            ws.write_string(1, 1, "Source", f["head"])
            ws.write_string(1, 2, "LGD final", f["head"])
            ws.write_string(1, 3, f"Σ steps 1–{k_max}", f["head"])
            for k in range(k_max):
                ws.write_number(1, 4 + k, k + 1, f["head"])
            for i in range(M.shape[0]):
                ws.write_number(2 + i, 0, i + 1)
                ws.write_string(2 + i, 1, str(L["source"][i]))
                num(ws, 2 + i, 2, L["lgd_final"][i], f["lgd"])
                row = M[i]
                if np.isfinite(row).any():
                    num(ws, 2 + i, 3, float(np.nansum(row)), f["lgd"])
                    ws.write_row(2 + i, 4, [x if math.isfinite(x) else "" for x in row.tolist()], f["tri"])
            ws.set_column(0, 0, 9)
            ws.set_column(1, 1, 9)
            ws.set_column(2, 3, 11)
            ws.set_column(4, 3 + k_max, 9)
            ws.freeze_panes(2, 4)

        wb.close()
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
