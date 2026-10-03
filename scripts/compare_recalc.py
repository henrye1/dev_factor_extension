"""Compare Excel's recalculated values (scripts/excel_recalc.ps1) with the engine's.

Usage:  python scripts/compare_recalc.py <expected.json> <recalc.json>
"""
from __future__ import annotations

import json
import sys

import numpy as np


def main(expected_path: str, recalc_path: str) -> int:
    exp = json.load(open(expected_path, encoding="utf-8"))
    got = json.load(open(recalc_path, encoding="utf-8-sig"))
    ok = True

    def check(name, a, b, tol=1e-9):
        nonlocal ok
        a = np.array([np.nan if x is None or isinstance(x, str) else x for x in a], dtype=float)
        b = np.array(b, dtype=float)
        if a.shape != b.shape:
            print(f"FAIL {name}: shape {a.shape} vs {b.shape}")
            ok = False
            return
        diff = float(np.nanmax(np.abs(a - b))) if a.size else 0.0
        bad = int(np.isnan(a).sum())
        good = diff <= tol and bad == 0
        ok &= good
        print(f"{'ok  ' if good else 'FAIL'} {name}: max |Excel − engine| = {diff:.3e}, non-numeric cells = {bad}")

    check("Results selected LGD", got["sel"], exp["sel"])
    check("LGD_300 final LGD", got["final"], exp["final"])
    check("lambda", [got["lam"]], [exp["lam"]], 1e-10)
    check("gamma", [got["gam"]], [exp["gam"]], 1e-10)
    check("weighted selected LGD", [got["avg_sel"]], [exp["avg_sel"]])
    tie = max(abs(got["tie_max"]), abs(got["tie_min"]))
    print(f"{'ok  ' if tie < 1e-9 else 'FAIL'} tie-out in Excel: {tie:.3e}")
    ok &= tie < 1e-9
    order_bad = [x for x in got["order"] if x not in ("OK", "", None)]
    print(f"{'ok  ' if not order_bad else 'FAIL'} Raw_Index order check: {len(order_bad)} flagged")
    ok &= not order_bad
    errs = {k: v for k, v in got["errors"].items() if v}
    print(f"{'ok  ' if not errs else 'FAIL'} error cells: {errs or 'none'}")
    ok &= not errs
    print(f"Excel open + full recalculation: {got['seconds']} s")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
