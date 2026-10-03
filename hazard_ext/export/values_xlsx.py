"""Values-only Excel workbook for one zip under one scenario."""
from __future__ import annotations

import io
import math

import numpy as np
import xlsxwriter
from xlsxwriter.utility import xl_rowcol_to_cell

from ..engine.core import ExtensionResult
from .tables import (AVERAGE_LABELS, CONFIG_LABELS, LGD_TS_COLUMNS, PARAM_LABELS,
                     RESULT_COLUMNS, TAIL_COLUMNS)

NAVY = "#1F3A5F"


def _formats(wb) -> dict:
    return {
        "title": wb.add_format({"bold": True, "font_size": 14, "font_color": NAVY}),
        "bold": wb.add_format({"bold": True}),
        "head": wb.add_format({"bold": True, "bg_color": NAVY, "font_color": "white",
                               "text_wrap": True, "valign": "top", "border": 1}),
        "note": wb.add_format({"italic": True, "font_color": "#555555"}),
        "warn": wb.add_format({"font_color": "#9C5700", "bg_color": "#FFEB9C", "text_wrap": True}),
        "int": wb.add_format({"num_format": "0"}),
        "money": wb.add_format({"num_format": "#,##0"}),
        "lgd": wb.add_format({"num_format": "0.0000"}),
        "lgd6": wb.add_format({"num_format": "0.000000"}),
        "sci": wb.add_format({"num_format": "0.00E+00"}),
        "num": wb.add_format({"num_format": "0.000000"}),
        "tri": wb.add_format({"num_format": "0.00000%"}),
        "text": wb.add_format({}),
    }


def _write_value(ws, r, c, v, fmt=None):
    if v is None:
        return
    if isinstance(v, (float, np.floating)):
        if not math.isfinite(v):
            return
        ws.write_number(r, c, float(v), fmt)
    elif isinstance(v, (int, np.integer)) and not isinstance(v, bool):
        ws.write_number(r, c, int(v), fmt)
    elif isinstance(v, bool):
        ws.write_boolean(r, c, v, fmt)
    else:
        ws.write_string(r, c, str(v), fmt)        # never parsed as a formula


def _table(ws, f, top, data: dict, columns) -> None:
    for c, (_, heading, _) in enumerate(columns):
        ws.write(top, c, heading, f["head"])
    for c, (key, _, fmt) in enumerate(columns):
        col = data[key]
        for i, v in enumerate(col):
            _write_value(ws, top + 1 + i, c, v.item() if hasattr(v, "item") else v, f[fmt])
    ws.set_row(top, 48)
    ws.set_column(0, 0, 10)
    ws.set_column(1, 1, 18)
    ws.set_column(2, len(columns) - 1, 15)
    ws.freeze_panes(top + 1, 1)


def _triangle(wb, f, name: str, title: str, tri: np.ndarray, n_rows: int, n_cols: int, fmt) -> None:
    """Rows are TermSteps 1..n_rows, columns are buckets 1..n_cols. Cells with b < ts stay blank."""
    ws = wb.add_worksheet(name)
    ws.write(0, 0, title, f["bold"])
    ws.write(2, 0, "TermStep", f["head"])
    ws.write_row(2, 1, list(range(1, n_cols + 1)), f["head"])
    for ts in range(1, n_rows + 1):
        ws.write_number(2 + ts, 0, ts)
        if ts <= n_cols:
            ws.write_row(2 + ts, ts, tri[ts, ts:n_cols + 1].tolist(), fmt)
    ws.set_column(1, n_cols, 11)
    ws.freeze_panes(3, 1)


def build_values_workbook(res: ExtensionResult, dataset_name: str, scenario_name: str,
                          filename: str = "") -> bytes:
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True, "nan_inf_to_errors": True,
                                   "strings_to_formulas": False, "strings_to_urls": False})
    f = _formats(wb)
    n, mb = res.n, int(res.config["max_bucket"])
    cols = int(res.shape_stats["tri_width"])
    p, cfg, avg = res.params, res.config, res.averages

    # ------------------------------------------------------------------ Summary
    ws = wb.add_worksheet("Summary")
    ws.write(0, 0, "Hazard-rate challenger – bucket extension (values export)", f["title"])
    ws.write_string(1, 0, f"Zip: {dataset_name}" + (f"  ({filename})" if filename else "")
                    + f"   |   Scenario: {scenario_name}", f["note"])
    r = 3
    ws.write(r, 0, "Exposure-weighted average over all TermSteps", f["bold"])
    for i, (key, label) in enumerate(AVERAGE_LABELS):
        ws.write(r + 1 + i, 0, label)
        _write_value(ws, r + 1 + i, 1, avg.get(key), f["lgd"])
    r += len(AVERAGE_LABELS) + 1
    ws.write(r, 0, "Tie-out range (max / min)")
    _write_value(ws, r, 1, avg.get("tie_max"), f["sci"])
    _write_value(ws, r, 2, avg.get("tie_min"), f["sci"])
    r += 2
    ws.write(r, 0, "Parameters (scenario with this zip's overrides applied)", f["bold"])
    for key, label in PARAM_LABELS:
        r += 1
        ws.write(r, 0, label)
        _write_value(ws, r, 1, p.get(key))
    r += 2
    ws.write(r, 0, "Derived", f["bold"])
    for key, label in CONFIG_LABELS:
        r += 1
        ws.write(r, 0, label)
        _write_value(ws, r, 1, cfg.get(key))
    r += 2
    ws.write(r, 0, "LGD to Target TermStep", f["bold"])
    for key, label in [("validation_max", "Max validation (own row − derived)"),
                       ("validation_min", "Min validation (own row − derived)"),
                       ("balance_factor_target", "Balance factor at Target TermStep (must stay > 0)")]:
        r += 1
        ws.write(r, 0, label)
        _write_value(ws, r, 1, res.lgd_ts_summary.get(key), f["lgd"])
    r += 2
    ws.write(r, 0, "Warnings", f["bold"])
    if not res.warnings:
        ws.write(r + 1, 0, "None")
    for i, w in enumerate(res.warnings):
        ws.merge_range(r + 1 + i, 0, r + 1 + i, 6, w, f["warn"])
        ws.set_row(r + 1 + i, 32)
    ws.set_column(0, 0, 62)
    ws.set_column(1, 2, 18)

    # ------------------------------------------------------------------- tables
    ws_res = wb.add_worksheet("Results")
    _table(ws_res, f, 0, res.results, RESULT_COLUMNS)
    ws_lgd = wb.add_worksheet("LGD_TS")
    _table(ws_lgd, f, 0, res.lgd_ts, LGD_TS_COLUMNS)
    ws_tail = wb.add_worksheet("Tail_Fit")
    _table(ws_tail, f, 0, res.tail_fit, TAIL_COLUMNS)

    # --------------------------------------------------------------- chart data
    wsd = wb.add_worksheet("Chart_Data")
    chart_ts = [1] + ([min(48, n // 2)] if n >= 4 else [])
    blocks = []
    c0 = 0
    for ts in chart_ts:
        cur = res.curve(ts)
        wsd.write(0, c0, f"TermStep {ts}: RecoveryPct by bucket", f["bold"])
        heads = ["Bucket", "Observed", "Exponential", "Power", "Reference curve shape"]
        keys = ["bucket", "observed", "exp", "power", "client"]
        for j, h in enumerate(heads):
            wsd.write(1, c0 + j, h, f["head"])
        for j, k in enumerate(keys):
            for i, v in enumerate(cur[k]):
                # zero cannot be drawn on a log axis; leave it blank
                if v is not None and (k == "bucket" or v > 0):
                    wsd.write_number(2 + i, c0 + j, v)
        blocks.append((ts, c0, len(cur["bucket"])))
        c0 += len(heads) + 1

    # ------------------------------------------------------------------- charts
    wsc = wb.add_worksheet("Charts")
    wsc.write_string(0, 0, f"{dataset_name} – {scenario_name}", f["title"])

    def col_of(columns, key):
        return [k for k, _, _ in columns].index(key)

    def series(sheet, columns, key, rows, name, **kw):
        c = col_of(columns, key)
        return {"name": name, "categories": [sheet, 1, 0, rows, 0],
                "values": [sheet, 1, c, rows, c], "marker": {"type": "none"},
                "line": {"width": 1.75, **kw}}

    T = len(res.lgd_ts["ts"])
    ch = wb.add_chart({"type": "scatter", "subtype": "straight"})
    ch.add_series(series("Results", RESULT_COLUMNS, "lgd_file", n, "Original (file)", color="#7F7F7F", dash_type="dash"))
    ch.add_series(series("Results", RESULT_COLUMNS, "lgd_exp", n, "Exponential", color="#2E75B6"))
    ch.add_series(series("Results", RESULT_COLUMNS, "lgd_power", n, "Power law", color="#C55A11"))
    if cfg.get("client_cohort") is not None:
        ch.add_series(series("Results", RESULT_COLUMNS, "lgd_client", n, "Reference curve shape", color="#548235"))
    ch.set_title({"name": "LGD by TermStep – original vs extended"})
    ch.set_x_axis({"name": "TermStep", "min": 0, "max": n})
    ch.set_y_axis({"name": "LGD", "num_format": "0.00"})
    ch.set_size({"width": 720, "height": 380})
    wsc.insert_chart("A3", ch)

    ch = wb.add_chart({"type": "scatter", "subtype": "straight"})
    ch.add_series(series("Results", RESULT_COLUMNS, "uplift", n, "Uplift in recoveries", color="#548235"))
    ch.set_title({"name": "Uplift in PV recoveries by TermStep (selected method)"})
    ch.set_x_axis({"name": "TermStep", "min": 0, "max": n})
    ch.set_y_axis({"name": "Uplift", "num_format": "0.000"})
    ch.set_legend({"none": True})
    ch.set_size({"width": 720, "height": 380})
    wsc.insert_chart("M3", ch)

    ch = wb.add_chart({"type": "scatter", "subtype": "straight"})
    ch.add_series(series("LGD_TS", LGD_TS_COLUMNS, "lgd_final", T, "LGD FINAL", color=NAVY))
    ch.add_series(series("LGD_TS", LGD_TS_COLUMNS, "derived_selected", T, "Derived (base row)", color="#C55A11", dash_type="dash"))
    ch.add_series(series("LGD_TS", LGD_TS_COLUMNS, "lgd_file", T, "Original (file)", color="#7F7F7F", dash_type="dash"))
    ch.set_title({"name": f"LGD to TermStep {T}"})
    ch.set_x_axis({"name": "TermStep", "min": 0, "max": T})
    ch.set_y_axis({"name": "LGD", "num_format": "0.00"})
    ch.set_size({"width": 720, "height": 380})
    wsc.insert_chart("A23", ch)

    ch = wb.add_chart({"type": "scatter", "subtype": "straight"})
    ch.add_series(series("LGD_TS", LGD_TS_COLUMNS, "lgd_final", T, "Lifetime (FINAL)", color=NAVY))
    ch.add_series(series("LGD_TS", LGD_TS_COLUMNS, "lgd_valuation_horizon", T,
                         f"Within {p['horizon2']} months", color="#2E75B6"))
    ch.add_series(series("LGD_TS", LGD_TS_COLUMNS, "lgd_short_horizon", T,
                         f"Within {p['horizon']} months", color="#7F7F7F"))
    ch.set_title({"name": "Horizon LGD vs lifetime LGD"})
    ch.set_x_axis({"name": "TermStep", "min": 0, "max": T})
    ch.set_y_axis({"name": "LGD", "num_format": "0.00"})
    ch.set_size({"width": 720, "height": 380})
    wsc.insert_chart("M23", ch)

    anchor = ["A43", "M43"]
    for (ts, c0, rows), cell in zip(blocks, anchor):
        ch = wb.add_chart({"type": "scatter", "subtype": "straight"})
        styles = [("Observed", "#000000", None), ("Exponential", "#2E75B6", None),
                  ("Power law", "#C55A11", None), ("Reference curve shape", "#548235", None)]
        for j, (name, color, _) in enumerate(styles, start=1):
            if name == "Reference curve shape" and cfg.get("client_cohort") is None:
                continue
            ch.add_series({
                "name": name, "categories": ["Chart_Data", 2, c0, 1 + rows, c0],
                "values": ["Chart_Data", 2, c0 + j, 1 + rows, c0 + j],
                "marker": {"type": "circle", "size": 3, "fill": {"color": color},
                           "border": {"color": color}} if name == "Observed" else {"type": "none"},
                "line": {"none": True} if name == "Observed" else {"width": 1.5, "color": color}})
        ch.set_title({"name": f"TermStep {ts}: RecoveryPct by bucket (log scale)"})
        ch.set_x_axis({"name": "Bucket", "min": 0})
        ch.set_y_axis({"name": "RecoveryPct", "log_base": 10, "num_format": "0.0000%"})
        ch.set_size({"width": 720, "height": 380})
        wsc.insert_chart(cell, ch)

    # ---------------------------------------------------------------- triangles
    _triangle(wb, f, "Hazard_Obs", "Observed RecoveryPct (cash in bucket b as % of balance at TermStep)",
              res.R, n, n, f["tri"])
    _triangle(wb, f, "Exposure_Obs", "Observed ExposureBucket (R) – the credibility base",
              res.E, n, n, f["money"])
    names = [("Ext_Exp", "Extended RecoveryPct – shape 1: exponential decay"),
             ("Ext_Power", "Extended RecoveryPct – shape 2: power law"),
             ("Ext_Ref", "Extended RecoveryPct – shape 3: reference curve")]
    for k, (name, title) in enumerate(names):
        if k == 2 and cfg.get("client_cohort") is None:
            continue
        _triangle(wb, f, name, title + " (observed to the last credible bucket, then scale × shape)",
                  res.ext[k], n, cols, f["tri"])

    wb.close()
    return buf.getvalue()


def cell(r: int, c: int) -> str:   # small helper used by tests
    return xl_rowcol_to_cell(r, c)
