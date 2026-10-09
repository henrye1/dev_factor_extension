"""Every cohort's results under one scenario, in one workbook or as a zip of workbooks.

Workbook sheets
  Summary              one row per cohort: method, vintages, exposure, the headline LGDs, decay
                       parameters, tie-out, warnings, whether the figures are current
  Results_by_TermStep  the Results table of every cohort, long form (Zip, Category, TermStep, …)
  LGD_to_Target        the LGD-to-Target table of every cohort, long form
  Tail_fit             the tail-fit table of every cohort, long form
  Parameters           the effective parameters per cohort (scenario values with the cohort's
                       overrides applied), one column per cohort, and the derived Config values
  Warnings             every warning the engine raised, by cohort

The zip bundle holds the values workbook of each cohort, named <zip>_<scenario>_values.xlsx.
"""
from __future__ import annotations

import io
import math
import os
import re
import tempfile
import zipfile

import numpy as np
import xlsxwriter

from ..engine.core import ExtensionResult
from .summary_xlsx import vintage_text
from .tables import (CONFIG_LABELS, LGD_TS_COLUMNS, PARAM_LABELS, RESULT_COLUMNS, TAIL_COLUMNS, headed)
from .values_xlsx import build_values_workbook

NAVY = "#1F3A5F"


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "export"


def _num(ws, r, c, v, fmt=None):
    if v is None:
        return
    if isinstance(v, (bool, np.bool_)):
        ws.write_boolean(r, c, bool(v))
        return
    v = float(v)
    if math.isfinite(v):
        ws.write_number(r, c, v, fmt)


def _long_table(wb, f, name, title, items, columns, key):
    """One sheet with the table ``key`` of every cohort stacked, two label columns in front."""
    ws = wb.add_worksheet(name)
    ws.write_string(0, 0, title, f["title"])
    heads = ["Zip", "Category"] + [h_ for _, h_, _ in columns]
    ws.set_row(2, 48)
    for c, h_ in enumerate(heads):
        ws.write_string(2, c, h_, f["head"])
    r = 3
    for it in items:
        data = getattr(it["res"], key)
        cols = headed(columns, it["res"].config)
        n = len(data[cols[0][0]])
        for i in range(n):
            ws.write_string(r, 0, it["zip"])
            ws.write_string(r, 1, it["category"])
            for c, (k, _, fmt) in enumerate(cols, start=2):
                v = data[k][i]
                v = v.item() if hasattr(v, "item") else v
                if isinstance(v, str):
                    ws.write_string(r, c, v)
                else:
                    _num(ws, r, c, v, f.get(fmt))
            r += 1
    ws.set_column(0, 1, 14)
    ws.set_column(2, len(heads) - 1, 15)
    ws.freeze_panes(3, 3)
    ws.autofilter(2, 0, max(r - 1, 3), len(heads) - 1)


def build_results_workbook(project_name: str, scenario_name: str, items: list[dict]) -> bytes:
    """items: one per cohort with keys zip, category, stale, computed_at, res (ExtensionResult)."""
    items = sorted(items, key=lambda it: it["zip"])
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
            "int": wb.add_format({"num_format": "0"}),
            "money": wb.add_format({"num_format": "#,##0"}),
            "lgd": wb.add_format({"num_format": "0.0000"}),
            "lgd6": wb.add_format({"num_format": "0.000000"}),
            "sci": wb.add_format({"num_format": "0.00E+00"}),
            "num": wb.add_format({"num_format": "0.000000"}),
            "pct": wb.add_format({"num_format": "0.00%"}),
            "text": wb.add_format({}),
        }

        # ------------------------------------------------------------ Summary
        ws = wb.add_worksheet("Summary")
        ws.write_string(0, 0, f"{project_name} – {scenario_name}: results for every cohort", f["title"])
        ws.write_string(1, 0, "Exposure-weighted figures over all observed TermSteps of each cohort. The other sheets hold "
                              "every cohort's full tables, with Zip and Category in the first two columns for filtering.", f["note"])
        heads = ["Zip", "Category", "Current", "Method", "Vintages", "Observed TermSteps", "Last observed bucket",
                 "Σ exposure at ts (R)", "Discount rate", "Original LGD", "Replica LGD", "LGD exponential",
                 "LGD power law", "LGD log-normal", "LGD SELECTED", "Uplift (weighted)", "Uplift (simple)",
                 "λ used", "γ used", "Log-normal μ used", "Log-normal σ used", "Half-life (buckets)",
                 "Reference row last credible bucket", "LastTS applied", "Tie-out (max abs)",
                 "File LGD floored (TermSteps)", "Warnings", "Computed at"]
        ws.set_row(3, 48)
        for c, h_ in enumerate(heads):
            ws.write_string(3, c, h_, f["head"])
        fmts = {5: f["int"], 6: f["int"], 7: f["money"], 8: f["pct"], 21: f["lgd"], 22: f["int"], 23: f["int"],
                24: f["sci"], 25: f["int"], 26: f["int"]}
        for i, it in enumerate(items):
            res: ExtensionResult = it["res"]
            cfg, avg = res.config, res.averages
            row = [it["zip"], it["category"], "" if it["stale"] else "yes", cfg["method_label"], vintage_text(cfg),
                   res.n, cfg["last_obs_file"], avg["exposure_total"], cfg["rate"], avg["lgd_file"], avg["lgd_replica"],
                   avg["lgd_exp"], avg["lgd_power"], avg["lgd_logn"], avg["lgd_selected"], avg["uplift"],
                   avg["uplift_simple"], cfg["lam"], cfg["gam"], cfg["mu"], cfg["sigma"], cfg["half_life"],
                   cfg["ref_last_cred"], cfg["last_ts"], max(abs(avg["tie_max"]), abs(avg["tie_min"])),
                   avg.get("file_floored_count", 0), len(res.warnings), it.get("computed_at") or ""]
            for c, v in enumerate(row):
                if isinstance(v, str):
                    ws.write_string(4 + i, c, v)
                else:
                    _num(ws, 4 + i, c, v, fmts.get(c, f["lgd"]))
        ws.set_column(0, 1, 14)
        ws.set_column(2, len(heads) - 1, 13)
        ws.freeze_panes(4, 2)
        ws.autofilter(3, 0, max(3 + len(items), 4), len(heads) - 1)

        # ------------------------------------------------------- long tables
        _long_table(wb, f, "Results_by_TermStep", f"{scenario_name}: Results by TermStep for every cohort",
                    items, RESULT_COLUMNS, "results")
        _long_table(wb, f, "LGD_to_Target", f"{scenario_name}: LGD to the Target TermStep for every cohort",
                    items, LGD_TS_COLUMNS, "lgd_ts")
        _long_table(wb, f, "Tail_fit", f"{scenario_name}: tail fit for every cohort", items, TAIL_COLUMNS, "tail_fit")

        # --------------------------------------------------------- Parameters
        ws = wb.add_worksheet("Parameters")
        ws.write_string(0, 0, "Effective parameters per cohort (the scenario's values with the cohort's overrides applied), "
                              "then the values the engine derived.", f["note"])
        ws.write_string(2, 0, "Parameter", f["head"])
        for c, it in enumerate(items, start=1):
            ws.write_string(2, c, it["zip"], f["head"])
        r = 3
        for key, label in PARAM_LABELS:
            ws.write_string(r, 0, label)
            for c, it in enumerate(items, start=1):
                v = it["res"].params.get(key)
                if v is None:
                    continue
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    ws.write_number(r, c, v)
                else:
                    ws.write_string(r, c, str(v))
            r += 1
        r += 1
        ws.write_string(r, 0, "Derived", f["bold"])
        r += 1
        for key, label in CONFIG_LABELS:
            ws.write_string(r, 0, label)
            for c, it in enumerate(items, start=1):
                v = it["res"].config.get(key)
                if v is None:
                    continue
                if isinstance(v, (bool, np.bool_)):
                    ws.write_boolean(r, c, bool(v))
                elif isinstance(v, (int, float, np.integer, np.floating)):
                    _num(ws, r, c, v)
                else:
                    ws.write_string(r, c, str(v))
            r += 1
        ws.set_column(0, 0, 62)
        ws.set_column(1, max(1, len(items)), 16)
        ws.freeze_panes(3, 1)

        # ----------------------------------------------------------- Warnings
        ws = wb.add_worksheet("Warnings")
        ws.write_string(0, 0, "Zip", f["head"])
        ws.write_string(0, 1, "Warning", f["head"])
        r = 1
        for it in items:
            for w_ in it["res"].warnings:
                ws.write_string(r, 0, it["zip"])
                ws.write_string(r, 1, w_)
                r += 1
        if r == 1:
            ws.write_string(1, 0, "None")
        ws.set_column(0, 0, 14)
        ws.set_column(1, 1, 140)

        wb.close()
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def build_results_bundle(scenario_name: str, items: list[dict]) -> bytes:
    """A zip holding the values workbook of every cohort under the scenario."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for it in sorted(items, key=lambda it: it["zip"]):
            name = f"{_safe(it['zip'])}_{_safe(scenario_name)}_values.xlsx"
            zf.writestr(name, build_values_workbook(it["res"], it["zip"], scenario_name, it.get("filename", "")))
    return buf.getvalue()
