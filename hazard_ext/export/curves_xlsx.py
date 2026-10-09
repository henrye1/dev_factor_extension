"""Curves workbook for one scenario: LGD and marginal recovery curves for every cohort.

Sheets
  Notes                   what each sheet holds and the scenario's parameters
  LGD_by_TermStep         final LGD by TermStep, one column per cohort, then LGD within the
                          valuation horizon per cohort
  Marginal_face           month since default down the side; per cohort our selected extended
                          curve from TermStep 1 (cash as % of the balance at default), then the
                          client's applied curve per cohort
  Marginal_outstanding    the same curves as a share of the balance still outstanding each month
  Cumulative_face         cumulative recovery of every curve on Marginal_face
  M_<cohort>              marginal recoveries by TermStep for the first K remaining steps (final basis)
"""
from __future__ import annotations

import math
import os
import re
import tempfile

import numpy as np
import xlsxwriter

from ..engine.applied import to_outstanding
from ..engine.core import ExtensionResult
from .summary_xlsx import final_marginals, vintage_text
from .tables import PARAM_LABELS

NAVY = "#1F3A5F"


def _num(ws, r, c, v, fmt=None):
    if v is None:
        return
    v = float(v)
    if math.isfinite(v):
        ws.write_number(r, c, v, fmt)


def _wide(ws, f, title, note, first_col, columns, fmt, row_fmt=None):
    """columns: list of (heading, 1-D array). Row i holds index i+1 of first_col."""
    ws.write_string(0, 0, title, f["title"])
    ws.write_string(1, 0, note, f["note"])
    ws.set_row(3, 48)
    ws.write_string(3, 0, first_col, f["head"])
    for c, (head, _) in enumerate(columns, start=1):
        ws.write_string(3, c, head, f["head"])
    n = max((len(v) for _, v in columns), default=0)
    for i in range(n):
        ws.write_number(4 + i, 0, i + 1, row_fmt)
        for c, (_, v) in enumerate(columns, start=1):
            if i < len(v):
                _num(ws, 4 + i, c, v[i], fmt)
    ws.set_column(0, 0, 12)
    ws.set_column(1, max(1, len(columns)), 17)
    ws.freeze_panes(4, 1)


def build_curves_workbook(project_name: str, scenario_name: str, items: list[dict],
                          applied: dict[str, np.ndarray], k_max: int = 120) -> bytes:
    """items: one per cohort with keys zip, category, stale, res (ExtensionResult), applied_label."""
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
            "tri": wb.add_format({"num_format": "0.00000%"}),
        }
        items = sorted(items, key=lambda it: it["zip"])

        # ---------------------------------------------------------------- Notes
        ws = wb.add_worksheet("Notes")
        ws.set_column(0, 0, 36)
        ws.set_column(1, 1, 110)
        ws.write_string(0, 0, f"{project_name} – {scenario_name}: LGD and recovery curves for every cohort", f["title"])
        lines = [
            ("LGD_by_TermStep", "Final LGD by TermStep per cohort (own extended row to LastTS, base row rolled forward beyond), then LGD within the valuation horizon."),
            ("Marginal_face", "Marginal recovery by month since default: cash in the month as a share of the balance at default. Our selected extended curve from TermStep 1 per cohort, and the client's applied curve where uploaded."),
            ("Marginal_outstanding", "The same curves as a share of the balance still outstanding at the start of each month (how a client who applies rates to the outstanding balance sees them)."),
            ("Cumulative_face", "Cumulative recovery of every curve on Marginal_face."),
            ("M_<cohort>", f"Marginal recoveries by TermStep for the first {k_max} remaining steps, final basis: cash in remaining step k as a share of the balance at the TermStep."),
            ("Out of date", "A cohort marked out of date was run before its scenario, override or zip data changed; run it again for current figures."),
        ]
        for i, (k, v) in enumerate(lines, start=2):
            ws.write_string(i, 0, k, f["bold"])
            ws.write_string(i, 1, v)
        r = len(lines) + 3
        ws.write_string(r, 0, "Scenario parameters", f["bold"])
        params = items[0]["res"].params if items else {}
        for key, label in PARAM_LABELS:
            r += 1
            ws.write_string(r, 0, label)
            v = params.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                ws.write_number(r, 1, v)
            else:
                ws.write_string(r, 1, "" if v is None else str(v))
        r += 2
        ws.write_string(r, 0, "Cohort", f["head"])
        ws.write_string(r, 1, "Method, vintages, client applied curve, status", f["head"])
        for it in items:
            r += 1
            cfg = it["res"].config
            ws.write_string(r, 0, it["zip"])
            ws.write_string(r, 1, f"{cfg['method_label']}; {vintage_text(cfg).lower()}; "
                                  f"client applied curve {it.get('applied_label') or 'none'}; "
                                  f"{'out of date' if it['stale'] else 'current'}")

        # ------------------------------------------------------- LGD_by_TermStep
        ws = wb.add_worksheet("LGD_by_TermStep")
        cols = [(f"{it['zip']} – final LGD", np.asarray(it["res"].lgd_ts["lgd_final"], dtype=float)) for it in items]
        cols += [(f"{it['zip']} – LGD within {it['res'].params['horizon2']} months", np.asarray(it["res"].lgd_ts["lgd_valuation_horizon"], dtype=float)) for it in items]
        _wide(ws, f, f"{scenario_name}: LGD by TermStep", "Final LGD per cohort, then LGD within the valuation horizon per cohort.",
              "TermStep", cols, f["lgd"])

        # ------------------------------------------------- marginal curves
        face_cols = []
        for it in items:
            res: ExtensionResult = it["res"]
            sel = int(res.config["method"]) - 1
            mb = int(res.config["max_bucket"])
            face_cols.append((f"{it['zip']} – ours ({res.config['method_label']})", res.ext[sel, 1, 1:mb + 1].copy()))
        for it in items:
            ap = it.get("applied_label")
            if ap and ap in applied:
                face_cols.append((f"{it['zip']} – client applied {ap}", np.asarray(applied[ap], dtype=float)))

        ws = wb.add_worksheet("Marginal_face")
        _wide(ws, f, f"{scenario_name}: marginal recovery by month since default, face basis",
              "Cash in the month as a share of the balance at default. Our curve is the selected extended row of TermStep 1.",
              "Month", face_cols, f["tri"])
        ws = wb.add_worksheet("Marginal_outstanding")
        _wide(ws, f, f"{scenario_name}: marginal recovery by month since default, outstanding basis",
              "Cash in the month as a share of the balance still outstanding at the start of that month. Blank once nothing is outstanding.",
              "Month", [(h_, to_outstanding(v)) for h_, v in face_cols], f["tri"])
        ws = wb.add_worksheet("Cumulative_face")
        _wide(ws, f, f"{scenario_name}: cumulative recovery by month since default",
              "Running total of Marginal_face, as a share of the balance at default.",
              "Month", [(h_, np.cumsum(np.where(np.isfinite(v), v, 0.0))) for h_, v in face_cols], f["lgd"])

        # ------------------------------------------------- per-cohort marginals
        used: set = set()
        for it in items:
            res = it["res"]
            raw = "M_" + re.sub(r"[^A-Za-z0-9]+", "", it["zip"])[:20]
            name, i = raw, 2
            while name in used:
                name = f"{raw}_{i}"
                i += 1
            used.add(name)
            ws = wb.add_worksheet(name)
            M = final_marginals(res, k_max)
            L = res.lgd_ts
            ws.write_string(0, 0, f"{it['zip']} under {scenario_name}: marginal recoveries by TermStep for the remaining steps "
                                  f"(final basis: own extended row to LastTS {res.config['last_ts']}, base row rolled forward beyond). "
                                  f"Cash in remaining step k as % of the balance at the TermStep; blank past MaxBucket {res.config['max_bucket']}.", f["note"])
            ws.write_string(1, 0, "TermStep", f["head"])
            ws.write_string(1, 1, "Source", f["head"])
            ws.write_string(1, 2, "LGD final", f["head"])
            ws.write_string(1, 3, f"Σ steps 1–{k_max}", f["head"])
            for k in range(k_max):
                ws.write_number(1, 4 + k, k + 1, f["head"])
            for i in range(M.shape[0]):
                ws.write_number(2 + i, 0, i + 1)
                ws.write_string(2 + i, 1, str(L["source"][i]))
                _num(ws, 2 + i, 2, L["lgd_final"][i], f["lgd"])
                row = M[i]
                if np.isfinite(row).any():
                    _num(ws, 2 + i, 3, float(np.nansum(row)), f["lgd"])
                    ws.write_row(2 + i, 4, [x if math.isfinite(x) else "" for x in row.tolist()], f["tri"])
            ws.set_column(0, 1, 9)
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
