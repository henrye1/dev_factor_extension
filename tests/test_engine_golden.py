"""The engine must reproduce the example workbooks' cached values."""
from __future__ import annotations

import numpy as np
import pytest

from hazard_ext.engine.core import EngineError, compute
from hazard_ext.engine.params import Params, merge_params

from .conftest import ZIP_NAMES, load_zip, prototype_curves

TOL = 1e-9

RESULT_COLS = [
    "ts", "exposure", "lgd_file", "lgd_replica", "tie_out", "lgd_exp", "lgd_power",
    "lgd_client", "lgd_selected", "pv_original", "pv_selected", "uplift", "lgd_horizon",
    "undisc_observed", "undisc_selected", "last_cred", "buckets_added",
]
LGD_COLS = [
    "ts", "exposure", "lgd_file", "lgd_own", "balance_factor", "derived_exp", "derived_power",
    "derived_client", "derived_selected", "lgd_final", "validation", "lgd_valuation_horizon",
    "lgd_short_horizon", "undisc_remaining",
]
TAIL_COLS = ["ts", "last_obs", "last_cred", "win_start", "win_end", "n_win", "sum_obs",
             "scale_exp", "scale_power", "scale_client"]


def _run(g, **changes):
    p = merge_params(g.params, changes)
    return compute(g.data, p, g.curves[p.client_cohort])


def _assert_cols(actual: dict, expected: np.ndarray, cols: list[str], rel_cols=()):
    assert len(cols) == expected.shape[1]
    for j, col in enumerate(cols):
        a = np.asarray(actual[col], dtype=float)
        e = expected[:, j]
        assert a.shape == e.shape, col
        assert np.array_equal(np.isnan(a), np.isnan(e)), f"{col}: blank pattern differs"
        ok = ~np.isnan(e)
        if col in rel_cols:
            np.testing.assert_allclose(a[ok], e[ok], rtol=1e-9, atol=0, err_msg=col)
        else:
            np.testing.assert_allclose(a[ok], e[ok], rtol=0, atol=TOL, err_msg=col)


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
    _assert_cols(r.tail_fit, golden.z["tail"], TAIL_COLS, rel_cols=("scale_exp", "scale_power", "scale_client"))


def test_results_table(golden):
    r = _run(golden)
    _assert_cols(r.results, golden.z["results"], RESULT_COLS, rel_cols=("exposure",))


def test_weighted_averages(golden):
    r = _run(golden)
    keys = ["lgd_file", "lgd_replica", "lgd_exp", "lgd_power", "lgd_client", "lgd_selected", "uplift"]
    for k, e in zip(keys, golden.z["averages"]):
        assert r.averages[k] == pytest.approx(e, abs=TOL), k
    tie_max, tie_min, simple = golden.z["extra"]
    assert r.averages["uplift_simple"] == pytest.approx(simple, abs=TOL)
    assert abs(r.averages["tie_max"]) < TOL and abs(r.averages["tie_min"]) < TOL


def test_lgd_to_target(golden):
    r = _run(golden)
    _assert_cols(r.lgd_ts, golden.z["lgd"], LGD_COLS, rel_cols=("exposure",))
    assert list(r.lgd_ts["source"]) == list(golden.z["lgd_src"])
    vmax, vmin, bal = golden.z["lgd_summary"]
    assert r.lgd_ts_summary["validation_max"] == pytest.approx(vmax, abs=TOL)
    assert r.lgd_ts_summary["validation_min"] == pytest.approx(vmin, abs=TOL)
    assert r.lgd_ts_summary["balance_factor_target"] == pytest.approx(bal, abs=TOL)


@pytest.mark.parametrize("method", [1, 2])
def test_method_switch_selects_matching_column(vb44, method):
    r = _run(vb44, method=method)
    col = {1: "lgd_exp", 2: "lgd_power"}[method]
    np.testing.assert_array_equal(r.results["lgd_selected"], r.results[col])
    # the three shape columns do not depend on the selected method
    np.testing.assert_allclose(r.results["lgd_client"], vb44.z["results"][:, 7], atol=TOL)


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


def test_method3_without_curve_is_rejected(vb44):
    with pytest.raises(EngineError, match="reference curve"):
        compute(vb44.data, merge_params(vb44.params), None)


def test_without_curve_other_methods_run_and_client_is_blank(vb44):
    r = compute(vb44.data, merge_params(vb44.params, {"method": 1}), None)
    assert np.isnan(r.results["lgd_client"]).all()
    assert r.averages["lgd_client"] != r.averages["lgd_client"]      # nan
    np.testing.assert_allclose(r.results["lgd_exp"], vb44.z["results"][:, 5], atol=TOL)
    assert r.to_json()["averages"]["lgd_client"] is None


def test_warnings(vb44):
    r = _run(vb44, max_bucket=600, target_ts=50)
    text = " | ".join(r.warnings)
    assert "beyond the end of the reference curve" in text
    assert "below the last observed TermStep" in text
    r = _run(vb44, rate=0.10)
    assert any("Tie-out" in w for w in r.warnings)
    assert not _run(vb44).warnings


def test_file_lgd_floor_is_reported_not_treated_as_a_mismatch():
    # In the cohort 44 zip the risk suite floors TermStep 2's LGD at TermStep 1's LGD.
    data = load_zip("44")
    r = compute(data, Params(target_ts=300, max_bucket=420), prototype_curves()["44"])
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
def test_all_zips_tie_out(name):
    data = load_zip(name)
    curves = prototype_curves()
    if name == "ALL":
        params, curve = Params(method=1, target_ts=360, max_bucket=480), None
    else:
        params, curve = Params(target_ts=360, max_bucket=480), curves[name]
    r = compute(data, params, curve)
    assert max(abs(r.averages["tie_max"]), abs(r.averages["tie_min"])) < TOL
    assert r.config["rate"] == pytest.approx(data.meta["Parameters"]["InterestRate"], abs=1e-6)
    sel = np.asarray(r.results["lgd_selected"])
    assert np.isfinite(sel).all()
    final = np.asarray(r.lgd_ts["lgd_final"])
    assert len(final) == 360 and np.isfinite(final).all()
