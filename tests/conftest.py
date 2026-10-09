from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from hazard_ext.engine.parse import COLUMNS, NUM_COLUMNS, from_raw_frame, parse_zip

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden"
ZIP_NAMES = ["11", "15", "22", "23", "25", "44", "ALL"]

CONFIG_KEYS = {
    "rate": "Discount rate p.a.",
    "max_bucket": "MaxBucket",
    "min_exposure": "MinExposure (credibility cut, R)",
    "window": "Window W (buckets)",
    "fit_start": "FitStart bucket",
    "ref_ts": "Reference TermStep for λ / γ",
    "method": "Method (1 = exponential, 2 = power law, 3 = client-curve shape)",
    "client_cohort": "Client curve cohort (11/15/22/23/25/44)",
    "horizon": "Horizon (months) for the short LGD",
    "floor": "Hazard floor (per bucket)",
    "base_ts": "Base TermStep row for the 300-period LGD",
    "horizon2": "Valuation horizon (months) – client basis",
    "last_ts": "Last observed TermStep to use as-is",
}


class Golden:
    def __init__(self, key: str):
        z = np.load(GOLDEN / f"{key}.npz", allow_pickle=False)
        self.z = z
        self.config = json.loads(str(z["config"]))
        df = pd.DataFrame(z["raw"], columns=NUM_COLUMNS)
        df.insert(0, "EventType", z["events"])
        self.data = from_raw_frame(df[COLUMNS], category=str(self.config[CONFIG_KEYS["client_cohort"]]))
        self.params = {k: self.config[label] for k, label in CONFIG_KEYS.items() if k != "client_cohort"}
        # the workbooks' method 3 was the removed client-curve shape; the golden comparisons run on method 1
        self.params["method"] = 1
        self.params["target_ts"] = 300
        self.params["event_type"] = self.config["EventType"]
        cohorts = [str(c) for c in z["cohorts"]]
        self.curves = {c: z["curves"][:, i + 1] for i, c in enumerate(cohorts)}


_prototype: dict = {}


def prototype_curves() -> dict[str, np.ndarray]:
    """The six July 2026 prototype curves (11, 15, 22, 23, 25, 44; t = 1..553) from the workbooks'
    Client_Curve sheet. Used only as applied-curve test data: the engine takes no curves."""
    if not _prototype:
        _prototype.update(Golden("vb44").curves)
    return _prototype


def prototype_curves_csv() -> str:
    curves = prototype_curves()
    labels = sorted(curves)
    n = max(len(v) for v in curves.values())
    lines = [",".join(["t", *labels])]
    lines += [",".join([str(t + 1), *(repr(float(curves[k][t])) for k in labels)]) for t in range(n)]
    return "\n".join(lines) + "\n"


@pytest.fixture(scope="session", params=["vb44", "vb22"])
def golden(request) -> Golden:
    return Golden(request.param)


@pytest.fixture(scope="session")
def vb44() -> Golden:
    return Golden("vb44")


def zip_path(name: str) -> Path:
    return ROOT / f"debug ({name}).zip"


_zip_cache: dict = {}


def load_zip(name: str):
    if name not in _zip_cache:
        _zip_cache[name] = parse_zip(str(zip_path(name)))
    return _zip_cache[name]


@pytest.fixture(scope="session")
def small_zip_path() -> Path:
    return zip_path("44")


@pytest.fixture(scope="session")
def zip44():
    return load_zip("44")
