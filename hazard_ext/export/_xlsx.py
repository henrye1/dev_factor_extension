"""Helpers shared by every Excel export: workbook creation, cell formats, safe cell writing,
sheet names, and the marginal-recoveries sheet that two workbooks carry."""
from __future__ import annotations

import io
import math
import os
import re
import tempfile
from typing import Callable

import numpy as np
import xlsxwriter

from .tables import final_marginals

NAVY = "#1F3A5F"

# Every format any export uses, by name. Each workbook creates the set once with formats().
FORMAT_SPECS: dict[str, dict] = {
    "title": {"bold": True, "font_size": 14, "font_color": NAVY},
    "bold": {"bold": True},
    "note": {"italic": True, "font_color": "#555555"},
    "wrap": {"text_wrap": True, "valign": "top"},
    "warn": {"font_color": "#9C5700", "bg_color": "#FFEB9C", "text_wrap": True},
    "head": {"bold": True, "bg_color": NAVY, "font_color": "white", "text_wrap": True, "valign": "top", "border": 1},
    "input": {"font_color": "#0000FF", "bg_color": "#DDEBF7", "border": 1},
    "input_money": {"font_color": "#0000FF", "bg_color": "#DDEBF7", "border": 1, "num_format": "#,##0"},
    "input_pct": {"font_color": "#0000FF", "bg_color": "#DDEBF7", "border": 1, "num_format": "0.00%"},
    "int": {"num_format": "0"},
    "money": {"num_format": "#,##0"},
    "pct": {"num_format": "0.00%"},
    "lgd": {"num_format": "0.0000"},
    "lgd_b": {"num_format": "0.0000", "bold": True},
    "lgd6": {"num_format": "0.000000"},
    "num": {"num_format": "0.000000"},
    "sci": {"num_format": "0.00E+00"},
    "tri": {"num_format": "0.00000%"},
    "text": {},
}

# plain write() never turns text into a formula or a link; formulas go through write_formula
_SAFE_OPTIONS = {"nan_inf_to_errors": True, "strings_to_formulas": False, "strings_to_urls": False}


def formats(wb, **overrides: dict) -> dict:
    """Create the named formats on ``wb``. Keyword arguments replace or add specs by name."""
    specs = {**FORMAT_SPECS, **overrides}
    return {name: wb.add_format(spec) for name, spec in specs.items()}


def build_xlsx(fill: Callable[[xlsxwriter.Workbook], None], *, streaming: bool = True) -> bytes:
    """Run ``fill(wb)`` on a new workbook and return the file's bytes.

    streaming=True writes through a temporary file in xlsxwriter's constant-memory mode, which
    keeps large workbooks small in memory but needs every sheet written top to bottom.
    streaming=False builds the workbook in memory and allows cells in any order."""
    if not streaming:
        buf = io.BytesIO()
        wb = xlsxwriter.Workbook(buf, {"in_memory": True, **_SAFE_OPTIONS})
        fill(wb)
        wb.close()
        return buf.getvalue()
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    try:
        wb = xlsxwriter.Workbook(path, {"constant_memory": True, **_SAFE_OPTIONS})
        fill(wb)
        wb.close()
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def write_num(ws, r: int, c: int, v, fmt=None) -> None:
    """A number, or nothing when the value is missing or not finite. Booleans stay booleans."""
    if v is None:
        return
    if isinstance(v, (bool, np.bool_)):
        ws.write_boolean(r, c, bool(v))
        return
    v = float(v)
    if math.isfinite(v):
        ws.write_number(r, c, v, fmt)


def write_value(ws, r: int, c: int, v, fmt=None) -> None:
    """Any value: numbers as numbers, booleans as booleans, everything else as text (never a formula)."""
    if v is None:
        return
    if isinstance(v, (float, np.floating)):
        if math.isfinite(v):
            ws.write_number(r, c, float(v), fmt)
    elif isinstance(v, (bool, np.bool_)):
        ws.write_boolean(r, c, bool(v), fmt)
    elif isinstance(v, (int, np.integer)):
        ws.write_number(r, c, int(v), fmt)
    else:
        ws.write_string(r, c, str(v), fmt)


def write_cells(ws, r: int, values: list, num_fmt: Callable[[int], object]) -> None:
    """One row: text as text, anything else through write_num with the format for its column."""
    for c, v in enumerate(values):
        if isinstance(v, str):
            ws.write_string(r, c, v)
        else:
            write_num(ws, r, c, v, num_fmt(c))


def write_headings(ws, r: int, headings: list[str], fmt, height: int | None = None) -> None:
    if height:
        ws.set_row(r, height)
    for c, h in enumerate(headings):
        ws.write_string(r, c, h, fmt)


def safe_name(name: str) -> str:
    """A file-name-safe version of a user-supplied name."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "export"


def unique_sheet_name(used: set, raw: str) -> str:
    """``raw`` cut to Excel's 31 characters and made unique with _2, _3, ..."""
    name = raw[:31] or "sheet"
    i = 2
    while name in used:
        suffix = f"_{i}"
        name = raw[:31 - len(suffix)] + suffix
        i += 1
    used.add(name)
    return name


def write_marginals_sheet(ws, f: dict, title: str, res, k_max: int) -> None:
    """Marginal recoveries by TermStep for the first ``k_max`` remaining steps, final basis."""
    M = final_marginals(res, k_max)
    L = res.lgd_ts
    ws.write_string(0, 0, title, f["note"])
    ws.write_string(1, 0, "TermStep", f["head"])
    ws.write_string(1, 1, "Source", f["head"])
    ws.write_string(1, 2, "LGD final", f["head"])
    ws.write_string(1, 3, f"Σ steps 1–{k_max}", f["head"])
    for k in range(k_max):
        ws.write_number(1, 4 + k, k + 1, f["head"])
    for i in range(M.shape[0]):
        ws.write_number(2 + i, 0, i + 1)
        ws.write_string(2 + i, 1, str(L["source"][i]))
        write_num(ws, 2 + i, 2, L["lgd_final"][i], f["lgd"])
        row = M[i]
        if np.isfinite(row).any():
            write_num(ws, 2 + i, 3, float(np.nansum(row)), f["lgd"])
            ws.write_row(2 + i, 4, [x if math.isfinite(x) else "" for x in row.tolist()], f["tri"])
    ws.set_column(0, 1, 9)
    ws.set_column(2, 3, 11)
    ws.set_column(4, 3 + k_max, 9)
    ws.freeze_panes(2, 4)


def marginals_title(label: str, res) -> str:
    return (f"{label}: marginal recoveries by TermStep for the remaining steps (final basis: own extended row "
            f"to LastTS {res.config['last_ts']}, base row rolled forward beyond). Cash in remaining step k as % "
            f"of the balance at the TermStep; blank past MaxBucket {res.config['max_bucket']}.")
