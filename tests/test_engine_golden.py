"""The engine must reproduce the example workbooks' cached values.

The golden files were extracted from the hand-built workbooks, whose third shape was the client
curve and whose Config method is 3. That shape no longer exists, so only the columns that keep
their meaning are asserted: everything except the old client-shape columns and the "selected"
columns, which the workbooks computed under method 3. The selected path is covered by running
the engine under methods 1 and 2 and checking it against the exponential and power-law columns.
"""
from __future__ import annotations

import numpy as np
import pytest

from hazard_ext.engine.core import EngineError, compute
from hazard_ext.engine.params import Params, merge_params

from .conftest import ZIP_NAMES, load_zip

TOL = 1e-9

# golden column order (the workbook's Results sheet); None = a column that is no longer asserted
RESULT_COLS = [
    "ts", "exposure", "lgd_file", "lgd_replica", "tie_out", "lgd_exp", "lgd_power",
    None, None, "pv_original", None, None, None,
    "undisc_observed", None, "last_cred", "buckets_added",
]
# the "selected" columns, checked under methods 1 and 2 against the golden per-shape columns
RESULT_SELECTED = {"lgd_selected": {1: 5, 2: 6}}
LGD_COLS = [
    "ts", "exposure", "lgd_file", None, None, "derived_exp", "derived_power",
    None, None, None, None, None, None, None,
]
LGD_SELECTED = {"derived_selected": {1: 5, 2: 6}}
TAIL_COLS = ["ts", "last_obs", "last_cred", "win_start", "win_end", "n_win", "sum_obs",
             "scale_exp", "scale_power", None]


def _run(g, **changes):
    p = merge_params(g.params, changes)
    return compute(g.data, p)


def _assert_cols(actual: dict, expected: np.ndarray, cols: list, rel_cols=()):
    assert len(cols) == expected.shape[1]
    for j, col in enumerate(cols):
        if col is None:
            continue
        _assert_col(actual[col], expected[:, j], col, col in rel_cols)


def _assert_col(a, e, name, rel=False):
    a = np.asarray(a, dtype=float)
    assert a.shape == e.shape, name
    assert np.array_equal(np.isnan(a), np.isnan(e)), f"{name}: blank pattern differs"
    ok = ~np.isnan(e)
    if rel:
        np.testing.assert_allclose(a[ok], e[ok], rtol=1e-9, atol=0, err_msg=name)
    else:
        np.testing.assert_allclose(a[ok], e[ok], rtol=0, atol=TOL, err_msg=name)


def test_derived_config(golden):
    r = _run(golden)
    c = golden.config
    assert r.config["lam_fit"] == pytest.approx(c["λ fitted (ref row, FitStart..LastCredible)"], abs=1e-12)
    assert r.config["gam_fit"] == pytest.approx(c["γ fitted (ref row, ln b)"], abs=1e-11)
    assert r.config["v"] == pytest.approx(c["Monthly discount factor v = (1+r)^(−1/12)"], abs=1e-15)
    assert r.config["ref_last_cred"] == c["Reference row last credible bucket"]
    assert r.config["last_obs_file"] == c["Last observed bucket in file (max BucketIndex with exposure > 0)"]
    assert r.config["rate_implied"] == pytest.approx(c["Rate implied by the file"], abs=1e-12)
    assert r.config["half_life"] == pytest.approx(c["Half-life of the exponential tail (buckets)"], abs=1e-9)


def test_tail_fit(golden):
    r = _run(golden)
    _assert_cols(r.tail_fit, golden.z["tail"], TAIL_COLS, rel_cols=("scale_exp", "scale_power"))


def test_results_table(golden):
    r = _run(golden)
    _assert_cols(r.results, golden.z["results"], RESULT_COLS, rel_cols=("exposure",))


@pytest.mark.parametrize("method", [1, 2])
def test_selected_columns_under_methods_1_and_2(golden, method):
    # pv_selected, uplift, lgd_horizon and undisc_selected are only held for method 3 in the golden
    # files, so they are checked for consistency with the per-shape columns here instead.
    r = _run(golden, method=method)
    for key, cols in RESULT_SELECTED.items():
        _assert_col(r.results[key], golden.z["results"][:, cols[method]], f"{key} (method {method})")
    for key, cols in LGD_SELECTED.items():
        _assert_col(r.lgd_ts[key], golden.z["lgd"][:, cols[method]], f"{key} (method {method})")
    shape = {1: "lgd_exp", 2: "lgd_power"}[method]
    np.testing.assert_array_equal(r.results["lgd_selected"], r.results[shape])
    np.testing.assert_allclose(r.results["pv_selected"], 1 - r.results[shape], atol=1e-15)
    np.testing.assert_allclose(r.results["uplift"], r.results["pv_selected"] - r.results["pv_original"], atol=1e-15)
    assert np.all(r.results["lgd_horizon"] >= r.results["lgd_selected"] - 1e-12)


def test_weighted_averages(golden):
    r = _run(golden)
    keys = ["lgd_file", "lgd_replica", "lgd_exp", "lgd_power", None, None, None]
    for k, e in zip(keys, golden.z["averages"]):
        if k is not None:
            assert r.averages[k] == pytest.approx(e, abs=TOL), k
    assert abs(r.averages["tie_max"]) < TOL and abs(r.averages["tie_min"]) < TOL


def test_lgd_to_target(golden):
    r = _run(golden)
    _assert_cols(r.lgd_ts, golden.z["lgd"], LGD_COLS, rel_cols=("exposure",))
    assert list(r.lgd_ts["source"]) == list(golden.z["lgd_src"])
    # validation and the balance factor are per selected method in the golden files (method 3)


def test_method_switch_selects_matching_column(vb44):
    a, b = _run(vb44, method=1), _run(vb44, method=2)
    np.testing.assert_array_equal(a.results["lgd_selected"], a.results["lgd_exp"])
    np.testing.assert_array_equal(b.results["lgd_selected"], b.results["lgd_power"])
    # the three shape columns do not depend on the selected method
    np.testing.assert_array_equal(a.results["lgd_power"], b.results["lgd_power"])
    np.testing.assert_array_equal(a.results["lgd_logn"], b.results["lgd_logn"])


def test_default_last_ts_is_last_termstep_with_data(vb44):
    r = _run(vb44, last_ts=None)
    assert r.config["last_ts"] == 97          # TermStep 98 is empty in the VB44 file


def test_percent_min_exposure_equals_absolute(vb44):
    opening = vb44.data.triangles("Lifetime").E[1, 1]
    pct = 1e8 / opening * 100
    a = _run(vb44)
    b = _run(vb44, min_exposure_mode="pct", min_exposure=pct)
    np.testing.assert_array_equal(a.results["last_cred"], b.results["last_cred"])
    np.testing.assert_allclose(a.results["lgd_selected"], b.results["lgd_selected"], atol=1e-12)


def test_no_reference_curve_key_remains(vb44):
    r = _run(vb44, method=3)
    j = r.to_json()
    text = str(sorted(j["config"]) + sorted(j["results"]) + sorted(j["lgd_ts"]) + sorted(j["tail_fit"])
               + sorted(j["averages"]) + sorted(j["params"]) + sorted(r.curve(1)))
    assert "client" not in text and "reference" not in text
    assert "lgd_logn" in j["results"] and "derived_logn" in j["lgd_ts"] and "scale_logn" in j["tail_fit"]


def test_warnings(vb44):
    r = _run(vb44, max_bucket=600, target_ts=50)
    text = " | ".join(r.warnings)
    assert "below the last observed TermStep" in text
    r = _run(vb44, rate=0.10)
    assert any("Tie-out" in w for w in r.warnings)
    assert not _run(vb44).warnings


def test_file_lgd_floor_is_reported_not_treated_as_a_mismatch():
    # In the cohort 44 zip the risk suite floors TermStep 2's LGD at TermStep 1's LGD.
    data = load_zip("44")
    r = compute(data, Params(target_ts=300, max_bucket=420))
    gap = np.asarray(r.results["file_floor_gap"])
    assert gap[1] == pytest.approx(0.0026321195721, abs=1e-10)
    assert np.nan_to_num(np.delete(gap, 1)).max() < 1e-12
    assert r.averages["file_floored_count"] == 1
    assert any("floored" in w and "(2)" in w for w in r.warnings)
    assert not any(w.startswith("Tie-out") for w in r.warnings)


def test_reference_row_out_of_range(vb44):
    with pytest.raises(EngineError, match="Reference TermStep"):
        _run(vb44, ref_ts=500)


def test_overrides_replace_fitted_decay(vb44):
    r = _run(vb44, lambda_override=0.02, gamma_override=1.5)
    assert r.config["lam"] == 0.02 and r.config["gam"] == 1.5
    assert r.config["lam_fit"] == pytest.approx(0.052292248034841275, abs=1e-12)
    base = _run(vb44)
    # a slower decay adds recoveries, so LGD falls
    assert r.averages["lgd_exp"] < base.averages["lgd_exp"]


@pytest.mark.parametrize("name", ZIP_NAMES)
@pytest.mark.parametrize("method", [1, 2, 3])
def test_all_zips_tie_out(name, method):
    data = load_zip(name)
    r = compute(data, Params(method=method, target_ts=360, max_bucket=480))
    assert max(abs(r.averages["tie_max"]), abs(r.averages["tie_min"])) < TOL
    assert r.config["rate"] == pytest.approx(data.meta["Parameters"]["InterestRate"], abs=1e-6)
    sel = np.asarray(r.results["lgd_selected"])
    assert np.isfinite(sel).all()
    final = np.asarray(r.lgd_ts["lgd_final"])
    assert len(final) == 360 and np.isfinite(final).all()
