"""Excel and CSV downloads."""
from __future__ import annotations

import re

import numpy as np
from sqlalchemy import select

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ...engine.core import EngineError
from ...export.formula_xlsx import build_formula_workbook
from ...engine.applied import to_face
from ...export.curves_xlsx import build_curves_workbook
from ...export.summary_xlsx import build_summary_workbook
from ...export.tables import TABLES, csv_table
from ...export.values_xlsx import build_values_workbook
from ..deps import Access, get_db, project_access
from ..models import Result, Scenario, iso
from ..runner import applied_curves, recompute
from ..storage import StorageError
from .scenarios import _result

router = APIRouter(prefix="/api")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "export"


def _download(content: bytes | str, filename: str, media: str) -> Response:
    return Response(content=content, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/projects/{pid}/results/{sid}/{did}/export")
def export_result(sid: int, did: int, request: Request, kind: str = "values", table: str = "results",
                  access: Access = Depends(project_access()), db: Session = Depends(get_db)):
    """kind = values (Excel, values only), formula (Excel, live formulas) or csv (one table)."""
    r = _result(db, access.project.id, sid, did)
    if r.status != "ok":
        raise HTTPException(400, "This result has an error and cannot be exported: " + r.error)
    stem = f"{_safe(r.dataset.name)}_{_safe(r.scenario.name)}"
    if kind == "csv":
        if table not in TABLES:
            raise HTTPException(422, "table must be one of: " + ", ".join(TABLES))
        return _download(csv_table(r.payload, table), f"{stem}_{table}.csv", "text/csv; charset=utf-8")
    if kind not in ("values", "formula"):
        raise HTTPException(422, "kind must be values, formula or csv")
    if r.stale:
        # the workbook is rebuilt from the current zip and curves; a stale result was run on older ones
        raise HTTPException(409, "The inputs changed after this run. Run the scenario again, then export")
    cache = request.app.state.cache
    try:
        res = recompute(db, cache, r)
        if kind == "values":
            body = build_values_workbook(res, r.dataset.name, r.scenario.name, r.dataset.filename)
        else:
            body = build_formula_workbook(res, cache.get(r.dataset.blob_key), r.dataset.name, r.scenario.name)
    except (EngineError, StorageError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from None
    return _download(body, f"{stem}_{kind}.xlsx", XLSX)


@router.get("/projects/{pid}/export/curves")
def export_curves(request: Request, scenario_id: int, access: Access = Depends(project_access()),
                  db: Session = Depends(get_db)):
    """LGD and marginal recovery curves for every cohort under one scenario."""
    pid = access.project.id
    s = db.get(Scenario, scenario_id)
    if s is None or s.project_id != pid:
        raise HTTPException(404, "Scenario not found")
    rows = db.execute(select(Result).where(Result.scenario_id == s.id, Result.status == "ok")).scalars().all()
    if not rows:
        raise HTTPException(400, "This scenario has not been run for any zip yet")
    applied = {label: to_face(np.asarray(row.values, dtype=float), row.basis)
               for label, row in applied_curves(db, pid).items()}
    cache = request.app.state.cache
    try:
        items = [{"zip": r.dataset.name, "category": r.dataset.category, "stale": r.stale,
                  "applied_label": r.dataset.category if r.dataset.category in applied else None,
                  "res": recompute(db, cache, r)} for r in rows]
        body = build_curves_workbook(access.project.name, s.name, items, applied)
    except (EngineError, StorageError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from None
    return _download(body, f"{_safe(access.project.name)}_{_safe(s.name)}_curves.xlsx", XLSX)


@router.get("/projects/{pid}/export/summary")
def export_summary(request: Request, access: Access = Depends(project_access()), db: Session = Depends(get_db)):
    """Every computed result in the project in one workbook, with marginal recoveries and analytics."""
    pid = access.project.id
    rows = db.execute(select(Result).join(Scenario, Scenario.id == Result.scenario_id)
                      .where(Scenario.project_id == pid, Result.status == "ok")).scalars().all()
    if not rows:
        raise HTTPException(400, "Nothing has been run in this project yet")
    applied = {label: to_face(np.asarray(row.values, dtype=float), row.basis)
               for label, row in applied_curves(db, pid).items()}
    cache = request.app.state.cache
    items = []
    try:
        for r in rows:
            items.append({
                "zip": r.dataset.name, "category": r.dataset.category, "scenario": r.scenario.name,
                "scenario_id": r.scenario_id, "dataset_id": r.dataset_id, "stale": r.stale,
                "computed_at": iso(r.computed_at) or "", "applied_label": r.dataset.category if r.dataset.category in applied else None,
                "res": recompute(db, cache, r),
            })
        body = build_summary_workbook(access.project.name, items, applied)
    except (EngineError, StorageError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from None
    return _download(body, f"{_safe(access.project.name)}_summary.xlsx", XLSX)
