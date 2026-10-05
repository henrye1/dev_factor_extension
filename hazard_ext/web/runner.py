"""Runs scenarios against datasets and stores the results."""
from __future__ import annotations

import logging
import threading
from collections import OrderedDict

import numpy as np
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..engine.core import EngineError, ExtensionResult, compute
from ..engine.params import Params, merge_params
from ..engine.parse import ParseError, RecoveryData
from .models import ClientCurve, Dataset, Result, Scenario, ScenarioOverride, User, utcnow
from .storage import BlobStore, StorageError


log = logging.getLogger("hazard_ext")


class DataCache:
    """Small LRU of parsed datasets, so charts and exports do not re-read storage each time."""

    def __init__(self, store: BlobStore, capacity: int = 12):
        self.store = store
        self.capacity = capacity
        self._items: OrderedDict[str, RecoveryData] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, blob_key: str) -> RecoveryData:
        with self._lock:
            if blob_key in self._items:
                self._items.move_to_end(blob_key)
                return self._items[blob_key]
        data = RecoveryData.from_bytes(self.store.get(blob_key))
        with self._lock:
            self._items[blob_key] = data
            while len(self._items) > self.capacity:
                self._items.popitem(last=False)
        return data

    def drop(self, blob_key: str) -> None:
        with self._lock:
            self._items.pop(blob_key, None)


def project_curves(db: Session, project_id: int) -> dict[str, np.ndarray]:
    """Shape curves for method 3: the curves uploaded to the project, by label."""
    curves = {}
    rows = db.execute(select(ClientCurve).where(ClientCurve.project_id == project_id,
                                                ClientCurve.kind == "shape")).scalars()
    for row in rows:
        curves[row.label] = np.asarray(row.values, dtype=float)
    return curves


def applied_curves(db: Session, project_id: int) -> dict[str, ClientCurve]:
    """The client's applied recovery curves uploaded to the project, by label."""
    rows = db.execute(select(ClientCurve).where(ClientCurve.project_id == project_id,
                                                ClientCurve.kind == "applied")).scalars()
    return {row.label: row for row in rows}


def effective_params(scenario: Scenario, override: ScenarioOverride | None) -> Params:
    return merge_params(scenario.params, override.params if override else None)


def choose_curve(params: Params, dataset: Dataset, curves: dict[str, np.ndarray]):
    """The reference curve for a zip: the chosen label, else the zip's own Category1."""
    label = params.client_cohort or dataset.category
    curve = curves.get(label)
    if curve is None and params.method == 3:
        raise EngineError(
            f"There is no reference curve '{label}' for this zip. Choose a reference curve in the "
            f"scenario or in this zip's override, upload one, or use method 1 or 2")
    return (label if curve is not None else ""), curve


def summarise(res: ExtensionResult) -> dict:
    j = res.to_json()
    a, c = j["averages"], j["config"]
    return {
        "lgd_file": a["lgd_file"], "lgd_replica": a["lgd_replica"], "lgd_exp": a["lgd_exp"],
        "lgd_power": a["lgd_power"], "lgd_client": a["lgd_client"],
        "lgd_selected": a["lgd_selected"], "uplift": a["uplift"],
        "uplift_simple": a["uplift_simple"], "exposure_total": a["exposure_total"],
        "method": c["method"], "method_label": c["method_label"],
        "lam": c["lam"], "gam": c["gam"], "rate": c["rate"],
        "n": j["n"], "warnings": len(j["warnings"]),
    }


def run_one(db: Session, cache: DataCache, scenario: Scenario, dataset: Dataset, user: User,
            curves: dict[str, np.ndarray] | None = None) -> Result:
    """Run one scenario on one zip and upsert the Result row. Failures are recorded, not raised."""
    override = db.get(ScenarioOverride, (scenario.id, dataset.id))
    result = db.get(Result, (scenario.id, dataset.id))
    if result is None:
        result = Result(scenario_id=scenario.id, dataset_id=dataset.id, computed_by=user.id)
        db.add(result)
    result.computed_by = user.id
    result.computed_at = utcnow()
    result.stale = False
    try:
        params = effective_params(scenario, override)
        result.effective_params = params.model_dump()
        label, curve = choose_curve(params, dataset, curves or project_curves(db, scenario.project_id))
        res = compute(cache.get(dataset.blob_key), params, curve)
        result.status, result.error = "ok", ""
        result.curve_label = label
        result.payload = res.to_json()
        result.summary = summarise(res)
    except (EngineError, ParseError, StorageError, ValueError) as exc:
        result.status, result.error = "error", str(exc)
        result.payload, result.summary, result.curve_label = {}, {}, ""
    except Exception:                       # one zip must never take the whole run down
        log.exception("Unexpected error running scenario %s on dataset %s", scenario.id, dataset.id)
        result.status = "error"
        result.error = "The calculation failed unexpectedly. The details are in the server log"
        result.payload, result.summary, result.curve_label = {}, {}, ""
    db.flush()
    return result


def recompute(db: Session, cache: DataCache, result: Result) -> ExtensionResult:
    """Rebuild the full engine output (triangles included) for a stored result, using the
    parameters it was run with."""
    if result.status != "ok":
        raise EngineError(result.error or "This result has not been computed")
    params = Params(**result.effective_params)
    curves = project_curves(db, result.scenario.project_id)
    curve = curves.get(result.curve_label) if result.curve_label else None
    if params.method == 3 and curve is None:
        raise EngineError(f"The client curve '{result.curve_label}' used by this result no longer "
                          f"exists; run the scenario again")
    return compute(cache.get(result.dataset.blob_key), params, curve)


def mark_stale(db: Session, *, scenario_id: int | None = None, dataset_id: int | None = None,
               project_id: int | None = None) -> None:
    """Flag stored results that no longer reflect the current inputs."""
    stmt = update(Result).values(stale=True)
    if scenario_id is not None:
        stmt = stmt.where(Result.scenario_id == scenario_id)
    if dataset_id is not None:
        stmt = stmt.where(Result.dataset_id == dataset_id)
    if project_id is not None:
        stmt = stmt.where(Result.scenario_id.in_(
            select(Scenario.id).where(Scenario.project_id == project_id)))
    db.execute(stmt)
