"""Curves workbook for one scenario: LGD and marginal recovery curves for every cohort.

Sheets
  Notes                   what each sheet holds and the scenario's parameters
  Curve_parameters        per cohort: the parameters behind each forecast curve (fitted and used
                          λ, γ, μ, σ, the fit window, the level on TermStep 1 and the base row)
  Scale_by_TermStep       the level (scale) of each shape on every TermStep's anchor window
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
from ..engine.core import ExtensionResult, curve_parameters
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
            "num": wb.add_format({"num_format": "0.000000"}),
        }
        items = sorted(items, key=lambda it: it["zip"])

        # ---------------------------------------------------------------- Notes
        ws = wb.add_worksheet("Notes")
        ws.set_column(0, 0, 36)
        ws.set_column(1, 1, 110)
        ws.write_string(0, 0, f"{project_name} – {scenario_name}: LGD and recovery curves for every cohort", f["title"])
        lines = [
            ("Curve_parameters", "The parameters behind each cohort's forecast curves: fitted and used λ, γ, μ and σ, the regression window, and the level (scale) that anchors each shape on TermStep 1 and on the base row. Beyond the last credible bucket, RecoveryPct(ts, b) = max(floor, scale(ts) × shape(b))."),
            ("Scale_by_TermStep", "The scale of each shape on every TermStep: Σ observed RecoveryPct over the anchor window ÷ Σ shape over the window."),
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

        # ------------------------------------------------------ Curve_parameters
        ws = wb.add_worksheet("Curve_parameters")
        ws.write_string(0, 0, f"{scenario_name}: the parameters behind every cohort's forecast curves", f["title"])
        ws.write_string(1, 0, "Shapes: exponential e^(−λb); power law b^(−γ); log-normal (1/b)·exp(−(ln b − μ)²/(2σ²)). Each is fitted on "
                              "the reference row from FitStart to its last credible bucket and scaled to the anchor window of each "
                              "TermStep. 'Used' differs from 'fitted' where the scenario set an override.", f["note"])
        heads = ["Cohort", "Selected method", "Vintages", "Reference TermStep", "FitStart", "Fit to bucket", "Fit points",
                 "λ fitted", "λ used", "λ override", "Half-life (buckets)", "γ fitted", "γ used", "γ override",
                 "Log-normal points", "μ fitted", "μ used", "μ override", "σ fitted", "σ used", "σ override",
                 "Log-normal peak bucket", "Log-normal median bucket", "Floor",
                 "Scale exponential, TermStep 1", "Scale power, TermStep 1", "Scale log-normal, TermStep 1",
                 "Base TermStep", "Scale exponential, base row", "Scale power, base row", "Scale log-normal, base row",
                 "Last credible bucket, TermStep 1"]
        ws.set_row(3, 48)
        for c, h_ in enumerate(heads):
            ws.write_string(3, c, h_, f["head"])
        for i, it in enumerate(items):
            res = it["res"]
            cp = {row_["shape"]: row_ for row_ in curve_parameters(res, 1)}
            e_, pw_, ln_ = cp["exp"], cp["power"], cp["logn"]
            cfg = res.config
            row_ = [it["zip"], cfg["method_label"], vintage_text(cfg), e_["fit_window"]["ref_ts"], e_["fit_window"]["from"],
                    e_["fit_window"]["to"], e_["fit_window"]["points"],
                    e_["params"][0]["fitted"], e_["params"][0]["used"], "yes" if e_["params"][0]["override"] else "",
                    e_["derived"]["half_life"], pw_["params"][0]["fitted"], pw_["params"][0]["used"],
                    "yes" if pw_["params"][0]["override"] else "", ln_["fit_window"]["points"],
                    ln_["params"][0]["fitted"], ln_["params"][0]["used"], "yes" if ln_["params"][0]["override"] else "",
                    ln_["params"][1]["fitted"], ln_["params"][1]["used"], "yes" if ln_["params"][1]["override"] else "",
                    ln_["derived"]["peak_bucket"], ln_["derived"]["median_bucket"], e_["floor"],
                    e_["scale_ts"], pw_["scale_ts"], ln_["scale_ts"], int(res.params["base_ts"]),
                    e_["scale_base"], pw_["scale_base"], ln_["scale_base"], e_["last_cred_ts"]]
            for c, v in enumerate(row_):
                if isinstance(v, str):
                    ws.write_string(4 + i, c, v)
                else:
                    _num(ws, 4 + i, c, v, None if c in (3, 4, 5, 6, 14, 27, 31) else f["num"])
        ws.set_column(0, 0, 12)
        ws.set_column(1, 2, 22)
        ws.set_column(3, len(heads) - 1, 14)
        ws.freeze_panes(4, 1)

        # ----------------------------------------------------- Scale_by_TermStep
        ws = wb.add_worksheet("Scale_by_TermStep")
        scale_cols = []
        for it in items:
            for key, lab in (("scale_exp", "exponential"), ("scale_power", "power"), ("scale_logn", "log-normal")):
                scale_cols.append((f"{it['zip']} – scale {lab}", np.asarray(it["res"].tail_fit[key], dtype=float)))
        _wide(ws, f, f"{scenario_name}: scale of each shape by TermStep",
              "The level that anchors the shape on the TermStep's window of credible buckets; blank where the shape is undefined.",
              "TermStep", scale_cols, f["num"])

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
