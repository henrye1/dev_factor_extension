"""Column definitions shared by the CSV and Excel exports, CSV output and the
project summary workbook."""
from __future__ import annotations

import csv
import io

import xlsxwriter

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
    ("vintage_years", "Vintages within the last N years of the zip's latest vintage (blank = all)"),
]

CONFIG_LABELS = [
    ("rate", "Discount rate used"),
    ("rate_implied", "Rate implied by the file"),
    ("v", "Monthly discount factor v"),
    ("min_exposure_abs", "MinExposure applied (R)"),
    ("opening_exposure", "TermStep 1 opening exposure (R)"),
    ("last_obs_file", "Last observed bucket in file"),
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


def summary_workbook(project_name: str, matrix: dict) -> bytes:
    """Every scenario against every zip: headline LGD figures, one row per pair."""
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True, "strings_to_formulas": False, "strings_to_urls": False})
    ws = wb.add_worksheet("Summary")
    bold = wb.add_format({"bold": True})
    head = wb.add_format({"bold": True, "bg_color": "#1F3A5F", "font_color": "white",
                          "text_wrap": True, "valign": "top", "border": 1})
    lgd = wb.add_format({"num_format": "0.0000"})
    money = wb.add_format({"num_format": "#,##0"})
    ws.write_string(0, 0, f"LGD tail extension – {project_name}", bold)
    headings = ["Zip", "Category", "Scenario", "Status", "Stale", "Method", "Vintages",
                "Discount rate", "Exposure (R)", "Original LGD", "Replica LGD", "LGD exponential",
                "LGD power", "LGD log-normal", "LGD SELECTED", "Uplift (weighted)",
                "Uplift (simple)", "λ used", "γ used", "Warnings", "Error", "Computed at"]
    for c, h in enumerate(headings):
        ws.write(2, c, h, head)
    ds = {d["id"]: d for d in matrix["datasets"]}
    sc = {s["id"]: s for s in matrix["scenarios"]}
    row = 3
    for cell in matrix["cells"]:
        d, s = ds[cell["dataset_id"]], sc[cell["scenario_id"]]
        sm = cell.get("summary") or {}
        # write_string: names are user text and must never be parsed as formulas
        ws.write_string(row, 0, d["name"])
        ws.write_string(row, 1, d["category"])
        ws.write_string(row, 2, s["name"])
        ws.write_string(row, 3, {"none": "not run"}.get(cell["status"], cell["status"]))
        ws.write_string(row, 4, "yes" if cell.get("stale") else "")
        ws.write_string(row, 5, sm.get("method_label", ""))
        ws.write_string(row, 6, sm.get("vintages") or "")
        for c, (key, fmt) in enumerate([
                ("rate", lgd), ("exposure_total", money), ("lgd_file", lgd), ("lgd_replica", lgd),
                ("lgd_exp", lgd), ("lgd_power", lgd), ("lgd_logn", lgd), ("lgd_selected", lgd),
                ("uplift", lgd), ("uplift_simple", lgd), ("lam", lgd), ("gam", lgd)], start=7):
            if sm.get(key) is not None:
                ws.write_number(row, c, sm[key], fmt)
        if sm.get("warnings") is not None:
            ws.write_number(row, 19, sm["warnings"])
        ws.write_string(row, 20, cell.get("error", ""))
        ws.write_string(row, 21, cell.get("computed_at", "") or "")
        row += 1
    ws.set_row(2, 32)
    ws.set_column(0, 2, 18)
    ws.set_column(3, 21, 14)
    ws.freeze_panes(3, 3)
    wb.close()
    return buf.getvalue()
