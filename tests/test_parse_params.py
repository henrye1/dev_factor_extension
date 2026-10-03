from __future__ import annotations

import io
import zipfile

import numpy as np
import pytest

from hazard_ext.engine.curves import CurveError, builtin_curves, parse_curve_file
from hazard_ext.engine.params import DEFAULTS, Params, clean_overrides, merge_params
from hazard_ext.engine.parse import ParseError, RecoveryData, parse_zip

from .conftest import ZIP_NAMES, load_zip


def _zip_bytes(files: dict[str, str]) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    buf.seek(0)
    return buf


# ------------------------------------------------------------------- parsing
@pytest.mark.parametrize("name", ZIP_NAMES)
def test_zip_profile(name):
    data = load_zip(name)
    prof = data.profile()
    assert data.category == name
    assert prof["event_types"] == ["Lifetime", "LifetimeSingle", "TwelveMonthSingle"]
    assert prof["identical_events"] is True
    assert prof["contiguous"] is True
    assert prof["min_ts"] == 1 and prof["max_ts"] == prof["n"]
    assert prof["last_obs_bucket"] == prof["n"] - 1          # the last bucket is empty
    assert prof["implied_rate"] == pytest.approx(data.meta["Parameters"]["InterestRate"], abs=1e-6)
    assert prof["opening_exposure"] > 0


def test_round_trip_through_bytes(zip44):
    blob = zip44.to_bytes()
    assert len(blob) < 2_000_000
    back = RecoveryData.from_bytes(blob)
    assert back.category == "44" and back.event_types == zip44.event_types
    assert back.meta == zip44.meta
    for ev in zip44.event_types:
        np.testing.assert_array_equal(back.raw[ev], zip44.raw[ev])
    a, b = zip44.triangles("Lifetime"), back.triangles("Lifetime")
    np.testing.assert_array_equal(a.R, b.R)
    np.testing.assert_array_equal(a.E, b.E)


def test_triangle_layout(zip44):
    tri = zip44.triangles("Lifetime")
    assert tri.R.shape == (tri.n + 1, tri.n + 1)
    assert np.all(np.tril(tri.R, -1) == 0) and np.all(np.tril(tri.E, -1) == 0)   # b < ts is empty
    assert tri.E[1, 1] == pytest.approx(4017646153.76, rel=1e-12)
    assert tri.R[1, 1] == pytest.approx(0.0094923237040946233, rel=1e-12)


def test_missing_recovery_file():
    with pytest.raises(ParseError, match="lgd_recovery.csv"):
        parse_zip(_zip_bytes({"debug.json": "{}"}))


def test_missing_columns():
    with pytest.raises(ParseError, match="RecoveryPct"):
        parse_zip(_zip_bytes({"lgd_recovery.csv": "EventType,TermStep\nLifetime,1\n"}))


def test_not_a_zip():
    with pytest.raises(ParseError, match="valid zip"):
        parse_zip(io.BytesIO(b"this is not a zip"))


def test_unknown_event_type(zip44):
    with pytest.raises(ParseError, match="Nope"):
        zip44.triangles("Nope")


# ---------------------------------------------------------------- parameters
def test_defaults_match_the_workbook_config():
    p = Params()
    assert (p.event_type, p.max_bucket, p.min_exposure, p.window, p.fit_start, p.ref_ts,
            p.method, p.horizon, p.horizon2, p.floor, p.base_ts) == \
           ("Lifetime", 420, 100_000_000, 12, 24, 1, 3, 12, 120, 0, 1)
    assert p.rate is None and p.last_ts is None and p.lambda_override is None
    assert p.target_ts == 300


def test_override_wins_over_scenario():
    p = merge_params({"method": 1, "window": 6}, {"window": 18, "last_ts": 80})
    assert (p.method, p.window, p.last_ts) == (1, 18, 80)
    assert p.fit_start == DEFAULTS["fit_start"]


def test_override_can_blank_a_scenario_value():
    assert merge_params({"rate": 0.2}, {"rate": None}).rate is None


def test_unknown_or_invalid_override_is_rejected():
    with pytest.raises(ValueError, match="Unknown"):
        clean_overrides({"windw": 3})
    with pytest.raises(ValueError):
        merge_params({}, {"method": 4})
    with pytest.raises(ValueError):
        Params(min_exposure_mode="pct", min_exposure=150)
    assert clean_overrides({"method": 2}) == {"method": 2}


def test_override_is_judged_against_its_scenario_not_the_defaults():
    # switching only the basis is valid when the scenario's amount is a sensible percentage
    assert clean_overrides({"min_exposure_mode": "pct"}) == {"min_exposure_mode": "pct"}
    assert merge_params({"min_exposure": 0.5}, {"min_exposure_mode": "pct"}).min_exposure_mode == "pct"
    with pytest.raises(ValueError, match="percentage"):
        merge_params({}, {"min_exposure_mode": "pct"})            # R100m is not a percentage


@pytest.mark.parametrize("bad", [
    {"window": 10 ** 30}, {"horizon": 10 ** 9}, {"rate": float("nan")}, {"floor": float("inf")},
    {"lambda_override": 1e9}, {"min_exposure": float("nan")}, {"event_type": "x" * 500},
])
def test_parameters_are_bounded_and_finite(bad):
    with pytest.raises(ValueError):
        Params(**bad)


# -------------------------------------------------------------------- curves
def test_builtin_curves_match_the_workbooks(vb44):
    curves = builtin_curves()
    assert sorted(curves) == ["11", "15", "22", "23", "25", "44"]
    for label, values in curves.items():
        assert len(values) == 553
        np.testing.assert_allclose(values, vb44.curves[label], rtol=0, atol=1e-18)


def test_parse_curve_csv():
    out = parse_curve_file(b"t,44,New\n1,0.01,0.02\n2,0.03,0.04\n", "c.csv")
    assert list(out) == ["44", "New"] and out["New"].tolist() == [0.02, 0.04]


@pytest.mark.parametrize("text,msg", [
    (b"t,a\n1,0.1\n3,0.2\n", "no gaps"),
    (b"t,a\n1,-0.1\n", "negative"),
    (b"t,a\n1,inf\n", "not finite"),
    (b"t\n1\n", "at least one"),
])
def test_bad_curve_files(text, msg):
    with pytest.raises(CurveError, match=msg):
        parse_curve_file(text, "c.csv")


def test_curve_file_type():
    with pytest.raises(CurveError, match="csv or .xlsx"):
        parse_curve_file(b"", "c.txt")
