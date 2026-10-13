"""Formula-driven Excel workbook for one zip under one scenario.

Same sheets, defined names and formulas as the hand-built framework workbooks
(README, Config, Results, LGD_300, Charts, Hazard_Obs, Tail_Fit, Ext_Exp, Ext_Power,
Ext_LogN, Raw_Debug), generalised to the zip's own number of TermSteps, the scenario's
MaxBucket and its Target TermStep. The log-normal shape is fitted on Tail_Fit with SLOPE and
INTERCEPT helper rows (the two-stage form of the quadratic least-squares fit), so no array
formulas are needed. With a vintage filter, Raw_Debug holds the rows rebuilt from
runoff_triangle.csv for the chosen vintages instead of the file's rows.

Two deliberate differences from the hand-built workbooks:

* Hazard_Obs reads Raw_Debug through a small Raw_Index sheet (first row and row count per
  TermStep) and INDEX, instead of one three-condition SUMIFS per cell. On the larger zips
  SUMIFS would mean about 200,000 scans of 55,000 rows on every recalculation.
* Results carries two extra columns (R, S) showing the file LGD before the risk suite's
  monotone floor; the tie-out in column E is measured against that value.

Every formula cell is written with the engine's value as its cached result, so the workbook
opens populated; Excel recalculates it on load.
"""
from __future__ import annotations

import math
import os
import tempfile

import numpy as np
import xlsxwriter
from xlsxwriter.utility import xl_col_to_name as CN

from ..engine.core import ExtensionResult
from ..engine.params import Params
from ..engine.parse import COLUMNS, RecoveryData, month_int
from .summary_xlsx import vintage_text

TR = 19                       # Tail_Fit: the per-TermStep table starts on row TR + 1 (header on row TR)

NAVY = "#1F3A5F"
_EPS_TXT = "0.000000001"


def _v(x):
    """Cached value for a formula cell: blank when the engine has no number."""
    if x is None:
        return ""
    if isinstance(x, (float, np.floating)):
        return float(x) if math.isfinite(x) else ""
    if isinstance(x, (np.integer,)):
        return int(x)
    return x


def _logn_helper_rows(res: ExtensionResult, p: Params, n: int, wd: int) -> dict[int, np.ndarray]:
    """Cached values of the Tail_Fit helper rows 12-17 (the log-normal fit), by row number."""
    ref_row = res.R[p.ref_ts]
    b = np.arange(1, wd + 1, dtype=float)
    r = np.full(wd, np.nan)
    r[:n] = ref_row[1:n + 1]
    x = np.log(b)
    ok = r > 0
    with np.errstate(invalid="ignore", divide="ignore"):
        y = np.where(ok, np.log(np.where(ok, r, 1.0)) + x, np.nan)
    x2 = np.where(ok, x * x, np.nan)
    lo, hi = sorted((int(p.fit_start), int(res.config["ref_last_cred"])))
    lo, hi = max(lo, 1), min(hi, wd)
    win = np.zeros(wd, dtype=bool)
    if hi >= lo:
        win[lo - 1:hi] = True
    sel = win & ok

    def fit(yv, xv):
        if sel.sum() < 2:
            return float("nan"), float("nan")
        xs, ys = xv[sel], yv[sel]
        dx = xs - xs.mean()
        den = float((dx * dx).sum())
        slope = float((dx * (ys - ys.mean())).sum() / den) if den else float("nan")
        return slope, float(ys.mean() - slope * xs.mean())

    sy, iy = fit(y, x)
    sx2, ix2 = fit(x2, x)
    ry = np.where(ok, y - (iy + sy * x), np.nan)
    rx2 = np.where(ok, x2 - (ix2 + sx2 * x), np.nan)
    s16 = np.where(ok, y + x2 / (2.0 * p.sigma_override ** 2), np.nan) if p.sigma_override is not None else np.full(wd, np.nan)
    s17 = np.where(ok, x2 - 2.0 * p.mu_override * x, np.nan) if p.mu_override is not None else np.full(wd, np.nan)
    return {12: y, 13: x2, 14: ry, 15: rx2, 16: s16, 17: s17}


def build_formula_workbook(res: ExtensionResult, data: RecoveryData, dataset_name: str,
                           scenario_name: str) -> bytes:
    p = Params(**res.params)
    if not data.is_contiguous(p.event_type):
        raise ValueError("The formula workbook needs each TermStep's buckets in ascending order "
                         "without gaps; this zip does not have that layout. Use the values export")
    filtered = bool(res.config.get("vintage_filter"))
    v_start = month_int(res.config["vintage_start_effective"]) if filtered else None

    n = res.n
    wd = int(res.shape_stats["tri_width"])          # bucket columns in the extended triangles
    T = int(res.config["target_ts"])
    cfg, st = res.config, res.shape_stats

    # ---- Raw_Debug rows -------------------------------------------------------------------
    if filtered:
        # the rows rebuilt from runoff_triangle.csv for the chosen vintages (one EventType)
        blocks = [(p.event_type, data.rebuilt_block(p.event_type, v_start))]
    elif data.identical_events:
        blocks = [(p.event_type, data.block(p.event_type))]
    else:
        blocks = [(ev, data.block(ev)) for ev in data.event_types]
    n_raw = sum(len(b) for _, b in blocks)
    rlast = 3 + n_raw
    offset = 0
    for ev, blk in blocks:
        if ev == p.event_type:
            sel_block, sel_offset = blk, offset
        offset += len(blk)
    ts_col = sel_block[:, 0].astype(int)
    first_pos = np.zeros(n + 2, dtype=int)
    count = np.zeros(n + 2, dtype=int)
    uniq, idx, cnt = np.unique(ts_col, return_index=True, return_counts=True)
    first_pos[uniq] = idx + sel_offset + 1          # 1-based position inside Raw_Debug!4:N
    count[uniq] = cnt

    def RD(col: str) -> str:
        return f"Raw_Debug!${col}$4:${col}${rlast}"

    # ---- column / row helpers -------------------------------------------------------------
    def bc(b: int) -> str:                 # bucket column on Hazard_Obs, Tail_Fit, Ext_*
        return CN(b + 1)

    def lc(b: int) -> str:                 # bucket column on LGD_300
        return CN(b + 2)

    nc, wc, lw = bc(n), bc(wd), lc(wd)
    e_hdr = n + 11                         # header row of the ExposureBucket block on Hazard_Obs
    PH, PI, PJ, PK, PL, PM, PN, PO = (CN(wd + 3 + i) for i in range(8))
    res_last = 9 + n
    ext_last = 5 + n
    tail_last = TR + n
    lgd_last = 12 + T
    helper = _logn_helper_rows(res, p, n, wd)

    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    try:
        # plain write() never turns text into a formula or link; formulas go through write_formula
        wb = xlsxwriter.Workbook(path, {"constant_memory": True, "nan_inf_to_errors": True,
                                        "strings_to_formulas": False, "strings_to_urls": False})
        f = {
            "title": wb.add_format({"bold": True, "font_size": 14, "font_color": NAVY}),
            "bold": wb.add_format({"bold": True}),
            "note": wb.add_format({"italic": True, "font_color": "#555555"}),
            "wrap": wb.add_format({"text_wrap": True, "valign": "top"}),
            "head": wb.add_format({"bold": True, "bg_color": NAVY, "font_color": "white",
                                   "text_wrap": True, "valign": "top", "border": 1}),
            "input": wb.add_format({"font_color": "#0000FF", "bg_color": "#DDEBF7", "border": 1}),
            "input_money": wb.add_format({"font_color": "#0000FF", "bg_color": "#DDEBF7", "border": 1,
                                          "num_format": "#,##0"}),
            "input_pct": wb.add_format({"font_color": "#0000FF", "bg_color": "#DDEBF7", "border": 1,
                                        "num_format": "0.00%"}),
            "lgd": wb.add_format({"num_format": "0.0000"}),
            "lgd_b": wb.add_format({"num_format": "0.0000", "bold": True}),
            "num": wb.add_format({"num_format": "0.000000"}),
            "sci": wb.add_format({"num_format": "0.00E+00"}),
            "money": wb.add_format({"num_format": "#,##0"}),
            "tri": wb.add_format({"num_format": "0.00000%;-0.00000%;"}),
            "tri_money": wb.add_format({"num_format": "#,##0;-#,##0;"}),
        }
        # Sheets are created in display order. constant_memory needs each sheet written
        # top to bottom, which every block below respects.
        ws_readme = wb.add_worksheet("README")
        ws_cfg = wb.add_worksheet("Config")
        ws_res = wb.add_worksheet("Results")
        ws_lgd = wb.add_worksheet("LGD_300")
        ws_ch = wb.add_worksheet("Charts")
        ws_obs = wb.add_worksheet("Hazard_Obs")
        ws_tail = wb.add_worksheet("Tail_Fit")
        ws_ext = [wb.add_worksheet(nm) for nm in ("Ext_Exp", "Ext_Power", "Ext_LogN")]
        ws_idx = wb.add_worksheet("Raw_Index")
        ws_raw = wb.add_worksheet("Raw_Debug")

        for name, ref in {
            "EventType": "$B$4", "Rate": "$B$5", "MaxBucket": "$B$6", "MinExp": "$B$7",
            "WinW": "$B$8", "FitStart": "$B$9", "RefTS": "$B$10", "Method": "$B$11",
            "MuOvr": "$B$12", "Horizon": "$B$13", "LamOvr": "$B$14", "GamOvr": "$B$15",
            "Floor": "$B$16", "BaseTS": "$B$17", "Horizon2": "$B$18", "LastTS": "$B$19",
            "SigOvr": "$B$20",
            "v": "$B$22", "Lam": "$B$27", "Gam": "$B$28", "RefLastCred": "$B$29",
            "Mu": "$B$33", "Sig": "$B$34",
        }.items():
            wb.define_name(name, f"=Config!{ref}")

        # =============================================================== README
        _readme(ws_readme, f, res, dataset_name, scenario_name, n, wd, T, n_raw, data, p)

        # =============================================================== Config
        w = ws_cfg
        w.set_column(0, 0, 58)
        w.set_column(1, 1, 18)
        w.set_column(2, 2, 110)
        w.write(0, 0, "CONFIG – blue cells are inputs", f["title"])
        w.write_row(2, 0, ["Parameter", "Value", "Note"], f["head"])
        min_note = ("Observed buckets with ExposureBucket below this are treated as unobserved and "
                    "replaced by the fitted tail.")
        if p.min_exposure_mode == "pct":
            min_note += (f" The scenario states the cut as {p.min_exposure:g}% of the TermStep 1 "
                         f"opening exposure; the Rand amount is shown here.")
        rows = [
            ("EventType", p.event_type, "input",
             "Which block of Raw_Debug is used." + (" The EventType blocks are identical in this zip, "
             "so only one is included in Raw_Debug." if data.identical_events and len(data.event_types) > 1 else "")),
            ("Discount rate p.a.", cfg["rate"], "input_pct",
             "Derived from the file: (1 / DiscountFactor at index 1)^12 − 1 = B24. Type over if the challenger rate changes."),
            ("MaxBucket", cfg["max_bucket"], "input",
             f"Buckets extended to this index (the sheets are built {wd} buckets wide; a larger value has no effect)."),
            ("MinExposure (credibility cut, R)", cfg["min_exposure_abs"], "input_money", min_note),
            ("Window W (buckets)", p.window, "input",
             "The last W credible buckets of each TermStep row anchor the tail level (scale)."),
            ("FitStart bucket", p.fit_start, "input",
             "Start of the regression window for λ / γ on the reference row (end = that row's last credible bucket)."),
            ("Reference TermStep for λ / γ", p.ref_ts, "input",
             "Row whose observed tail is used to fit the decay parameters (1 = longest, best populated)."),
            ("Method (1 = exponential, 2 = power law, 3 = log-normal)", p.method, "input",
             'Selected extension used in the "Selected" columns. All three are always computed.'),
            ("Log-normal μ override (blank = fitted)", "" if p.mu_override is None else p.mu_override, "input",
             "Location of the log-normal shape on ln b. Fitted value shown in B31; with σ given, μ comes from a single SLOPE (B33)."),
            ("Horizon (months) for the short LGD", p.horizon, "input",
             "Results also show LGD over this horizon only."),
            ("λ override (blank = fitted)", "" if p.lambda_override is None else p.lambda_override, "input",
             "Exponential decay per bucket. Fitted value shown in B25."),
            ("γ override (blank = fitted)", "" if p.gamma_override is None else p.gamma_override, "input",
             "Power-law exponent. Fitted value shown in B26."),
            ("Hazard floor (per bucket)", p.floor, "input", "Minimum RecoveryPct applied to the extended tail."),
            ("Base TermStep row for the derived LGD", p.base_ts, "input",
             "LGD_300 derives TermSteps beyond LastTS by rolling this row's extended cash curve forward."),
            ("Valuation horizon (months)", p.horizon2, "input",
             "LGD_300 also shows LGD over this horizon only."),
            ("Last observed TermStep to use as-is", cfg["last_ts"], "input",
             "TermSteps up to this come from their own observed+extended row; beyond it from the base-row derivation."),
            ("Log-normal σ override (blank = fitted)", "" if p.sigma_override is None else p.sigma_override, "input",
             "Spread of the log-normal shape on ln b. Fitted value shown in B32; with μ given, σ comes from a single SLOPE (B34)."),
        ]
        for i, (label, value, fmt, note) in enumerate(rows):
            w.write(3 + i, 0, label)
            if value == "":
                w.write_blank(3 + i, 1, None, f[fmt] if fmt else None)
            elif isinstance(value, str):
                w.write_string(3 + i, 1, value, f[fmt] if fmt else None)    # never a formula
            else:
                w.write_number(3 + i, 1, value, f[fmt] if fmt else None)
            w.write(3 + i, 2, note)
        w.write(20, 0, "Derived", f["bold"])
        tail5 = f"Tail_Fit!$C$5:${wc}$5"
        tail10 = f"Tail_Fit!$C$10:${wc}$10"
        tail11 = f"Tail_Fit!$C$11:${wc}$11"

        def fit_rng(row):                      # the regression window on one Tail_Fit row
            rng_ = f"Tail_Fit!$C${row}:${wc}${row}"
            return f"INDEX({rng_},FitStart):INDEX({rng_},RefLastCred)"

        xr, yr, x2r, ryr, rx2r, s16r, s17r = (fit_rng(r_) for r_ in (11, 12, 13, 14, 15, 16, 17))
        derived = [
            ("Monthly discount factor v = (1+r)^(−1/12)", "=(1+Rate)^(-1/12)", cfg["v"]),
            ("Last observed bucket in file (max BucketIndex with exposure > 0)",
             f'=_xlfn.MAXIFS({RD("E")},{RD("D")},">0",{RD("A")},EventType)', cfg["last_obs_file"]),
            ("Rate implied by the file",
             f'=(1/INDEX({RD("J")},MATCH(1,{RD("I")},0)))^12-1', cfg["rate_implied"]),
            ("λ fitted (ref row, FitStart..LastCredible)",
             f"=-SLOPE(INDEX({tail10},FitStart):INDEX({tail10},RefLastCred),INDEX({tail5},FitStart):INDEX({tail5},RefLastCred))",
             cfg["lam_fit"]),
            ("γ fitted (ref row, ln b)",
             f"=-SLOPE(INDEX({tail10},FitStart):INDEX({tail10},RefLastCred),INDEX({tail11},FitStart):INDEX({tail11},RefLastCred))",
             cfg["gam_fit"]),
            ("λ used", '=IF(LamOvr="",B25,LamOvr)', cfg["lam"]),
            ("γ used", '=IF(GamOvr="",B26,GamOvr)', cfg["gam"]),
            ("Reference row last credible bucket", f"=INDEX(Tail_Fit!$C${TR + 1}:$C${tail_last},RefTS)", cfg["ref_last_cred"]),
            ("Half-life of the exponential tail (buckets)", "=LN(2)/Lam", cfg["half_life"]),
            ("Log-normal μ fitted (ref row, ln R + ln b on ln b and ln b²)",
             f"=IF(B35<0,(SLOPE({yr},{xr})-B35*SLOPE({x2r},{xr}))*B32^2,NA())", cfg["mu_fit"]),
            ("Log-normal σ fitted", "=IF(B35<0,SQRT(-1/(2*B35)),NA())", cfg["sigma_fit"]),
            ("Log-normal μ used",
             f'=IF(MuOvr<>"",MuOvr,IF(SigOvr<>"",SLOPE({s16r},{xr})*SigOvr^2,B31))', cfg["mu"]),
            ("Log-normal σ used",
             f'=IF(SigOvr<>"",SigOvr,IF(MuOvr<>"",IFERROR(SQRT(-1/(2*SLOPE({yr},{s17r}))),NA()),B32))', cfg["sigma"]),
            ("Log-normal quadratic coefficient a2 (must be < 0)", f"=IFERROR(SLOPE({ryr},{rx2r}),NA())",
             _logn_a2(res)),
            ("Log-normal mode (bucket of peak recovery) = exp(μ − σ²)", "=EXP(Mu-Sig^2)", cfg["logn_mode"]),
            ("Log-normal median (bucket) = exp(μ)", "=EXP(Mu)", cfg["logn_median"]),
        ]
        for i, (label, formula, value) in enumerate(derived):
            w.write(21 + i, 0, label)
            w.write_formula(21 + i, 1, formula, f["num"] if i not in (1, 7) else None, _v(value))
        info = [
            ("Target TermStep (rows built on LGD_300)", T,
             "Fixed when the workbook is built. Run the scenario with another Target TermStep to change it."),
            ("Vintages", vintage_text(cfg),
             "Fixed when the workbook is built. " + (
                 f"Raw_Debug holds rows rebuilt from runoff_triangle.csv for vintages from "
                 f"{cfg['vintage_start_effective']} ({cfg['cohorts_included']} of {cfg['cohorts_total']} cohorts); "
                 f"the file's last observed bucket with all vintages is {cfg['last_obs_unfiltered']}."
                 if filtered else "Raw_Debug holds the file's rows over all default vintages.")),
            ("Vintage parameters (start month / last N years)",
             f"{p.vintage_start or ''} / {p.vintage_years or ''}".strip(" /") or "none", ""),
        ]
        for i, (label, value, note) in enumerate(info):
            w.write(21 + len(derived) + i, 0, label)
            if isinstance(value, str):
                w.write_string(21 + len(derived) + i, 1, value)
            else:
                w.write_number(21 + len(derived) + i, 1, value)
            w.write(21 + len(derived) + i, 2, note)

        # ============================================================== Results
        w = ws_res
        w.set_column(0, 0, 30)
        w.set_column(1, 18, 15)
        R = res.results
        avg = res.averages
        w.write(0, 0, "Results by TermStep – original (truncated) vs extended LGD", f["title"])
        w.write(1, 0, "E must be 0.0000 (framework replica of the file's own PV from the observed triangle). "
                      "Uplift = recoveries added by extending the buckets. Weighted averages use ExposureBucket "
                      "at (ts, ts) – the balance of the accounts valued at that TermStep.", f["note"])
        w.write(3, 0, "Exposure-weighted average over all TermSteps", f["bold"])
        w.write_row(4, 0, ["(weighted by Exposure at ts)", "Original LGD", "Replica LGD", "LGD exponential",
                           "LGD power", "LGD log-normal", "LGD SELECTED", "Uplift"], f["bold"])
        rng = lambda col: f"{col}$10:{col}${res_last}"
        for c, (col, key) in enumerate([("C", "lgd_file"), ("D", "lgd_replica"), ("F", "lgd_exp"), ("G", "lgd_power"),
                                        ("H", "lgd_logn"), ("I", "lgd_selected"), ("L", "uplift")], start=1):
            w.write_formula(5, c, f"=SUMPRODUCT($B$10:$B${res_last},{rng(col)})/SUM($B$10:$B${res_last})",
                            f["lgd_b"], _v(avg[key]))
        w.write(6, 0, "Tie-out range (max / min)")
        w.write_formula(6, 1, f"=MAX(E10:E{res_last})", f["sci"], _v(avg["tie_max"]))
        w.write(6, 2, "Simple average uplift")
        w.write_formula(6, 3, f"=AVERAGE(L10:L{res_last})", f["lgd"], _v(avg["uplift_simple"]))
        w.write_formula(7, 1, f"=MIN(E10:E{res_last})", f["sci"], _v(avg["tie_min"]))
        heads = ["TermStep", "Exposure at ts (R)", cfg["lgd_file_label"], "Replica LGD (observed only)",
                 "Tie-out (D − R)", "LGD – exponential", "LGD – power", "LGD – log-normal",
                 "LGD – SELECTED (Config Method)", "Original PV recoveries", "Selected PV recoveries",
                 "Uplift in recoveries (K − J)", "LGD within Horizon – selected",
                 "Undiscounted recoveries – observed", "Undiscounted recoveries – selected",
                 "Last credible bucket", "Buckets added",
                 "File LGD before floor (1 − CumulativeSumPV)", "File LGD floor (C − R)"]
        w.set_row(8, 48)
        w.write_row(8, 0, heads, f["head"])
        lgd_unfloored = np.asarray(R["lgd_file"]) - np.asarray(R["file_floor_gap"])
        ext_rng = lambda sheet, col: f"{sheet}!${col}$6:${col}${ext_last}"
        for ts in range(1, n + 1):
            r, i, ri = 9 + ts, ts - 1, 4 + ts
            row0 = r - 1
            guard = (f"OR(Raw_Index!$C${ri}=0,Config!$B$23<A{r},Config!$B$23-A{r}>=Raw_Index!$C${ri})")
            pos = f"Raw_Index!$B${ri}+Config!$B$23-A{r}"
            w.write_number(row0, 0, ts)
            w.write_formula(row0, 1, f"=INDEX(Hazard_Obs!$C${e_hdr + 1}:${nc}${e_hdr + n},A{r},A{r})",
                            f["money"], _v(R["exposure"][i]))
            w.write_formula(row0, 2, f'=IF({guard},"",INDEX({RD("M")},{pos}))', f["lgd"], _v(R["lgd_file"][i]))
            w.write_formula(row0, 3, f"=INDEX({ext_rng('Ext_Exp', PK)},A{r})", f["lgd"], _v(R["lgd_replica"][i]))
            w.write_formula(row0, 4, f'=IF(R{r}="","",D{r}-R{r})', f["sci"], _v(R["tie_out"][i]))
            w.write_formula(row0, 5, f"=INDEX({ext_rng('Ext_Exp', PI)},A{r})", f["lgd"], _v(R["lgd_exp"][i]))
            w.write_formula(row0, 6, f"=INDEX({ext_rng('Ext_Power', PI)},A{r})", f["lgd"], _v(R["lgd_power"][i]))
            w.write_formula(row0, 7, f"=INDEX({ext_rng('Ext_LogN', PI)},A{r})", f["lgd"], _v(R["lgd_logn"][i]))
            w.write_formula(row0, 8, f"=CHOOSE(Method,F{r},G{r},H{r})", f["lgd_b"], _v(R["lgd_selected"][i]))
            w.write_formula(row0, 9, f"=1-D{r}", f["lgd"], _v(R["pv_original"][i]))
            w.write_formula(row0, 10, f"=1-I{r}", f["lgd"], _v(R["pv_selected"][i]))
            w.write_formula(row0, 11, f"=K{r}-J{r}", f["lgd"], _v(R["uplift"][i]))
            w.write_formula(row0, 12,
                            f"=CHOOSE(Method,INDEX({ext_rng('Ext_Exp', PM)},A{r}),INDEX({ext_rng('Ext_Power', PM)},A{r}),"
                            f"INDEX({ext_rng('Ext_LogN', PM)},A{r}))", f["lgd"], _v(R["lgd_horizon"][i]))
            w.write_formula(row0, 13, f"=INDEX({ext_rng('Ext_Exp', PO)},A{r})", f["lgd"], _v(R["undisc_observed"][i]))
            w.write_formula(row0, 14,
                            f"=CHOOSE(Method,INDEX({ext_rng('Ext_Exp', PN)},A{r}),INDEX({ext_rng('Ext_Power', PN)},A{r}),"
                            f"INDEX({ext_rng('Ext_LogN', PN)},A{r}))", f["lgd"], _v(R["undisc_selected"][i]))
            w.write_formula(row0, 15, f"=Tail_Fit!$C${TR + ts}", None, _v(R["last_cred"][i]))
            w.write_formula(row0, 16, f"=MaxBucket-MAX(P{r},A{r}-1)", None, _v(R["buckets_added"][i]))
            w.write_formula(row0, 17, f'=IF({guard},"",1-INDEX({RD("L")},{pos}))', f["lgd"], _v(lgd_unfloored[i]))
            w.write_formula(row0, 18, f'=IF(C{r}="","",C{r}-R{r})', f["num"], _v(R["file_floor_gap"][i]))
        w.freeze_panes(9, 1)

        # ============================================================== LGD_300
        w = ws_lgd
        L = res.lgd_ts
        w.set_column(0, 0, 44)
        w.set_column(1, 14, 15)
        w.write(0, 0, f"LGD extended to TermStep {T} – observed rows where they exist, base-row derivation beyond",
                f["title"])
        w.write(1, 0, "Rows 5-7: the base TermStep's extended cash curve c(b) under each shape (from the Ext sheets). "
                      "For a TermStep ts the balance is the base balance less cash collected in buckets BaseTS..ts−1, so "
                      "RecoveryPct(ts,b) = c(b) ÷ (1 − Σ c(BaseTS..ts−1)) and LGD(ts) = 1 − Σ_{b≥ts} RecoveryPct × "
                      "v^(b−ts+1), capped at MaxBucket. Columns F-H give this derivation for EVERY ts; D is the row's own "
                      "observed+extended LGD (ts ≤ LastTS); J is the FINAL series. K validates the derivation against "
                      "the observed rows.", f["note"])
        w.write(3, 0, "Bucket b", f["bold"])
        w.write_row(3, 3, list(range(1, wd + 1)), f["bold"])
        for k, (label, sheet) in enumerate([("c(b) – exponential", "Ext_Exp"), ("c(b) – power", "Ext_Power"),
                                            ("c(b) – log-normal", "Ext_LogN")]):
            w.write(4 + k, 0, label)
            for b in range(1, wd + 1):
                w.write_formula(4 + k, b + 2, f"=INDEX({sheet}!$C$6:${wc}${ext_last},BaseTS,{lc(b)}$4)",
                                f["tri"], _v(res.base_curves[k, b]))
        w.write(7, 0, "v^b")
        for b in range(1, wd + 1):
            w.write_formula(7, b + 2, f"=v^{lc(b)}$4", None, _v(res.vb[b]))
        w.write(9, 0, "Max |validation| over ts ≤ LastTS")
        w.write_formula(9, 1, f"=MAX(K13:K{lgd_last})", f["lgd"], _v(res.lgd_ts_summary["validation_max"]))
        w.write_formula(9, 2, f"=MIN(K13:K{lgd_last})", f["lgd"], _v(res.lgd_ts_summary["validation_min"]))
        w.write(10, 0, f"Balance factor at ts = {T} (must stay > 0)")
        w.write_formula(10, 1, f"=E{lgd_last}", f["lgd"], _v(res.lgd_ts_summary["balance_factor_target"]))
        w.set_row(11, 62)
        w.write_row(11, 0, [
            "TermStep", "Exposure at ts (R) – observed rows", f"{cfg['lgd_file_label']} (ts ≤ {n})",
            "Own-row LGD – selected method (ts ≤ LastTS)", "Balance factor at ts (1 − Σ c, base row, selected)",
            "Derived LGD – exponential", "Derived LGD – power", "Derived LGD – log-normal",
            "Derived LGD – selected", "LGD FINAL (own row to LastTS, derived beyond)",
            "Validation: own-row − derived (ts ≤ LastTS)",
            "LGD within valuation horizon (Config Horizon2) – derived basis",
            "FINAL LGD within short horizon (Config Horizon)",
            "Undiscounted remaining recovery – FINAL basis", "Source"], f["head"])
        b4 = f"$D$4:${lw}$4"
        v8 = f"$D$8:${lw}$8"
        crow = lambda k: f"$D${5 + k}:${lw}${5 + k}"
        for ts in range(1, T + 1):
            r, i, row0 = 12 + ts, ts - 1, 11 + ts
            A = f"$A{r}"
            pre = lambda k: f"1-SUMPRODUCT({crow(k)}*({b4}>=BaseTS)*({b4}<{A}))"
            den = lambda k: f"MAX({_EPS_TXT},{pre(k)})"
            pv = lambda k, end: (f"1-SUMPRODUCT({crow(k)}*{v8}*({b4}>={A})*({b4}<={end}))/v^({A}-1)/{den(k)}")
            und = lambda k: f"SUMPRODUCT({crow(k)}*({b4}>={A})*({b4}<=MaxBucket))/{den(k)}"
            choose = lambda fn: "CHOOSE(Method," + ",".join(fn(k) for k in range(3)) + ")"
            h2 = f"MIN(MaxBucket,{A}+Horizon2-1)"
            h1 = f"MIN(MaxBucket,{A}+Horizon-1)"
            w.write_number(row0, 0, ts)
            w.write_formula(row0, 1, f'=IF({A}>{n},"",INDEX(Results!$B$10:$B${res_last},{A}))', f["money"], _v(L["exposure"][i]))
            w.write_formula(row0, 2, f'=IF({A}>{n},"",INDEX(Results!$C$10:$C${res_last},{A}))', f["lgd"], _v(L["lgd_file"][i]))
            w.write_formula(row0, 3, f'=IF(OR({A}>LastTS,{A}>{n}),"",INDEX(Results!$I$10:$I${res_last},{A}))',
                            f["lgd"], _v(L["lgd_own"][i]))
            w.write_formula(row0, 4, "=" + choose(pre), f["lgd"], _v(L["balance_factor"][i]))
            for k, key in enumerate(("derived_exp", "derived_power", "derived_logn")):
                w.write_formula(row0, 5 + k, "=" + pv(k, "MaxBucket"), f["lgd"], _v(L[key][i]))
            w.write_formula(row0, 8, f"=CHOOSE(Method,F{r},G{r},H{r})", f["lgd"], _v(L["derived_selected"][i]))
            w.write_formula(row0, 9, f'=IF(D{r}="",I{r},D{r})', f["lgd_b"], _v(L["lgd_final"][i]))
            w.write_formula(row0, 10, f'=IF(D{r}="","",D{r}-I{r})', f["lgd"], _v(L["validation"][i]))
            w.write_formula(row0, 11, "=" + choose(lambda k: pv(k, h2)), f["lgd"], _v(L["lgd_valuation_horizon"][i]))
            w.write_formula(row0, 12, f'=IF(D{r}="",{choose(lambda k: pv(k, h1))},INDEX(Results!$M$10:$M${res_last},{A}))',
                            f["lgd"], _v(L["lgd_short_horizon"][i]))
            w.write_formula(row0, 13, f'=IF(D{r}="",{choose(und)},INDEX(Results!$O$10:$O${res_last},{A}))',
                            f["lgd"], _v(L["undisc_remaining"][i]))
            w.write_formula(row0, 14, f'=IF(D{r}="","derived","own row")', None, str(L["source"][i]))
        w.freeze_panes(12, 1)

        # =========================================================== Hazard_Obs
        w = ws_obs
        w.set_column(0, 1, 10)
        w.set_column(2, n + 1, 12)
        w.write(0, 0, "Observed triangles rebuilt from Raw_Debug (via Raw_Index, by EventType, TermStep, BucketIndex)",
                f["title"])
        w.write(1, 0, f"Block 1 (rows 6..): RecoveryPct = cash in bucket b as % of balance at TermStep. Block 2 "
                      f"(rows {e_hdr + 1}..): ExposureBucket (R) – the credibility base. Blank = b < ts.", f["note"])
        for blk, (title, hdr, col, fmt, arr) in enumerate([
                ("RecoveryPct", 5, "H", f["tri"], res.R),
                ("ExposureBucket (R)", e_hdr, "D", f["tri_money"], res.E)]):
            w.write(hdr - 2, 0, title, f["bold"])
            w.write(hdr - 1, 0, "TermStep", f["bold"])
            w.write(hdr - 1, 1, "ts", f["bold"])
            w.write_row(hdr - 1, 2, list(range(1, n + 1)), f["bold"])
            src = RD(col)
            for ts in range(1, n + 1):
                r, ri = hdr + ts, 4 + ts
                w.write_number(r - 1, 0, ts)
                w.write_number(r - 1, 1, ts)
                tail = f"-$A{r}>=Raw_Index!$C${ri}),0,INDEX({src},Raw_Index!$B${ri}+"
                vals = arr[ts]
                for b in range(1, n + 1):
                    c = bc(b)
                    w.write_formula(r - 1, b + 1, f"=IF(OR({c}${hdr}<$A{r},{c}${hdr}{tail}{c}${hdr}-$A{r}))",
                                    fmt, float(vals[b]))
        w.freeze_panes(5, 2)

        # ============================================================= Tail_Fit
        w = ws_tail
        T_ = res.tail_fit
        w.set_column(0, 0, 40)
        w.set_column(1, 11, 14)
        w.write(0, 0, "Tail shapes and per-TermStep anchoring", f["title"])
        w.write(1, 0, "Rows 5–11: bucket index, the three shapes, v^b, and the reference row's ln(RecoveryPct) / ln(b) "
                      "used to fit λ and γ. Rows 12–17: the log-normal fit (y = ln R + ln b on x = ln b and x²) as "
                      "single-regressor steps: y, x², the residuals of y and x² on x (their SLOPE is the quadratic "
                      "coefficient), and the transformed y / x used when σ or μ is given on Config. "
                      f"Rows {TR + 1}..: per TermStep – last observed and last credible bucket, anchor "
                      "window, and the scale for each shape (Σ observed over window ÷ Σ shape over window).", f["note"])
        w.write(4, 0, "Bucket b", f["bold"])
        w.write_row(4, 2, list(range(1, wd + 1)), f["bold"])
        obs_tab = f"Hazard_Obs!$C$6:${nc}${5 + n}"
        xw, yw, x2w = fit_rng(11), fit_rng(12), fit_rng(13)
        ref_row = res.R[p.ref_ts]
        shape_rows = [
            ("Shape 1: exponential e^(−λb)", lambda c, b: f"=EXP(-Lam*{c}$5)", lambda b: res.shapes[0, b], f["sci"]),
            ("Shape 2: power b^(−γ)", lambda c, b: f"={c}$5^(-Gam)", lambda b: res.shapes[1, b], f["sci"]),
            ("Shape 3: log-normal exp(−(ln b − μ)² / (2σ²)) / b",
             lambda c, b: f"=EXP(-((LN({c}$5)-Mu)^2)/(2*Sig^2))/{c}$5",
             lambda b: res.shapes[2, b], f["sci"]),
            ("v^b (discount)", lambda c, b: f"=v^{c}$5", lambda b: res.vb[b], None),
            ("ln RecoveryPct – reference row",
             lambda c, b: (f'=IFERROR(IF(INDEX({obs_tab},RefTS,{c}$5)>0,LN(INDEX({obs_tab},RefTS,{c}$5)),""),"")'
                           if b <= n else None),
             lambda b: math.log(ref_row[b]) if b <= n and ref_row[b] > 0 else "", None),
            ("ln b", lambda c, b: f"=LN({c}$5)", lambda b: math.log(b), None),
            ("y = ln R + ln b (log-normal fit)", lambda c, b: f'=IF(ISNUMBER({c}$10),{c}$10+{c}$11,"")',
             lambda b: helper[12][b - 1], None),
            ("x² = (ln b)² where y exists", lambda c, b: f'=IF(ISNUMBER({c}$10),{c}$11^2,"")',
             lambda b: helper[13][b - 1], None),
            ("residual of y on x over the fit window",
             lambda c, b: f'=IF(ISNUMBER({c}$12),{c}$12-(INTERCEPT({yw},{xw})+SLOPE({yw},{xw})*{c}$11),"")',
             lambda b: helper[14][b - 1], None),
            ("residual of x² on x over the fit window",
             lambda c, b: f'=IF(ISNUMBER({c}$13),{c}$13-(INTERCEPT({x2w},{xw})+SLOPE({x2w},{xw})*{c}$11),"")',
             lambda b: helper[15][b - 1], None),
            ("y + x² / (2σ²) when σ is given (μ = SLOPE on x × σ²)",
             lambda c, b: f'=IF(OR(SigOvr="",NOT(ISNUMBER({c}$12))),"",{c}$12+{c}$13/(2*SigOvr^2))',
             lambda b: helper[16][b - 1], None),
            ("x² − 2μx when μ is given (a2 = SLOPE of y on this)",
             lambda c, b: f'=IF(OR(MuOvr="",NOT(ISNUMBER({c}$12))),"",{c}$13-2*MuOvr*{c}$11)',
             lambda b: helper[17][b - 1], None),
        ]
        for k, (label, formula, value, fmt) in enumerate(shape_rows):
            w.write(5 + k, 0, label)
            for b in range(1, wd + 1):
                fm = formula(bc(b), b)
                if fm is not None:
                    w.write_formula(5 + k, b + 1, fm, fmt, _v(value(b)))
        w.set_row(TR - 1, 62)
        w.write_row(TR - 1, 0, ["TermStep", "Last observed bucket",
                            "Last credible bucket (ExposureBucket ≥ MinExposure; falls back to last observed if none)",
                            "Window start", "Window end", "Buckets in window", "Σ observed over window",
                            "Scale – exponential", "Scale – power", "Scale – log-normal",
                            "Shape 1 value at window end", "Shape 3 value at window end"], f["head"])
        hb = f"Hazard_Obs!$C$5:${nc}$5"
        for ts in range(1, n + 1):
            r, i, row0 = TR + ts, ts - 1, TR - 1 + ts
            er = f"Hazard_Obs!$C${e_hdr + ts}:${nc}${e_hdr + ts}"
            rr = f"Hazard_Obs!$C${5 + ts}:${nc}${5 + ts}"
            win = f"({hb}>=D{r})*({hb}<=E{r})"
            mx = f'_xlfn.MAXIFS({hb},{er},">="&MinExp)'
            lc_ = int(T_["last_cred"][i])
            w.write_number(row0, 0, ts)
            w.write_formula(row0, 1, f'=IFERROR(_xlfn.MAXIFS({hb},{er},">0"),0)', None, _v(T_["last_obs"][i]))
            w.write_formula(row0, 2, f"=IF({mx}>=A{r},{mx},B{r})", None, lc_)
            w.write_formula(row0, 3, f"=MAX(A{r},C{r}-WinW+1)", None, _v(T_["win_start"][i]))
            w.write_formula(row0, 4, f"=C{r}", None, _v(T_["win_end"][i]))
            w.write_formula(row0, 5, f"=IF(C{r}<A{r},0,E{r}-D{r}+1)", None, _v(T_["n_win"][i]))
            w.write_formula(row0, 6, f"=IF(F{r}=0,0,SUMPRODUCT({win}*{rr}))", f["num"], _v(T_["sum_obs"][i]))
            for k, key in enumerate(("scale_exp", "scale_power", "scale_logn")):
                w.write_formula(row0, 7 + k,
                                f"=IF(F{r}=0,0,IFERROR(G{r}/SUMPRODUCT({win}*$C${6 + k}:${nc}${6 + k}),0))",
                                f["num"], _v(T_[key][i]))
            w.write_formula(row0, 10, f'=IF(C{r}<1,"",INDEX($C$6:${wc}$6,C{r}))', f["sci"],
                            _v(res.shapes[0, lc_]) if lc_ >= 1 else "")
            w.write_formula(row0, 11, f'=IF(C{r}<1,"",INDEX($C$8:${wc}$8,C{r}))', f["sci"],
                            _v(res.shapes[2, lc_]) if lc_ >= 1 else "")
        w.freeze_panes(TR, 1)

        # ================================================================ Ext_*
        titles = ["Extended triangle – Shape 1: exponential decay",
                  "Extended triangle – Shape 2: power law",
                  "Extended triangle – Shape 3: log-normal"]
        scale_col = ["H", "I", "J"]
        b5 = f"$C$5:${wc}$5"
        vrow = f"Tail_Fit!$C$9:${wc}$9"
        for k, w in enumerate(ws_ext):
            w.set_column(0, 1, 10)
            w.set_column(2, wd + 1, 12)
            w.set_column(wd + 3, wd + 10, 16)
            w.write(0, 0, titles[k], f["title"])
            w.write(1, 0, "RecoveryPct per bucket, extended: observed up to the last credible bucket (Tail_Fit!C), then "
                          "scale × shape (floored). Columns right of the triangle: PV results per TermStep. "
                          "0 shown blank = b < ts.", f["note"])
            w.write(4, 0, "TermStep", f["bold"])
            w.write(4, 1, "Last cred.", f["bold"])
            w.write_row(4, 2, list(range(1, wd + 1)), f["bold"])
            w.write_row(4, wd + 3, [
                "PV recoveries to MaxBucket (% of balance at ts)", "LGD extended",
                "PV recoveries – observed buckets only (replica of file)", "LGD – observed only",
                "PV recoveries within Horizon", "LGD within Horizon",
                "Undiscounted recoveries to MaxBucket", "Undiscounted – observed only"], f["bold"])
            ext = res.ext[k]
            shape_row = 6 + k
            for ts in range(1, n + 1):
                r, row0 = 5 + ts, 4 + ts
                w.write_number(row0, 0, ts)
                w.write_formula(row0, 1, f"=Tail_Fit!$C${TR + ts}", None, int(res.tail_fit["last_cred"][ts - 1]))
                sc = f"Tail_Fit!${scale_col[k]}${TR + ts}"
                vals = ext[ts]
                for b in range(1, wd + 1):
                    c = bc(b)
                    inner = f"Hazard_Obs!{c}${5 + ts}" if b <= n else "0"
                    w.write_formula(
                        row0, b + 1,
                        f"=IF({c}$5<$A{r},0,IF({c}$5<=$B{r},{inner},MAX(Floor,{sc}*Tail_Fit!{c}${shape_row})))",
                        f["tri"], _v(vals[b]))
                rowrng = f"$C{r}:${wc}{r}"
                obs_r = f"Hazard_Obs!$C${5 + ts}:${nc}${5 + ts}"
                lgd_k = float(st["lgd"][k, ts])
                lgd_o = float(st["lgd_obs"][ts])
                lgd_h = float(st["lgd_h"][k, ts])
                c0 = wd + 3
                w.write_formula(row0, c0, f"=SUMPRODUCT({rowrng}*{vrow}*({b5}<=MaxBucket))/v^($A{r}-1)", f["lgd"], _v(1 - lgd_k))
                w.write_formula(row0, c0 + 1, f"=1-{PH}{r}", f["lgd"], _v(lgd_k))
                w.write_formula(row0, c0 + 2, f"=SUMPRODUCT({obs_r}*Tail_Fit!$C$9:${nc}$9)/v^($A{r}-1)", f["lgd"], _v(1 - lgd_o))
                w.write_formula(row0, c0 + 3, f"=1-{PJ}{r}", f["lgd"], _v(lgd_o))
                w.write_formula(row0, c0 + 4,
                                f"=SUMPRODUCT({rowrng}*{vrow}*({b5}<=MIN(MaxBucket,$A{r}+Horizon-1)))/v^($A{r}-1)",
                                f["lgd"], _v(1 - lgd_h))
                w.write_formula(row0, c0 + 5, f"=1-{PL}{r}", f["lgd"], _v(lgd_h))
                w.write_formula(row0, c0 + 6, f"=SUMPRODUCT({rowrng}*({b5}<=MaxBucket))", f["lgd"], _v(st["und"][k, ts]))
                w.write_formula(row0, c0 + 7, f"=SUM({obs_r})", f["lgd"], _v(st["und_obs"][ts]))
            w.freeze_panes(5, 2)

        # ============================================================ Raw_Index
        w = ws_idx
        w.set_column(0, 3, 26)
        w.write(0, 0, "Where each TermStep starts in Raw_Debug (for the selected EventType)", f["title"])
        w.write(1, 0, "Hazard_Obs reads Raw_Debug by position: bucket b of TermStep ts is row (first row + b − ts). "
                      "A refreshed block pasted into Raw_Debug must keep the delivered order: per EventType and "
                      "TermStep, buckets ascending from ts without gaps. Column D flags any TermStep that does not.",
                f["note"])
        w.write_row(3, 0, ["TermStep", "First row within Raw_Debug data", "Rows", "Order check"], f["head"])
        for ts in range(1, n + 1):
            r = 4 + ts
            w.write_number(r - 1, 0, ts)
            w.write_formula(r - 1, 1, f'=IFERROR(MATCH(1,INDEX(({RD("A")}=EventType)*({RD("B")}=$A{r}),0),0),0)',
                            None, int(first_pos[ts]))
            w.write_formula(r - 1, 2, f'=COUNTIFS({RD("A")},EventType,{RD("B")},$A{r})', None, int(count[ts]))
            w.write_formula(
                r - 1, 3,
                f'=IF(C{r}=0,"",IF(AND(INDEX({RD("E")},B{r})=A{r},INDEX({RD("E")},B{r}+C{r}-1)=A{r}+C{r}-1),"OK","CHECK"))',
                None, "OK" if count[ts] else "")

        # ============================================================ Raw_Debug
        w = ws_raw
        w.set_column(0, 12, 16)
        w.write(0, 0, (f"Rows rebuilt from runoff_triangle.csv for vintages from {cfg['vintage_start_effective']} "
                       f"({cfg['cohorts_included']} of {cfg['cohorts_total']} cohorts), in the layout of lgd_recovery: "
                       f"ExposureBucket, PrevColSum and ThisColSum are summed over the kept vintages, DiscountIndex = "
                       f"b − ts + 1, DiscountFactor = (1 + r)^(−index/12), and LGD = 1 − CumulativeSumPV floored at the "
                       f"previous TermStep's final LGD, as the risk suite does." if filtered else
                       "Debug output as delivered (values). Paste a refreshed lgd_recovery block over this, in the same order."),
                f["note"])
        w.write_row(2, 0, COLUMNS, f["head"])
        row0 = 3
        for ev, blk in blocks:
            for line in blk.tolist():
                w.write_string(row0, 0, ev)
                w.write_row(row0, 1, line)
                row0 += 1

        # =============================================================== Charts
        _charts(wb, ws_ch, f, n, wd, T, res_last, lgd_last, nc, wc, p, dataset_name, scenario_name,
                cfg["lgd_file_label"])

        wb.close()
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _logn_a2(res: ExtensionResult) -> float:
    """The quadratic coefficient of the full log-normal fit, −1/(2σ²), for the Config cache."""
    s = res.config.get("sigma_fit")
    return -1.0 / (2.0 * s * s) if s is not None and math.isfinite(s) and s > 0 else float("nan")


def _charts(wb, ws, f, n, wd, T, res_last, lgd_last, nc, wc, p, dataset_name, scenario_name, file_label) -> None:
    ws.write_string(0, 0, f"{dataset_name} – {scenario_name}", f["title"])

    def line(name, cats, vals, color, dash=None, markers=False):
        s = {"name": name, "categories": cats, "values": vals,
             "marker": {"type": "circle", "size": 3, "fill": {"color": color}, "border": {"color": color}}
             if markers else {"type": "none"},
             "line": {"none": True} if markers else {"width": 1.75, "color": color}}
        if dash and not markers:
            s["line"]["dash_type"] = dash
        return s

    size = {"width": 720, "height": 380}
    mid = min(48, max(1, n // 2))
    for ts, anchor in ((1, "A3"), (mid, "M3")):
        ch = wb.add_chart({"type": "scatter", "subtype": "straight"})
        r = 5 + ts
        cats = f"=Hazard_Obs!$C$5:${nc}$5"
        ch.add_series(line("Observed", cats, f"=Hazard_Obs!$C${r}:${nc}${r}", "#000000", markers=True))
        cats_w = f"=Tail_Fit!$C$5:${wc}$5"
        ch.add_series(line("Extended – exponential", cats_w, f"=Ext_Exp!$C${r}:${wc}${r}", "#2E75B6"))
        ch.add_series(line("Extended – power", cats_w, f"=Ext_Power!$C${r}:${wc}${r}", "#C55A11"))
        ch.add_series(line("Extended – log-normal", cats_w, f"=Ext_LogN!$C${r}:${wc}${r}", "#548235"))
        ch.set_title({"name": f"TermStep {ts}: RecoveryPct by bucket (log scale)"})
        ch.set_x_axis({"name": "Bucket", "min": 0, "max": wd})
        ch.set_y_axis({"name": "RecoveryPct", "log_base": 10, "num_format": "0.0000%"})
        ch.set_size(size)
        ws.insert_chart(anchor, ch)

    cats = f"=Results!$A$10:$A${res_last}"
    ch = wb.add_chart({"type": "scatter", "subtype": "straight"})
    ch.add_series(line(file_label, cats, f"=Results!$C$10:$C${res_last}", "#7F7F7F", "dash"))
    ch.add_series(line("Exponential", cats, f"=Results!$F$10:$F${res_last}", "#2E75B6"))
    ch.add_series(line("Power law", cats, f"=Results!$G$10:$G${res_last}", "#C55A11"))
    ch.add_series(line("Log-normal", cats, f"=Results!$H$10:$H${res_last}", "#548235"))
    ch.set_title({"name": "LGD by TermStep – original vs extended"})
    ch.set_x_axis({"name": "TermStep", "min": 0, "max": n})
    ch.set_y_axis({"name": "LGD", "num_format": "0.00"})
    ch.set_size(size)
    ws.insert_chart("A23", ch)

    ch = wb.add_chart({"type": "scatter", "subtype": "straight"})
    ch.add_series(line("Uplift in recoveries", cats, f"=Results!$L$10:$L${res_last}", "#548235"))
    ch.set_title({"name": "Uplift in PV recoveries by TermStep (selected method)"})
    ch.set_x_axis({"name": "TermStep", "min": 0, "max": n})
    ch.set_y_axis({"name": "Uplift", "num_format": "0.000"})
    ch.set_legend({"none": True})
    ch.set_size(size)
    ws.insert_chart("M23", ch)

    cats = f"=LGD_300!$A$13:$A${lgd_last}"
    ch = wb.add_chart({"type": "scatter", "subtype": "straight"})
    ch.add_series(line("LGD FINAL", cats, f"=LGD_300!$J$13:$J${lgd_last}", NAVY))
    ch.add_series(line("Derived (base row)", cats, f"=LGD_300!$I$13:$I${lgd_last}", "#C55A11", "dash"))
    ch.add_series(line(file_label, cats, f"=LGD_300!$C$13:$C${lgd_last}", "#7F7F7F", "dash"))
    ch.set_title({"name": f"LGD to TermStep {T}"})
    ch.set_x_axis({"name": "TermStep", "min": 0, "max": T})
    ch.set_y_axis({"name": "LGD", "num_format": "0.00"})
    ch.set_size(size)
    ws.insert_chart("A43", ch)

    ch = wb.add_chart({"type": "scatter", "subtype": "straight"})
    ch.add_series(line("Lifetime (FINAL)", cats, f"=LGD_300!$J$13:$J${lgd_last}", NAVY))
    ch.add_series(line(f"Within {p.horizon2} months", cats, f"=LGD_300!$L$13:$L${lgd_last}", "#2E75B6"))
    ch.add_series(line(f"Within {p.horizon} months", cats, f"=LGD_300!$M$13:$M${lgd_last}", "#7F7F7F"))
    ch.set_title({"name": "Horizon LGD vs lifetime LGD"})
    ch.set_x_axis({"name": "TermStep", "min": 0, "max": T})
    ch.set_y_axis({"name": "LGD", "num_format": "0.00"})
    ch.set_size(size)
    ws.insert_chart("M43", ch)


def _readme(ws, f, res, dataset_name, scenario_name, n, wd, T, n_raw, data, p) -> None:
    cfg, avg, s = res.config, res.averages, res.lgd_ts_summary
    ws.set_column(0, 0, 3)
    ws.set_column(1, 1, 150)
    R = res.results

    def at(col, ts):
        return float(R[col][ts - 1])

    picks = [t for t in (1, 12, 24, 48, 60, 72, 96, 120, 180, 240, 300) if t <= cfg["last_data_ts"]]
    by_ts = "; ".join(f"ts = {t}: {at('lgd_file', t):.3f} → {at('lgd_selected', t):.3f}" for t in picks)
    L = res.lgd_ts
    beyond = [t for t in (cfg["last_ts"] + 1, 120, 150, 180, 240, 300, 360, T) if cfg["last_ts"] < t <= T]
    beyond_txt = "; ".join(f"ts = {t}: {float(L['lgd_final'][t - 1]):.3f}" for t in sorted(set(beyond)))

    def fmt(x, spec=".3f"):
        return "n/a" if x is None or (isinstance(x, float) and not math.isfinite(x)) else format(x, spec)

    lines = [
        ("title", f"Hazard-rate challenger – bucket extension framework ({dataset_name} → TermStep {T})"),
        ("text", f"Source: debug zip for category {data.category or '(none)'} ({n_raw:,} rows in Raw_Debug; TermStep 1..{n}; "
                 f"EventType {p.event_type}). Scenario: {scenario_name}. Built by the LGD Tail Extension app; every formula "
                 f"is live and Config drives the workbook."),
        ("gap", ""),
        ("head", "WHAT THE DEBUG OUTPUT IS"),
        ("text", "For a TermStep ts (the age at which an account is valued) and a BucketIndex b ≥ ts: ExposureBucket = Σ balance "
                 "at bucket ts of the accounts still observed at bucket b; PrevColSum / ThisColSum = Σ balance of those accounts "
                 "at b−1 and b; RecoveryPct = (PrevColSum − ThisColSum) ÷ ExposureBucket = cash collected in bucket b as a % of "
                 "the balance at ts. Because the denominator is the balance at ts (not the running balance), the RecoveryPct "
                 "values along a row ADD UP directly to the cumulative recovery – no survival weighting is needed. "
                 "DiscountIndex = b − ts + 1, DiscountFactor = (1 + r)^(−DiscountIndex/12), Contribution = RecoveryPct × "
                 "DiscountFactor, CumulativeSumPV = running Σ Contribution. The file's LGD column is 1 − CumulativeSumPV, "
                 "floored at the previous TermStep's final LGD (Results columns R and S show where that floor bites)."),
        ("gap", ""),
        ("head", "THE PROBLEM"),
        ("text", f"The run-off triangle stops at bucket {cfg['last_obs_file']}, so every TermStep is truncated: an account valued "
                 f"at a late TermStep only gets the few buckets that remain and shows an LGD close to 1 purely by truncation. "
                 f"The late buckets that do exist are also thin and noisy."),
        ("gap", ""),
        ("head", "THE FRAMEWORK"),
        ("text", "1. Raw_Debug – the debug output (same 13 columns). Config → EventType selects which block is used. "
                 "Raw_Index locates each TermStep's rows."),
        ("text", "2. Hazard_Obs – the observed triangles: RecoveryPct (ts × b) and ExposureBucket (ts × b)."),
        ("text", "3. Tail_Fit – for each TermStep: the last observed bucket, the last CREDIBLE bucket (ExposureBucket ≥ Config "
                 "MinExposure), the anchor window (the last W credible buckets) and, for each of three tail shapes, the scale "
                 "that makes the shape pass through the window on average (scale = Σ observed RecoveryPct over the window ÷ "
                 "Σ shape over the window). Shapes: (1) exponential decay e^(−λb), λ fitted by log-linear regression on the "
                 "reference TermStep row over [FitStart, LastCredible]; (2) power law b^(−γ), γ fitted the same way on ln b; "
                 "(3) log-normal (1/b)·exp(−(ln b − μ)²/(2σ²)), the form of the client's industry curves, with μ and σ "
                 "fitted by the same least-squares regression (ln R + ln b on ln b and its square) on the same points. "
                 "λ, γ, μ and σ can be overridden on Config."),
        ("text", f"4. Ext_Exp / Ext_Power / Ext_LogN – the extended RecoveryPct triangle for every TermStep to bucket {wd} "
                 f"under each shape: observed values up to the last credible bucket, then scale × shape beyond. PV recoveries "
                 f"per TermStep = SUMPRODUCT(row, v^b) ÷ v^(ts−1). A short-horizon PV (Config Horizon) is also given."),
        ("text", f"5. Results – per TermStep 1..{n}: the original LGD from the debug file, the framework's replica from the "
                 f"observed triangle (tie-out column – must be 0.0000), the extended LGD under each shape, the selected method, "
                 f"the uplift in recoveries, the short-horizon LGD, and exposure-weighted portfolio averages at the top."),
        ("text", f"6. LGD_300 – the LGD for TermStep 1..{T}. TermSteps up to LastTS use their own observed+extended row; beyond "
                 f"that the base TermStep's extended cash curve c(b) is rolled forward: balance at ts = base balance − cash "
                 f"collected in buckets BaseTS..ts−1, RecoveryPct(ts,b) = c(b) ÷ that balance factor, LGD = 1 − Σ discounted. "
                 f"Column K validates the derivation against every observed row."),
        ("text", "7. Raw_Index – where each TermStep's rows start in Raw_Debug; column D checks the row order."),
        ("text", ("Vintages: " + vintage_text(cfg) + ". Raw_Debug holds the rows rebuilt from runoff_triangle.csv for those "
                  "vintages, so the 'Original' LGD is the LGD of that subset, not the file's figure.") if cfg.get("vintage_filter")
                 else "Vintages: all default vintages in the file."),
        ("gap", ""),
        ("head", "OBSERVATIONS AT BUILD"),
        ("text", f"• Exposure-weighted LGD across all TermSteps: original {fmt(avg['lgd_file'])} → {fmt(avg['lgd_exp'])} "
                 f"(exponential) / {fmt(avg['lgd_power'])} (power) / {fmt(avg['lgd_logn'])} (log-normal). Selected method: "
                 f"{cfg['method_label']}, {fmt(avg['lgd_selected'])}; uplift {fmt(avg['uplift'], '.4f')} exposure-weighted, "
                 f"{fmt(avg['uplift_simple'], '.4f')} simple average."),
        ("text", f"• Original → selected LGD by TermStep: {by_ts}."),
        ("text", f"• Fitted decay on the reference row: λ = {fmt(cfg['lam_fit'], '.4f')} (half-life "
                 f"{fmt(cfg['half_life'], '.1f')} buckets), γ = {fmt(cfg['gam_fit'], '.2f')}, log-normal μ = "
                 f"{fmt(cfg['mu_fit'])} and σ = {fmt(cfg['sigma_fit'])} (mode at bucket {fmt(cfg['logn_mode'], '.1f')}); "
                 f"discount rate {cfg['rate']:.2%}."),
        ("text", f"• LGD_300: own rows to TermStep {cfg['last_ts']}; validation (own row − derived) ranges "
                 f"{fmt(s['validation_min'], '.4f')} to {fmt(s['validation_max'], '.4f')}; balance factor at TermStep {T} = "
                 f"{fmt(s['balance_factor_target'])}." + (f" Beyond LastTS: {beyond_txt}." if beyond_txt else "")),
    ]
    for wtext in res.warnings:
        lines.append(("text", "• Warning: " + wtext))
    lines += [
        ("gap", ""),
        ("head", "CAVEATS"),
        ("text", "The extension is a modelling assumption, not data: beyond the last credible bucket nothing is observed. The "
                 "window scale uses the last W credible buckets unweighted; MinExposure is the lever that decides how much of "
                 "the noisy observed tail is kept. All three shapes are fitted to the challenger's own data; the log-normal "
                 "shares only its functional form with the client's industry curves, so its μ and σ can be compared with the "
                 "client's parameters without borrowing the client's curve."),
    ]
    for i, (kind, text) in enumerate(lines):
        if kind == "gap":
            continue
        fmt_ = {"title": f["title"], "head": f["bold"], "text": f["wrap"]}[kind]
        ws.write_string(i, 1, text, fmt_)
        if kind == "text":
            ws.set_row(i, max(15, 15 * math.ceil(len(text) / 165)))
