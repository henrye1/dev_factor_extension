from __future__ import annotations

import numpy as np
import pytest

from hazard_ext.engine.applied import implied_lgd, rate_at, to_face
from hazard_ext.engine.core import compute
from hazard_ext.engine.curves import builtin_curves
from hazard_ext.engine.params import Params

from .conftest import load_zip


def test_face_basis_is_unchanged():
    c = np.array([0.1, 0.05, 0.02])
    np.testing.assert_array_equal(to_face(c, "face"), c)


def test_outstanding_basis_is_survival_weighted():
    h = np.array([0.5, 0.5, 0.5])
    # month 1: 50% of face; month 2: 50% of the remaining 50%; month 3: 50% of the remaining 25%
    np.testing.assert_allclose(to_face(h, "outstanding"), [0.5, 0.25, 0.125])
    with pytest.raises(ValueError, match="basis"):
        to_face(h, "percent")


def test_roll_forward_divides_by_the_remaining_balance():
    c = np.array([0.2, 0.1, 0.1, 0.05])
    b, r = rate_at(c, 1, 6)
    assert b.tolist() == [1, 2, 3, 4, 5, 6]
    np.testing.assert_allclose(r[:4], c)
    assert np.isnan(r[4:]).all()                      # beyond the uploaded months
    b, r = rate_at(c, 3, 4)
    np.testing.assert_allclose(r, [0.1 / 0.7, 0.05 / 0.7])


def test_implied_lgd_matches_the_engine_derivation():
    # The engine's derived LGD beyond LastTS rolls the base row forward in exactly this way, so
    # feeding the base row's extended curve back in must reproduce the derived column.
    data = load_zip("44")
    res = compute(data, Params(target_ts=200, max_bucket=300, last_ts=0), builtin_curves()["44"])
    c = res.base_curves[2, 1:301]                     # client-shape extended cash curve of row 1
    lgd = implied_lgd(c, res.config["v"], 300, 200)
    np.testing.assert_allclose(lgd, res.lgd_ts["derived_client"], atol=1e-12)


def test_outstanding_basis_round_trip():
    from hazard_ext.engine.applied import to_outstanding
    h = np.array([0.5, 0.5, 0.5])
    np.testing.assert_allclose(to_outstanding(to_face(h, "outstanding")), h)
    c = np.array([0.2, 0.1, 0.7, 0.1])             # exhausted after step 3
    out = to_outstanding(c)
    np.testing.assert_allclose(out[:3], [0.2, 0.1 / 0.8, 0.7 / 0.7])
    assert np.isnan(out[3])


def test_implied_lgd_blank_once_the_balance_is_exhausted():
    c = np.array([1.0])                               # everything collected in month 1
    lgd = implied_lgd(c, 0.99, 12, 3)
    assert lgd[0] == pytest.approx(1 - 0.99 ** 1)     # discounted one month
    assert np.isnan(lgd[1:]).all()
