"""Excel and CSV downloads."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...engine.core import EngineError
from ...export._xlsx import safe_name
from ...export.curves_xlsx import build_curves_workbook
from ...export.formula_xlsx import build_formula_workbook
from ...export.results_xlsx import build_results_bundle, build_results_workbook
from ...export.summary_xlsx import build_summary_workbook
from ...export.tables import TABLES, csv_table
from ...export.values_xlsx import build_values_workbook
from ..deps import Access, get_db, project_access, result_or_404, scenario_or_404
from ..models import Result, Scenario, iso
from ..runner import applied_face_curves, recompute
from ..storage import StorageError

router = APIRouter(prefix="/api")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# what an export may raise that is the user's to fix, reported as 400 rather than 500
_EXPORT_ERRORS = (EngineError, StorageError, ValueError)


def _download(content: bytes | str, filename: str, media: str) -> Response:
    return Response(content=content, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _item(db: Session, cache, r: Result, applied: dict | None = None) -> dict:
    """One computed result as the export builders take it (the engine output rebuilt in full)."""
    category = r.dataset.category
    return {"zip": r.dataset.name, "category": category, "stale": r.stale, "filename": r.dataset.filename,
            "computed_at": iso(r.computed_at) or "", "scenario": r.scenario.name,
            "scenario_id": r.scenario_id, "dataset_id": r.dataset_id,
            "applied_label": category if applied is not None and category in applied else None,
            "res": recompute(db, cache, r)}


def _scenario_results(db: Session, pid: int, scenario_id: int) -> tuple[Scenario, list[Result]]:
    s = scenario_or_404(db, pid, scenario_id)
    rows = db.execute(select(Result).where(Result.scenario_id == s.id, Result.status == "ok")).scalars().all()
    if not rows:
        raise HTTPException(400, "This scenario has not been run for any zip yet")
    return s, rows


@router.get("/projects/{pid}/results/{sid}/{did}/export")
def export_result(sid: int, did: int, request: Request, kind: str = "values", table: str = "results",
                  access: Access = Depends(project_access()), db: Session = Depends(get_db)):
    """kind = values (Excel, values only), formula (Excel, live formulas) or csv (one table)."""
    r = result_or_404(db, access.project.id, sid, did)
    if r.status != "ok":
        raise HTTPException(400, "This result has an error and cannot be exported: " + r.error)
    stem = f"{safe_name(r.dataset.name)}_{safe_name(r.scenario.name)}"
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
    except _EXPORT_ERRORS as exc:
        raise HTTPException(400, str(exc)) from None
    return _download(body, f"{stem}_{kind}.xlsx", XLSX)


@router.get("/projects/{pid}/export/curves")
def export_curves(request: Request, scenario_id: int, access: Access = Depends(project_access()),
                  db: Session = Depends(get_db)):
    """LGD and marginal recovery curves for every cohort under one scenario."""
    pid = access.project.id
    s, rows = _scenario_results(db, pid, scenario_id)
    applied = applied_face_curves(db, pid)
    try:
        items = [_item(db, request.app.state.cache, r, applied) for r in rows]
        body = build_curves_workbook(access.project.name, s.name, items, applied)
    except _EXPORT_ERRORS as exc:
        raise HTTPException(400, str(exc)) from None
    return _download(body, f"{safe_name(access.project.name)}_{safe_name(s.name)}_curves.xlsx", XLSX)


@router.get("/projects/{pid}/export/results")
def export_results(request: Request, scenario_id: int, bundle: str = "xlsx",
                   access: Access = Depends(project_access()), db: Session = Depends(get_db)):
    """Every cohort's results under one scenario: one workbook (bundle=xlsx) with the full
    tables of every cohort stacked, or a zip of each cohort's values workbook (bundle=zip)."""
    if bundle not in ("xlsx", "zip"):
        raise HTTPException(422, "bundle must be xlsx or zip")
    s, rows = _scenario_results(db, access.project.id, scenario_id)
    stem = f"{safe_name(access.project.name)}_{safe_name(s.name)}"
    try:
        items = [_item(db, request.app.state.cache, r) for r in rows]
        if bundle == "zip":
            return _download(build_results_bundle(s.name, items), f"{stem}_workbooks.zip", "application/zip")
        body = build_results_workbook(access.project.name, s.name, items)
    except _EXPORT_ERRORS as exc:
        raise HTTPException(400, str(exc)) from None
    return _download(body, f"{stem}_all_cohorts.xlsx", XLSX)


@router.get("/projects/{pid}/export/summary")
def export_summary(request: Request, access: Access = Depends(project_access()), db: Session = Depends(get_db)):
    """Every computed result in the project in one workbook, with marginal recoveries and analytics."""
    pid = access.project.id
    rows = db.execute(select(Result).join(Scenario, Scenario.id == Result.scenario_id)
                      .where(Scenario.project_id == pid, Result.status == "ok")).scalars().all()
    if not rows:
        raise HTTPException(400, "Nothing has been run in this project yet")
    applied = applied_face_curves(db, pid)
    try:
        items = [_item(db, request.app.state.cache, r, applied) for r in rows]
        body = build_summary_workbook(access.project.name, items, applied)
    except _EXPORT_ERRORS as exc:
        raise HTTPException(400, str(exc)) from None
    return _download(body, f"{safe_name(access.project.name)}_summary.xlsx", XLSX)
