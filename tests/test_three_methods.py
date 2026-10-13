"""The log-normal shape (method 3) and the vintage start filter in the engine."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from hazard_ext.engine.core import EngineError, compute
from hazard_ext.engine.params import Params, load_params, merge_params
from hazard_ext.engine.parse import (COLUMNS, RecoveryData, RunoffBlock, from_raw_frame, rebuild_rows,
                                     rebuild_rows_slow)

from .conftest import ZIP_NAMES, load_zip


# ------------------------------------------------------------ log-normal fit
def _synthetic(mu=3.2, sigma=0.8, k=0.37, b0=1, b1=160):
    """A recovery row R(b) = k · s_logn(b) with the file layout, so the engine can fit it."""
    b = np.arange(b0, b1 + 1, dtype=float)
    s = np.exp(-((np.log(b) - mu) ** 2) / (2 * sigma * sigma)) / b
    r = k * s
    rows = []
    cum = 0.0
    for i, bb in enumerate(b):
        idx = i + 1
        df = 1.1 ** (-idx / 12)
        cum += r[i] * df
        rows.append(["Lifetime", 1, 0, 1e9, int(bb), 0.0, 0.0, r[i], idx, df, r[i] * df, cum, 1 - cum])
    return from_raw_frame(pd.DataFrame(rows, columns=COLUMNS))


def test_synthetic_lognormal_row_is_recovered_exactly():
    data = _synthetic()
    r = compute(data, Params(method=3, fit_start=24, min_exposure=0, target_ts=10, max_bucket=200))
    assert r.config["mu_fit"] == pytest.approx(3.2, abs=1e-10) and r.config["sigma_fit"] == pytest.approx(0.8, abs=1e-10)
    assert r.config["mu"] == pytest.approx(r.config["mu_fit"]) and r.config["logn_points"] == 137
    # the fitted shape, scaled on the anchor window, reproduces the row exactly
    np.testing.assert_allclose(r.ext[2, 1, 1:161], np.asarray(r.R[1, 1:161]), rtol=1e-9)
    # each single-override path recovers the other parameter
    a = compute(data, Params(method=3, fit_start=24, min_exposure=0, target_ts=10, max_bucket=200, sigma_override=0.8))
    assert a.config["mu"] == pytest.approx(3.2, abs=1e-10) and a.config["sigma"] == 0.8
    b = compute(data, Params(method=3, fit_start=24, min_exposure=0, target_ts=10, max_bucket=200, mu_override=3.2))
    assert b.config["sigma"] == pytest.approx(0.8, abs=1e-10) and b.config["mu"] == 3.2
    c = compute(data, Params(method=3, fit_start=24, min_exposure=0, target_ts=10, max_bucket=200, mu_override=3.0, sigma_override=0.5))
    assert (c.config["mu"], c.config["sigma"]) == (3.0, 0.5) and c.config["mu_fit"] == pytest.approx(3.2, abs=1e-10)
    assert c.config["logn_mode"] == pytest.approx(np.exp(3.0 - 0.25)) and c.config["logn_median"] == pytest.approx(np.exp(3.0))


def test_row_that_is_not_concave_in_ln_b_leaves_lognormal_undefined():
    # ln R + ln b is convex in ln b here, so the quadratic coefficient a2 is not negative
    b = np.arange(1, 80, dtype=float)
    r = 1e-4 * np.exp(0.02 * (np.log(b) ** 2))
    rows = [["Lifetime", 1, 0, 1e9, int(bb), 0.0, 0.0, r[i], i + 1, 0.99 ** (i + 1), r[i] * 0.99 ** (i + 1), 0.0, 1.0]
            for i, bb in enumerate(b)]
    data = from_raw_frame(pd.DataFrame(rows, columns=COLUMNS))
    res = compute(data, Params(method=1, fit_start=24, min_exposure=0, target_ts=10, max_bucket=100))
    assert np.isnan(res.config["mu"]) and np.isnan(res.config["sigma"]) and res.config["logn_points"] == 0
    assert any("not concave" in w for w in res.warnings)
    assert np.isnan(res.results["lgd_logn"]).all() and np.isnan(res.lgd_ts["derived_logn"]).all()
    assert np.isnan(res.averages["lgd_logn"]) and res.to_json()["averages"]["lgd_logn"] is None
    assert np.isfinite(res.results["lgd_exp"]).all() and np.isfinite(res.results["lgd_power"]).all()
    with pytest.raises(EngineError, match="μ / σ"):
        compute(data, Params(method=3, fit_start=24, min_exposure=0, target_ts=10, max_bucket=100))


@pytest.mark.parametrize("name, mu, sigma, window", [("44", 3.043, 0.703, (24, 89)), ("22", 3.769, 0.792, (24, 151)),
                                                     ("ALL", 3.722, 0.943, (24, 328))])
def test_fitted_parameters_on_the_zips_match_the_design_note(name, mu, sigma, window):
    r = compute(load_zip(name), Params(method=3))
    assert r.config["mu"] == pytest.approx(mu, abs=1e-3) and r.config["sigma"] == pytest.approx(sigma, abs=1e-3)
    assert (r.params["fit_start"], r.config["ref_last_cred"]) == window
    assert np.isfinite(r.results["lgd_selected"]).all()
    assert r.config["method_label"] == "Log-normal"


def test_lognormal_shape_matches_its_formula(zip44):
    r = compute(zip44, Params(method=3))
    b = np.arange(1, 50, dtype=float)
    expect = np.exp(-((np.log(b) - r.config["mu"]) ** 2) / (2 * r.config["sigma"] ** 2)) / b
    np.testing.assert_allclose(r.shapes[2, 1:50], expect, rtol=1e-12)
    assert r.curve(1)["logn"][0] == pytest.approx(r.ext[2, 1, 1])


# ------------------------------------------------------------ parameters
def test_method_default_is_exponential_and_retired_keys_are_dropped():
    assert Params().method == 1
    p = merge_params({"method": 3, "client_cohort": "44"}, {"client_cohort": None, "window": 6})
    assert p.method == 3 and p.window == 6 and not hasattr(p, "client_cohort")
    assert load_params({"method": 2, "client_cohort": "ALL"}).method == 2
    with pytest.raises(ValueError):
        Params(sigma_override=0)
    with pytest.raises(ValueError):
        Params(vintage_start="2016-08", vintage_years=10)
    assert Params(vintage_start="2016-08-31").vintage_start == "2016-08"
    assert Params(vintage_start="").vintage_start is None
    with pytest.raises(ValueError):
        Params(vintage_start="August 2016")


# ------------------------------------------------------------ vintage rebuild
@pytest.mark.parametrize("name", ZIP_NAMES)
def test_rebuild_with_every_vintage_equals_the_file(name):
    data = load_zip(name)
    assert data.runoff_status == {"ok": True, "reason": ""}
    for ev in data.event_types:
        file_tri = data.triangles(ev)
        block = data.runoff[ev]
        rows = rebuild_rows(block, np.ones(len(block.cohort), dtype=bool), file_tri.implied_rate)
        tri = RecoveryData(category="", meta={}, event_types=[ev], raw={ev: rows}).triangles(ev)
        np.testing.assert_allclose(tri.R, file_tri.R, atol=1e-12)
        np.testing.assert_allclose(tri.E, file_tri.E, rtol=1e-12)
        ok = ~np.isnan(file_tri.lgd_file)
        np.testing.assert_allclose(tri.lgd_file[ok], file_tri.lgd_file[ok], atol=1e-12)
        np.testing.assert_allclose(tri.lgd_unfloored[ok], file_tri.lgd_unfloored[ok], atol=1e-12)
        assert tri.last_obs_file == file_tri.last_obs_file


def test_vectorised_rebuild_equals_the_loop_reference(zip44):
    block = zip44.runoff["Lifetime"]
    rng = np.random.default_rng(7)
    keep = rng.random(len(block.cohort)) < 0.5
    fast = rebuild_rows(block, keep, 0.1771)
    slow = rebuild_rows_slow(block, keep, 0.1771)
    assert fast.shape == slow.shape
    np.testing.assert_allclose(fast, slow, rtol=1e-12, atol=1e-12)


def test_start_before_the_first_vintage_is_the_same_as_no_filter(zip44):
    a = compute(zip44, Params(method=2, vintage_start="2000-01")).to_json()
    b = compute(zip44, Params(method=2)).to_json()
    a.pop("params"), b.pop("params")
    assert a == b


def test_vintage_years_resolves_per_zip():
    p = Params(method=1, vintage_years=10)
    r44 = compute(load_zip("44"), p)
    r15 = compute(load_zip("15"), p)
    assert r44.config["vintage_filter"] is False                         # 2016-06 is before VB44's first vintage
    assert r15.config["vintage_filter"] is True and r15.config["vintage_start_effective"] == "2016-08"
    assert r15.config["vintage_last"] == "2026-07" and 0 < r15.config["cohorts_included"] < r15.config["cohorts_total"]
    assert 0 < r15.config["exposure_share"] < 1
    assert r15.config["lgd_file_label"] == "LGD (vintages from 2016-08)"
    assert r15.warnings[0].startswith("Vintages from 2016-08:")
    assert max(abs(r15.averages["tie_max"]), abs(r15.averages["tie_min"])) < 1e-9
    assert r15.config["last_obs_file"] < r15.config["last_obs_unfiltered"]


def test_filter_changes_the_triangle_and_the_lgd(zip44):
    base = compute(zip44, Params(method=1))
    r = compute(zip44, Params(method=1, vintage_years=5))
    assert r.config["vintage_start_effective"] == "2021-06" and r.config["cohorts_included"] == 18
    assert r.config["opening_exposure"] < base.config["opening_exposure"]
    assert r.averages["lgd_file"] != base.averages["lgd_file"]
    # the rebuilt subset's own LGD is reproduced exactly (the tie-out is zero by construction)
    assert max(abs(r.averages["tie_max"]), abs(r.averages["tie_min"])) < 1e-9
    # a Rand credibility cut that leaves fewer than W credible buckets on the smaller book is flagged
    r2 = compute(zip44, Params(method=1, vintage_years=5, min_exposure=2.5e9, fit_start=2))
    assert r2.tail_fit["n_win"][0] < 12 and any("Rand MinExposure" in w for w in r2.warnings)
    assert not any("Rand MinExposure" in w for w in compute(zip44, Params(method=1, min_exposure=2.5e9, fit_start=2)).warnings)


def test_vintage_errors(zip44):
    with pytest.raises(EngineError, match="after the last vintage"):
        compute(zip44, Params(vintage_start="2030-01"))
    data = RecoveryData(category="44", meta={}, event_types=zip44.event_types, raw=zip44.raw)   # no runoff
    with pytest.raises(EngineError, match="Upload the zip again"):
        compute(data, Params(vintage_years=5))
    compute(data, Params())                                                                     # unfiltered still runs
    assert data.profile()["has_runoff"] is False and data.profile()["vintage_filter"] is False


def test_storage_round_trip_keeps_the_runoff_and_old_blobs_load(zip44):
    back = RecoveryData.from_bytes(zip44.to_bytes())
    assert back.runoff_status == zip44.runoff_status and back.identical_runoff
    for ev in zip44.event_types:
        np.testing.assert_array_equal(back.runoff[ev].X, zip44.runoff[ev].X)
        np.testing.assert_array_equal(back.runoff[ev].cohort, zip44.runoff[ev].cohort)
    a = compute(back, Params(vintage_years=5)).to_json()
    b = compute(zip44, Params(vintage_years=5)).to_json()
    assert a == b
    # a blob written before runoff data was stored
    old = RecoveryData(category="44", meta={}, event_types=zip44.event_types, raw=zip44.raw)
    loaded = RecoveryData.from_bytes(old.to_bytes())
    assert loaded.runoff == {} and loaded.has_runoff is False
    with pytest.raises(EngineError, match="Upload the zip again"):
        compute(loaded, Params(vintage_start="2020-01"))


def test_self_check_failure_disables_the_filter_but_keeps_the_data(zip44):
    block = zip44.runoff["Lifetime"]
    bad = RunoffBlock(cohort=block.cohort.copy(), X=block.X * 1.001, obs=block.obs.copy())
    data = RecoveryData(category="44", meta={}, event_types=zip44.event_types, raw=zip44.raw,
                        runoff={ev: bad for ev in zip44.event_types})
    status = data.self_check()
    assert status["ok"] is False and "differs" in status["reason"]
    assert data.profile()["has_runoff"] is True and data.profile()["vintage_filter"] is False
    compute(data, Params())
    with pytest.raises(EngineError, match="does not reproduce"):
        compute(data, Params(vintage_years=5))
