"""Scenarios, per-zip overrides, runs and results."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...engine.core import EngineError
from ...engine.params import DEFAULTS, Params, clean_overrides, merge_params
from ...engine.parse import ParseError
from ..deps import Access, csrf_guard, current_user, get_db, project_access
from ..models import iso, Dataset, Result, Scenario, ScenarioOverride, User, utcnow
from ..runner import applied_curves, mark_stale, project_curves, recompute, run_one
from ...engine.applied import implied_lgd, rate_at, to_face, to_outstanding
import numpy as np
from ..storage import StorageError

router = APIRouter(prefix="/api", dependencies=[Depends(csrf_guard)])


def _validation_text(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(str(x) for x in e['loc']) or 'parameters'}: {e['msg']}"
                         for e in exc.errors())
    return str(exc)


def _full_params(params: dict | None) -> dict:
    try:
        return merge_params(params or {}).model_dump()
    except (ValidationError, ValueError) as exc:
        raise HTTPException(422, _validation_text(exc)) from None


def _scenario(db: Session, pid: int, sid: int) -> Scenario:
    s = db.get(Scenario, sid)
    if s is None or s.project_id != pid:
        raise HTTPException(404, "Scenario not found")
    return s


def _dataset(db: Session, pid: int, did: int) -> Dataset:
    d = db.get(Dataset, did)
    if d is None or d.project_id != pid:
        raise HTTPException(404, "Zip not found")
    return d


def result_meta(r: Result | None) -> dict:
    if r is None:
        return {"status": "none"}
    return {"status": r.status, "error": r.error, "stale": r.stale, "summary": r.summary,
            "curve_label": r.curve_label, "computed_at": iso(r.computed_at)}


def scenario_out(s: Scenario, detail: bool = False) -> dict:
    out = {"id": s.id, "name": s.name, "description": s.description, "params": s.params,
           "updated_at": iso(s.updated_at)}
    if detail:
        out["overrides"] = {str(o.dataset_id): o.params for o in s.overrides}
    return out


@router.get("/meta/params")
def params_meta(_: User = Depends(current_user)):
    """Defaults and descriptions of every scenario parameter, for building the form."""
    schema = Params.model_json_schema()["properties"]
    return {"defaults": DEFAULTS,
            "fields": [{"name": k, "description": v.get("description", "")} for k, v in schema.items()]}


# ------------------------------------------------------------------- scenarios
class ScenarioIn(BaseModel):
    name: str
    description: str = ""
    params: dict = {}


class ScenarioPut(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    params: Optional[dict] = None


def _check_name(db: Session, pid: int, name: str, ignore_id: int | None = None) -> str:
    name = name.strip()
    if not name:
        raise HTTPException(422, "The scenario name cannot be empty")
    if len(name) > 200:
        raise HTTPException(422, "The scenario name is too long")
    stmt = select(Scenario).where(Scenario.project_id == pid, Scenario.name == name)
    if ignore_id is not None:
        stmt = stmt.where(Scenario.id != ignore_id)
    if db.execute(stmt).scalar_one_or_none():
        raise HTTPException(409, "Another scenario in this project already has that name")
    return name


@router.get("/projects/{pid}/scenarios")
def list_scenarios(access: Access = Depends(project_access()), db: Session = Depends(get_db)):
    rows = db.execute(select(Scenario).where(Scenario.project_id == access.project.id)
                      .order_by(Scenario.name)).scalars()
    return [scenario_out(s, detail=True) for s in rows]


@router.post("/projects/{pid}/scenarios", status_code=201)
def create_scenario(body: ScenarioIn, access: Access = Depends(project_access("editor")),
                    db: Session = Depends(get_db)):
    pid = access.project.id
    # New scenarios start on method 1, which needs no reference curve; the engine default stays 3
    # so that Params() still equals the workbook Config.
    params = {"method": 1, **body.params}
    s = Scenario(project_id=pid, name=_check_name(db, pid, body.name),
                 description=body.description.strip(), params=_full_params(params),
                 created_by=access.user.id)
    db.add(s)
    db.commit()
    return scenario_out(s, detail=True)


@router.get("/projects/{pid}/scenarios/{sid}")
def get_scenario(sid: int, access: Access = Depends(project_access()), db: Session = Depends(get_db)):
    return scenario_out(_scenario(db, access.project.id, sid), detail=True)


@router.put("/projects/{pid}/scenarios/{sid}")
def update_scenario(sid: int, body: ScenarioPut, access: Access = Depends(project_access("editor")),
                    db: Session = Depends(get_db)):
    s = _scenario(db, access.project.id, sid)
    if body.name is not None:
        s.name = _check_name(db, s.project_id, body.name, ignore_id=s.id)
    if body.description is not None:
        s.description = body.description.strip()
    if body.params is not None:
        new = _full_params(body.params)
        if new != s.params:
            for o in s.overrides:                 # the overrides must still be valid on the new base
                try:
                    merge_params(new, o.params)
                except (ValidationError, ValueError) as exc:
                    raise HTTPException(422, f"Override for zip {o.dataset.name}: {_validation_text(exc)}") from None
            s.params = new
            mark_stale(db, scenario_id=s.id)
    s.updated_at = utcnow()
    db.commit()
    return scenario_out(s, detail=True)


@router.delete("/projects/{pid}/scenarios/{sid}")
def delete_scenario(sid: int, access: Access = Depends(project_access("editor")), db: Session = Depends(get_db)):
    db.delete(_scenario(db, access.project.id, sid))
    db.commit()
    return {"ok": True}


class CloneIn(BaseModel):
    name: str


@router.post("/projects/{pid}/scenarios/{sid}/clone", status_code=201)
def clone_scenario(sid: int, body: CloneIn, access: Access = Depends(project_access("editor")),
                   db: Session = Depends(get_db)):
    src = _scenario(db, access.project.id, sid)
    s = Scenario(project_id=src.project_id, name=_check_name(db, src.project_id, body.name),
                 description=src.description, params=dict(src.params), created_by=access.user.id)
    s.overrides = [ScenarioOverride(dataset_id=o.dataset_id, params=dict(o.params)) for o in src.overrides]
    db.add(s)
    db.commit()
    return scenario_out(s, detail=True)


# ------------------------------------------------------------------- overrides
class OverrideIn(BaseModel):
    params: dict = {}


@router.put("/projects/{pid}/scenarios/{sid}/overrides/{did}")
def set_override(sid: int, did: int, body: OverrideIn, access: Access = Depends(project_access("editor")),
                 db: Session = Depends(get_db)):
    """Replace this zip's override. An empty object removes it."""
    s = _scenario(db, access.project.id, sid)
    d = _dataset(db, access.project.id, did)
    try:
        params = clean_overrides(body.params)
        merge_params(s.params, params)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(422, _validation_text(exc)) from None
    row = db.get(ScenarioOverride, (s.id, d.id))
    before = row.params if row else {}
    if params:
        if row is None:
            db.add(ScenarioOverride(scenario_id=s.id, dataset_id=d.id, params=params))
        else:
            row.params = params
    elif row is not None:
        db.delete(row)
    if params != before:
        mark_stale(db, scenario_id=s.id, dataset_id=d.id)
        s.updated_at = utcnow()
    db.commit()
    db.refresh(s)
    return scenario_out(s, detail=True)


# ------------------------------------------------------------------------ runs
class RunIn(BaseModel):
    dataset_id: Optional[int] = None


@router.post("/projects/{pid}/scenarios/{sid}/run")
def run_scenario(sid: int, body: RunIn, request: Request,
                 access: Access = Depends(project_access("editor")), db: Session = Depends(get_db)):
    """Run the scenario on one zip, or on every zip in the project."""
    pid = access.project.id
    s = _scenario(db, pid, sid)
    if body.dataset_id is not None:
        datasets = [_dataset(db, pid, body.dataset_id)]
    else:
        datasets = list(db.execute(select(Dataset).where(Dataset.project_id == pid).order_by(Dataset.name)).scalars())
    if not datasets:
        raise HTTPException(400, "Upload at least one zip before running a scenario")
    curves = project_curves(db, pid)
    out = []
    for d in datasets:
        r = run_one(db, request.app.state.cache, s, d, access.user, curves)
        out.append({"dataset_id": d.id, "dataset": d.name, **result_meta(r)})
    db.commit()
    return out


@router.post("/projects/{pid}/run-all")
def run_everything(request: Request, access: Access = Depends(project_access("editor")),
                   db: Session = Depends(get_db)):
    """Run every scenario on every zip."""
    pid = access.project.id
    scenarios = list(db.execute(select(Scenario).where(Scenario.project_id == pid)).scalars())
    datasets = list(db.execute(select(Dataset).where(Dataset.project_id == pid)).scalars())
    curves = project_curves(db, pid)
    n_ok = n_err = 0
    for s in scenarios:
        for d in datasets:
            r = run_one(db, request.app.state.cache, s, d, access.user, curves)
            n_ok += r.status == "ok"
            n_err += r.status == "error"
    db.commit()
    return {"ok": n_ok, "errors": n_err}


@router.get("/projects/{pid}/matrix")
def matrix(access: Access = Depends(project_access()), db: Session = Depends(get_db)):
    """Every scenario against every zip: status and headline figures."""
    pid = access.project.id
    scenarios = list(db.execute(select(Scenario).where(Scenario.project_id == pid).order_by(Scenario.name)).scalars())
    datasets = list(db.execute(select(Dataset).where(Dataset.project_id == pid).order_by(Dataset.name)).scalars())
    results = {(r.scenario_id, r.dataset_id): r for r in db.execute(
        select(Result).join(Scenario, Scenario.id == Result.scenario_id).where(Scenario.project_id == pid)).scalars()}
    return {
        "scenarios": [{"id": s.id, "name": s.name} for s in scenarios],
        "datasets": [{"id": d.id, "name": d.name, "category": d.category} for d in datasets],
        "cells": [{"scenario_id": s.id, "dataset_id": d.id,
                   "has_override": any(o.dataset_id == d.id for o in s.overrides),
                   **result_meta(results.get((s.id, d.id)))}
                  for s in scenarios for d in datasets],
    }


@router.get("/projects/{pid}/series")
def series(access: Access = Depends(project_access()), db: Session = Depends(get_db)):
    """Final LGD by TermStep for every computed result in the project, for comparison charts.
    Light: only the final series, not the whole payload."""
    pid = access.project.id
    rows = db.execute(select(Result).join(Scenario, Scenario.id == Result.scenario_id)
                      .where(Scenario.project_id == pid, Result.status == "ok")).scalars()
    out = []
    for r in rows:
        lt = (r.payload or {}).get("lgd_ts") or {}
        if not lt.get("lgd_final"):
            continue
        out.append({"scenario_id": r.scenario_id, "dataset_id": r.dataset_id, "stale": r.stale,
                    "ts": lt["ts"], "lgd_final": lt["lgd_final"],
                    "lgd_selected_avg": (r.summary or {}).get("lgd_selected")})
    return out


# --------------------------------------------------------------------- results
def _result(db: Session, pid: int, sid: int, did: int) -> Result:
    _scenario(db, pid, sid)
    _dataset(db, pid, did)
    r = db.get(Result, (sid, did))
    if r is None:
        raise HTTPException(404, "This scenario has not been run for this zip")
    return r


@router.get("/projects/{pid}/results/{sid}/{did}")
def get_result(sid: int, did: int, access: Access = Depends(project_access()), db: Session = Depends(get_db)):
    r = _result(db, access.project.id, sid, did)
    return {**result_meta(r), "effective_params": r.effective_params, "payload": r.payload,
            "scenario": {"id": r.scenario.id, "name": r.scenario.name},
            "dataset": {"id": r.dataset.id, "name": r.dataset.name, "category": r.dataset.category}}


def _applied_for(db: Session, r: Result):
    """The client's applied curve for a result's zip (label = the zip's category), on the face basis."""
    row = applied_curves(db, r.scenario.project_id).get(r.dataset.category or "")
    if row is None:
        return None, None
    return row, to_face(np.asarray(row.values, dtype=float), row.basis)


@router.get("/projects/{pid}/results/{sid}/{did}/curve")
def get_curve_data(sid: int, did: int, request: Request, ts: int = 1,
                   access: Access = Depends(project_access()), db: Session = Depends(get_db)):
    """Observed and extended RecoveryPct along one TermStep row, plus the client's applied
    curve rolled forward to that TermStep when the project has one for the zip."""
    r = _result(db, access.project.id, sid, did)
    try:
        out = recompute(db, request.app.state.cache, r).curve(ts)
    except (EngineError, ParseError, StorageError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from None
    row, c_face = _applied_for(db, r)
    if row is not None:
        _, rates = rate_at(c_face, ts, out["bucket"][-1])
        out["applied"] = [None if not np.isfinite(x) else float(x) for x in rates]
        out["applied_label"] = row.label
        out["applied_basis"] = row.basis
    return out


@router.get("/projects/{pid}/results/{sid}/{did}/compare_curves")
def compare_curves(sid: int, did: int, request: Request, ts: int = 1, basis: str = "face",
                   access: Access = Depends(project_access()), db: Session = Depends(get_db)):
    """The client's applied curve against the observed data and the three fitted tails at one
    TermStep, every series on the same basis: face (share of the balance at the TermStep) or
    outstanding (share of what is still outstanding at the start of each step)."""
    if basis not in ("face", "outstanding"):
        raise HTTPException(422, "basis must be face or outstanding")
    r = _result(db, access.project.id, sid, did)
    try:
        cur = recompute(db, request.app.state.cache, r).curve(ts)
    except (EngineError, ParseError, StorageError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from None
    row, c_face = _applied_for(db, r)
    mb = cur["bucket"][-1]
    face = {
        "observed": np.array([np.nan if v is None else v for v in cur["observed"]], dtype=float),
        "exp": np.array(cur["exp"], dtype=float), "power": np.array(cur["power"], dtype=float),
        "reference": np.array([np.nan if v is None else v for v in cur["client"]], dtype=float),
        "reference_curve": np.array([np.nan if v is None else v for v in cur["client_curve"]], dtype=float) if ts == 1 else None,
        "applied": rate_at(c_face, ts, mb)[1] if row is not None else None,
    }
    shown = {k: (to_outstanding(v) if basis == "outstanding" else v) for k, v in face.items() if v is not None}
    cfg = r.payload["config"]
    sel = {1: "exp", 2: "power", 3: "reference"}[int(cfg["method"])]
    # cumulative recovery on the face basis at a few horizons, our selected tail against the client's curve
    marks = [m for m in (12, 24, 36, 48, 60, 120, 180, 240, 300, 360, 480) if m <= mb - ts + 1]
    if marks and marks[-1] != mb - ts + 1:
        marks.append(mb - ts + 1)

    def cum(series):
        s = np.where(np.isfinite(series), series, 0.0)
        return np.cumsum(s)

    ours, theirs = cum(face[sel]), cum(face["applied"]) if row is not None else None
    table = []
    for m in marks:
        known = row is not None and np.isfinite(face["applied"][:m]).all()
        table.append({"months": m, "ours": float(ours[m - 1]),
                      "client": float(theirs[m - 1]) if known else None,
                      "difference": float(ours[m - 1] - theirs[m - 1]) if known else None})
    return {
        "ts": ts, "basis": basis, "bucket": cur["bucket"], "last_cred": cur["last_cred"], "selected": sel,
        "series": {k: [None if not np.isfinite(x) else float(x) for x in v] for k, v in shown.items()},
        "applied_label": row.label if row is not None else None, "applied_basis": row.basis if row is not None else None,
        "reference_label": cfg.get("client_cohort"), "method_label": cfg["method_label"], "cumulative": table,
    }


@router.get("/projects/{pid}/results/{sid}/{did}/applied")
def get_applied_comparison(sid: int, did: int, access: Access = Depends(project_access()),
                           db: Session = Depends(get_db)):
    """The LGD implied by the client's applied curve at every TermStep of the result's LGD table,
    discounted at the rate the result used and counting buckets to its MaxBucket."""
    r = _result(db, access.project.id, sid, did)
    row, c_face = _applied_for(db, r)
    if row is None:
        return {"available": False, "label": r.dataset.category}
    if r.status != "ok":
        return {"available": False, "label": row.label, "reason": "The result has not been computed"}
    cfg = r.payload["config"]
    lgd = implied_lgd(c_face, cfg["v"], int(cfg["max_bucket"]), int(cfg["target_ts"]))
    return {"available": True, "label": row.label, "basis": row.basis, "months": len(c_face),
            "ts": list(range(1, int(cfg["target_ts"]) + 1)),
            "lgd": [None if not np.isfinite(x) else float(x) for x in lgd]}
