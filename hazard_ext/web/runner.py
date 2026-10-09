"""Runs scenarios against datasets and stores the results."""
from __future__ import annotations

import logging
import threading
from collections import OrderedDict

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..engine.core import EngineError, ExtensionResult, compute
from ..engine.params import Params, load_params, merge_params
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


def applied_curves(db: Session, project_id: int) -> dict[str, ClientCurve]:
    """The client's applied recovery curves uploaded to the project, by label."""
    rows = db.execute(select(ClientCurve).where(ClientCurve.project_id == project_id,
                                                ClientCurve.kind == "applied")).scalars()
    return {row.label: row for row in rows}


def effective_params(scenario: Scenario, override: ScenarioOverride | None) -> Params:
    return merge_params(scenario.params, override.params if override else None)


def vintage_text(cfg: dict) -> str:
    if not cfg.get("vintage_filter"):
        return ""
    return f"from {cfg['vintage_start_effective']} ({cfg['cohorts_included']} of {cfg['cohorts_total']} cohorts)"


def summarise(res: ExtensionResult) -> dict:
    j = res.to_json()
    a, c = j["averages"], j["config"]
    return {
        "lgd_file": a["lgd_file"], "lgd_replica": a["lgd_replica"], "lgd_exp": a["lgd_exp"],
        "lgd_power": a["lgd_power"], "lgd_logn": a["lgd_logn"],
        "lgd_selected": a["lgd_selected"], "uplift": a["uplift"],
        "uplift_simple": a["uplift_simple"], "exposure_total": a["exposure_total"],
        "method": c["method"], "method_label": c["method_label"],
        "lam": c["lam"], "gam": c["gam"], "mu": c["mu"], "sigma": c["sigma"], "rate": c["rate"],
        "vintage_filter": bool(c["vintage_filter"]), "vintages": vintage_text(c),
        "n": j["n"], "warnings": len(j["warnings"]),
    }


def run_one(db: Session, cache: DataCache, scenario: Scenario, dataset: Dataset, user: User) -> Result:
    """Run one scenario on one zip and upsert the Result row. Failures are recorded, not raised."""
    override = db.get(ScenarioOverride, (scenario.id, dataset.id))
    result = db.get(Result, (scenario.id, dataset.id))
    if result is None:
        result = Result(scenario_id=scenario.id, dataset_id=dataset.id, computed_by=user.id)
        db.add(result)
    result.computed_by = user.id
    result.computed_at = utcnow()
    result.stale = False
    result.curve_label = ""                 # kept for older rows; the engine takes no curve now
    try:
        params = effective_params(scenario, override)
        result.effective_params = params.model_dump()
        res = compute(cache.get(dataset.blob_key), params)
        result.status, result.error = "ok", ""
        result.payload = res.to_json()
        result.summary = summarise(res)
    except (EngineError, ParseError, StorageError, ValueError) as exc:
        result.status, result.error = "error", str(exc)
        result.payload, result.summary = {}, {}
    except Exception:                       # one zip must never take the whole run down
        log.exception("Unexpected error running scenario %s on dataset %s", scenario.id, dataset.id)
        result.status = "error"
        result.error = "The calculation failed unexpectedly. The details are in the server log"
        result.payload, result.summary = {}, {}
    db.flush()
    return result


def recompute(db: Session, cache: DataCache, result: Result) -> ExtensionResult:
    """Rebuild the full engine output (triangles included) for a stored result, using the
    parameters it was run with."""
    if result.status != "ok":
        raise EngineError(result.error or "This result has not been computed")
    if is_legacy_payload(result.payload):
        raise EngineError("This result was computed with the removed reference-curve method; run the scenario again")
    params = load_params(result.effective_params)
    return compute(cache.get(result.dataset.blob_key), params)


def is_legacy_payload(payload: dict | None) -> bool:
    """True for a result stored before the reference-curve shape was replaced by log-normal."""
    avg = (payload or {}).get("averages") or {}
    return "lgd_client" in avg or ("lgd_logn" not in avg and bool(avg))


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


def migrate_three_methods(session_factory) -> dict:
    """One-off data migration for the switch from the reference-curve shape to log-normal
    (9 October 2026). Idempotent and cheap, so it runs at every start.

    - removes the retired ``client_cohort`` key from scenario and override parameters;
    - marks every result that still holds reference-curve figures as stale (a stored
      method 3 keeps its number and now means log-normal; its old figures are never current).
    """
    from ..engine.params import RETIRED
    stripped = stale = 0
    with session_factory() as db:
        for s in db.execute(select(Scenario)).scalars():
            if any(k in (s.params or {}) for k in RETIRED):
                s.params = {k: v for k, v in s.params.items() if k not in RETIRED}
                stripped += 1
        for o in db.execute(select(ScenarioOverride)).scalars():
            if any(k in (o.params or {}) for k in RETIRED):
                o.params = {k: v for k, v in o.params.items() if k not in RETIRED}
                stripped += 1
        for r in db.execute(select(Result).where(Result.status == "ok", Result.stale.is_(False))).scalars():
            if is_legacy_payload(r.payload):
                r.stale = True
                stale += 1
        db.commit()
    if stripped or stale:
        log.info("Three-methods migration: %d parameter set(s) cleaned, %d result(s) marked out of date", stripped, stale)
    return {"stripped": stripped, "stale": stale}
