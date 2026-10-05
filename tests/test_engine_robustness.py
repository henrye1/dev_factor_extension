"""The engine must either return finite results or raise EngineError, whatever the parameters."""
from __future__ import annotations

import random

import numpy as np
import pandas as pd
import pytest

from hazard_ext.engine.core import EngineError, compute
from hazard_ext.engine.params import Params
from hazard_ext.engine.parse import COLUMNS, from_raw_frame

from .conftest import load_zip, prototype_curves


def _random_params(rng: random.Random, n: int) -> dict:
    return {
        "target_ts": rng.choice([1, 2, n - 1, n, n + 1, 300, 2000]),
        "max_bucket": rng.choice([1, 5, n - 1, n, n + 1, 420, 2000]),
        "min_exposure_mode": rng.choice(["abs", "pct"]),
        "min_exposure": rng.choice([0, 1e-9, 0.5, 50, 100]),
        "window": rng.choice([1, 2, 12, 500]),
        "fit_start": rng.choice([1, 2, 24, n, 1999]),
        "ref_ts": rng.choice([1, 2, n // 2, n]),
        "method": rng.choice([1, 2, 3]),
        "horizon": rng.choice([1, 12, 2000]),
        "horizon2": rng.choice([1, 120, 2000]),
        "lambda_override": rng.choice([None, None, 0.0, -0.5, 0.05, 50.0]),
        "gamma_override": rng.choice([None, None, 0.0, -2.0, 1.5, 100.0]),
        "floor": rng.choice([0, 0, 1e-6, 0.5]),
        "base_ts": rng.choice([1, 2, n // 2, n]),
        "last_ts": rng.choice([None, 0, 1, n // 2, n, 1999]),
        "rate": rng.choice([None, 0.0, 0.2, 5.0, -0.5]),
    }


@pytest.mark.parametrize("seed", range(60))
def test_random_parameters_never_crash(seed):
    rng = random.Random(seed)
    data = load_zip("44")
    n = data.triangles("Lifetime").n
    kwargs = _random_params(rng, n)
    curve = rng.choice([prototype_curves()["44"], None, np.zeros(3), prototype_curves()["11"][:50]])
    try:
        res = compute(data, Params(**kwargs), curve)
    except EngineError:
        return
    j = res.to_json()                                   # serialisable, no nan left in it
    assert len(j["lgd_ts"]["lgd_final"]) == kwargs["target_ts"]
    assert all(v is not None for v in j["results"]["lgd_selected"])
    assert all(v is not None for v in j["lgd_ts"]["lgd_final"])
    res.curve(1)
    res.curve(n)


def _frame(rows):
    return pd.DataFrame(rows, columns=COLUMNS)


@pytest.mark.parametrize("method", [1, 2, 3])
def test_tiny_and_degenerate_files(method):
    curve = prototype_curves()["44"]
    one = from_raw_frame(_frame([["Lifetime", 1, 0, 100.0, 1, 100.0, 90.0, 0.1, 1, 0.98, 0.098, 0.098, 0.902]]))
    zero_exposure = from_raw_frame(_frame([
        ["Lifetime", 1, 0, 0.0, 1, 0.0, 0.0, 0.0, 1, 0.98, 0.0, 0.0, 1.0],
        ["Lifetime", 1, 0, 0.0, 2, 0.0, 0.0, 0.0, 2, 0.96, 0.0, 0.0, 1.0],
        ["Lifetime", 2, 30, 0.0, 2, 0.0, 0.0, 0.0, 1, 0.98, 0.0, 0.0, 1.0],
    ]))
    for data in (one, zero_exposure):
        try:
            res = compute(data, Params(method=method, target_ts=10, max_bucket=20), curve)
        except EngineError:
            continue
        res.to_json()
