"""Reference curves used as tail shape 3.

A curve is a monthly recovery rate as a fraction of the balance at default by month since
default, t = 1, 2, ... There are no built-in curves: method 3 uses only the curves uploaded to
the project, so one client's curves never reach another client's project.
"""
from __future__ import annotations

import io

import numpy as np
import pandas as pd

class CurveError(ValueError):
    pass


def _frame_to_curves(df: pd.DataFrame) -> dict[str, np.ndarray]:
    if df.shape[1] < 2:
        raise CurveError("A curve file needs a 't' column and at least one curve column")
    if df.shape[1] > 51:
        raise CurveError("A curve file may hold at most 50 curves")
    df = df.dropna(how="all")
    t_col = df.columns[0]
    try:
        t = df[t_col].astype(float).to_numpy()
    except (TypeError, ValueError):
        raise CurveError("The first column must hold the month index t = 1, 2, 3, ...") from None
    if len(t) == 0 or not np.array_equal(t, np.arange(1, len(t) + 1)):
        raise CurveError("The first column must be t = 1, 2, 3, ... with no gaps")
    out: dict[str, np.ndarray] = {}
    for col in df.columns[1:]:
        label = str(col).strip()
        if label.endswith(".0"):
            label = label[:-2]
        try:
            vals = df[col].fillna(0.0).astype(float).to_numpy()
        except (TypeError, ValueError):
            raise CurveError(f"Curve '{label}' has non-numeric values") from None
        if not np.isfinite(vals).all():
            raise CurveError(f"Curve '{label}' has values that are not finite numbers")
        if (vals < 0).any():
            raise CurveError(f"Curve '{label}' has negative values")
        if len(label) > 100:
            raise CurveError("Curve labels are limited to 100 characters")
        if len(vals) > 5000:
            raise CurveError("Curves are limited to 5,000 months")
        if not label:
            raise CurveError("Every curve column needs a header label")
        out[label] = vals
    return out


def parse_curve_file(data: bytes, filename: str) -> dict[str, np.ndarray]:
    """Read an uploaded curve file: CSV or xlsx, first column t, one column per curve."""
    name = filename.lower()
    try:
        if name.endswith((".xlsx", ".xlsm")):
            df = pd.read_excel(io.BytesIO(data), engine="openpyxl")
        elif name.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(data), float_precision="round_trip")
        else:
            raise CurveError("Curve files must be .csv or .xlsx")
    except CurveError:
        raise
    except Exception as exc:
        raise CurveError(f"The curve file could not be read: {exc}") from None
    return _frame_to_curves(df)
