"""Extract golden fixtures from the example framework workbooks.

Reads the cached values of each example workbook and stores the inputs
(Raw_Debug, Config) and the expected outputs (Results, LGD_300, Tail_Fit,
Client_Curve) as a compact .npz used by the engine golden tests.

Usage:  python scripts/extract_golden.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import openpyxl

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "golden"

BOOKS = {
    "vb44": "Nutun_HazardRate_Bucket_Extension_Framework_VB44_1.xlsx",
    "vb22": "Nutun_HazardRate_Bucket_Extension_Framework_VB22.xlsx",
}


def _num(v):
    """Blank / text cells become nan."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else np.nan


def _block(ws, min_row, max_row, min_col, max_col):
    rows = ws.iter_rows(min_row=min_row, max_row=max_row, min_col=min_col,
                        max_col=max_col, values_only=True)
    return [list(r) for r in rows]


def extract(key: str, filename: str) -> None:
    wb = openpyxl.load_workbook(ROOT / filename, read_only=True, data_only=True)

    raw = [r for r in _block(wb["Raw_Debug"], 4, None, 1, 13) if r[0] is not None]
    events = np.array([r[0] for r in raw])
    raw_num = np.array([[_num(v) for v in r[1:]] for r in raw], dtype=float)

    cfg_rows = _block(wb["Config"], 4, 30, 1, 2)
    config = {str(r[0]): r[1] for r in cfg_rows if r[0] is not None}

    n = int(np.nanmax(raw_num[:, 0]))  # highest TermStep

    res = _block(wb["Results"], 10, 9 + n, 1, 17)
    results = np.array([[_num(v) for v in r] for r in res], dtype=float)
    avg = [_num(v) for v in _block(wb["Results"], 6, 6, 2, 8)[0]]
    r7 = _block(wb["Results"], 7, 8, 1, 4)
    extra = [_num(r7[0][1]), _num(r7[1][1]), _num(r7[0][3])]  # tie max, tie min, simple avg uplift

    lgd = _block(wb["LGD_300"], 13, 312, 1, 15)
    lgd_num = np.array([[_num(v) for v in r[:14]] for r in lgd], dtype=float)
    lgd_src = np.array([str(r[14]) for r in lgd])
    lgd_head = _block(wb["LGD_300"], 10, 11, 2, 3)
    lgd_summary = [_num(lgd_head[0][0]), _num(lgd_head[0][1]), _num(lgd_head[1][0])]

    tail = _block(wb["Tail_Fit"], 15, 14 + n, 1, 10)
    tail_num = np.array([[_num(v) for v in r] for r in tail], dtype=float)

    cc = _block(wb["Client_Curve"], 3, 556, 1, 7)
    cohorts = [str(v) for v in cc[0][1:]]
    curves = np.array([[_num(v) if v is not None else 0.0 for v in r] for r in cc[1:]], dtype=float)

    OUT.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        OUT / f"{key}.npz",
        events=events, raw=raw_num,
        config=json.dumps(config, default=str),
        results=results, averages=np.array(avg), extra=np.array(extra),
        lgd=lgd_num, lgd_src=lgd_src, lgd_summary=np.array(lgd_summary),
        tail=tail_num, cohorts=np.array(cohorts), curves=curves,
    )
    print(key, "rows", len(raw), "n", n, "->", OUT / f"{key}.npz")


if __name__ == "__main__":
    for k, f in BOOKS.items():
        extract(k, f)
