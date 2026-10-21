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
import zipfile

import numpy as np

from ..engine.core import ExtensionResult
from ._xlsx import build_xlsx, formats, safe_name, write_cells, write_headings, write_num
from .tables import CONFIG_LABELS, LGD_TS_COLUMNS, PARAM_LABELS, RESULT_COLUMNS, TAIL_COLUMNS, headed, vintage_text
from .values_xlsx import build_values_workbook

SUMMARY_HEADINGS = [
    "Zip", "Category", "Current", "Method", "Vintages", "Observed TermSteps", "Last observed bucket",
    "Σ exposure at ts (R)", "Discount rate", "Original LGD", "Replica LGD", "LGD exponential",
    "LGD power law", "LGD log-normal", "LGD SELECTED", "Uplift (weighted)", "Uplift (simple)",
    "λ used", "γ used", "Log-normal μ used", "Log-normal σ used", "Half-life (buckets)",
    "Reference row last credible bucket", "LastTS applied", "Tie-out (max abs)",
    "File LGD floored (TermSteps)", "Warnings", "Computed at"]
_SUMMARY_FMT = {5: "int", 6: "int", 7: "money", 8: "pct", 21: "lgd", 22: "int", 23: "int",
                24: "sci", 25: "int", 26: "int"}          # every other number: lgd


def _long_table(wb, f, name, title, items, columns, key) -> None:
    """One sheet with the table ``key`` of every cohort stacked, two label columns in front."""
    ws = wb.add_worksheet(name)
    ws.write_string(0, 0, title, f["title"])
    heads = ["Zip", "Category"] + [h for _, h, _ in columns]
    write_headings(ws, 2, heads, f["head"], 48)
    r = 3
    for it in items:
        data = getattr(it["res"], key)
        cols = headed(columns, it["res"].config)
        for i in range(len(data[cols[0][0]])):
            ws.write_string(r, 0, it["zip"])
            ws.write_string(r, 1, it["category"])
            for c, (k, _, fmt) in enumerate(cols, start=2):
                v = data[k][i]
                v = v.item() if hasattr(v, "item") else v
                if isinstance(v, str):
                    ws.write_string(r, c, v)
                else:
                    write_num(ws, r, c, v, f.get(fmt))
            r += 1
    ws.set_column(0, 1, 14)
    ws.set_column(2, len(heads) - 1, 15)
    ws.freeze_panes(3, 3)
    ws.autofilter(2, 0, max(r - 1, 3), len(heads) - 1)


def build_results_workbook(project_name: str, scenario_name: str, items: list[dict]) -> bytes:
    """items: one per cohort with keys zip, category, stale, computed_at, res (ExtensionResult)."""
    items = sorted(items, key=lambda it: it["zip"])
    return build_xlsx(lambda wb: _fill(wb, project_name, scenario_name, items))


def _fill(wb, project_name, scenario_name, items) -> None:
    f = formats(wb)

    # ------------------------------------------------------------ Summary
    ws = wb.add_worksheet("Summary")
    ws.write_string(0, 0, f"{project_name} – {scenario_name}: results for every cohort", f["title"])
    ws.write_string(1, 0, "Exposure-weighted figures over all observed TermSteps of each cohort. The other sheets hold "
                          "every cohort's full tables, with Zip and Category in the first two columns for filtering.",
                    f["note"])
    write_headings(ws, 3, SUMMARY_HEADINGS, f["head"], 48)
    for i, it in enumerate(items):
        res: ExtensionResult = it["res"]
        cfg, avg = res.config, res.averages
        row = [it["zip"], it["category"], "" if it["stale"] else "yes", cfg["method_label"], vintage_text(cfg),
               res.n, cfg["last_obs_file"], avg["exposure_total"], cfg["rate"], avg["lgd_file"], avg["lgd_replica"],
               avg["lgd_exp"], avg["lgd_power"], avg["lgd_logn"], avg["lgd_selected"], avg["uplift"],
               avg["uplift_simple"], cfg["lam"], cfg["gam"], cfg["mu"], cfg["sigma"], cfg["half_life"],
               cfg["ref_last_cred"], cfg["last_ts"], max(abs(avg["tie_max"]), abs(avg["tie_min"])),
               avg.get("file_floored_count", 0), len(res.warnings), it.get("computed_at") or ""]
        write_cells(ws, 4 + i, row, lambda c: f[_SUMMARY_FMT.get(c, "lgd")])
    ws.set_column(0, 1, 14)
    ws.set_column(2, len(SUMMARY_HEADINGS) - 1, 13)
    ws.freeze_panes(4, 2)
    ws.autofilter(3, 0, max(3 + len(items), 4), len(SUMMARY_HEADINGS) - 1)

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
            if isinstance(v, (bool, np.bool_, int, float, np.integer, np.floating)):
                write_num(ws, r, c, v)
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
        for w in it["res"].warnings:
            ws.write_string(r, 0, it["zip"])
            ws.write_string(r, 1, w)
            r += 1
    if r == 1:
        ws.write_string(1, 0, "None")
    ws.set_column(0, 0, 14)
    ws.set_column(1, 1, 140)


def build_results_bundle(scenario_name: str, items: list[dict]) -> bytes:
    """A zip holding the values workbook of every cohort under the scenario."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for it in sorted(items, key=lambda it: it["zip"]):
            name = f"{safe_name(it['zip'])}_{safe_name(scenario_name)}_values.xlsx"
            zf.writestr(name, build_values_workbook(it["res"], it["zip"], scenario_name, it.get("filename", "")))
    return buf.getvalue()
