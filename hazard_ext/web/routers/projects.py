"""Projects, members, datasets (zips) and curves."""
from __future__ import annotations

import hashlib
import logging
import uuid
from pathlib import PurePath
from typing import Optional

import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...engine.applied import to_face
from ...engine.curves import CurveError, parse_curve_file
from ...engine.parse import ParseError, parse_zip
from ..deps import Access, csrf_guard, current_user, get_db, project_access
from ..models import (iso, utcnow, CURVE_BASES, CURVE_KINDS, ROLES, ClientCurve, Dataset, Project,
                      ProjectMember, Result, Scenario, User)
from ..runner import applied_curves, is_legacy_result, mark_stale
from ..storage import StorageError

router = APIRouter(prefix="/api", dependencies=[Depends(csrf_guard)])
log = logging.getLogger("hazard_ext")


# ---------------------------------------------------------------------- output
def dataset_out(d: Dataset) -> dict:
    return {"id": d.id, "name": d.name, "category": d.category, "filename": d.filename,
            "sha256": d.sha256, "size_bytes": d.size_bytes, "profile": d.profile,
            "parameters": (d.meta or {}).get("Parameters", {}),
            "generated_at": (d.meta or {}).get("GeneratedAt"),
            "uploaded_at": iso(d.uploaded_at)}


def curve_rows(db: Session, project_id: int) -> list[dict]:
    """The client's applied curves uploaded to the project. Rows of the retired reference-shape
    kind stay in the table but are not listed."""
    rows = []
    for label, row in sorted(applied_curves(db, project_id).items()):
        values = to_face(np.asarray(row.values, dtype=float), row.basis)
        nz = np.nonzero(values > 0)[0]
        rows.append({
            "label": label, "id": row.id, "kind": "applied", "basis": row.basis,
            "source": "uploaded", "source_filename": row.source_filename,
            "length": int(len(values)), "last_nonzero_t": int(nz[-1] + 1) if nz.size else 0,
            "total": float(values.sum()),
        })
    return rows


def _unique_name(db: Session, project_id: int, base: str) -> str:
    taken = set(db.execute(select(Dataset.name).where(Dataset.project_id == project_id)).scalars())
    if base not in taken:
        return base
    i = 2
    while f"{base} ({i})" in taken:
        i += 1
    return f"{base} ({i})"


# -------------------------------------------------------------------- projects
class ProjectIn(BaseModel):
    name: str
    description: str = ""


class ProjectPatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


def _clean_name(name: str, what: str = "name") -> str:
    name = name.strip()
    if not name:
        raise HTTPException(422, f"The {what} cannot be empty")
    if len(name) > 200:
        raise HTTPException(422, f"The {what} is too long")
    return name


@router.get("/projects")
def list_projects(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.is_admin:
        rows = [(p, "owner") for p in db.execute(select(Project).order_by(Project.name)).scalars()]
    else:
        rows = db.execute(
            select(Project, ProjectMember.role).join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(ProjectMember.user_id == user.id).order_by(Project.name)).all()
    out = []
    for p, role in rows:
        n_data = db.execute(select(func.count()).select_from(Dataset).where(Dataset.project_id == p.id)).scalar_one()
        n_scen = db.execute(select(func.count()).select_from(Scenario).where(Scenario.project_id == p.id)).scalar_one()
        out.append({"id": p.id, "name": p.name, "description": p.description, "role": role,
                    "datasets": n_data, "scenarios": n_scen, "created_at": iso(p.created_at)})
    return out


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project = Project(name=_clean_name(body.name), description=body.description.strip(), created_by=user.id)
    project.members.append(ProjectMember(user_id=user.id, role="owner"))
    db.add(project)
    db.commit()
    return {"id": project.id, "name": project.name, "description": project.description, "role": "owner"}


@router.get("/projects/{pid}")
def get_project(access: Access = Depends(project_access()), db: Session = Depends(get_db)):
    p = access.project
    datasets = db.execute(select(Dataset).where(Dataset.project_id == p.id).order_by(Dataset.name)).scalars()
    scenarios = db.execute(select(Scenario).where(Scenario.project_id == p.id).order_by(Scenario.name)).scalars()
    legacy = sum(1 for r in db.execute(
        select(Result).join(Scenario, Scenario.id == Result.scenario_id)
        .where(Scenario.project_id == p.id, Result.status == "ok", Result.stale.is_(True))).scalars()
        if (r.effective_params or {}).get("method") == 3 and is_legacy_result(r))
    return {
        "id": p.id, "name": p.name, "description": p.description, "role": access.role,
        "datasets": [dataset_out(d) for d in datasets],
        "scenarios": [{"id": s.id, "name": s.name, "description": s.description,
                       "updated_at": iso(s.updated_at)} for s in scenarios],
        "curves": curve_rows(db, p.id),
        # results that still hold the removed reference-curve method's figures (need running again)
        "legacy_method3": legacy,
    }


@router.patch("/projects/{pid}")
def patch_project(body: ProjectPatch, access: Access = Depends(project_access("owner")),
                  db: Session = Depends(get_db)):
    p = db.get(Project, access.project.id)
    if body.name is not None:
        p.name = _clean_name(body.name)
    if body.description is not None:
        p.description = body.description.strip()
    db.commit()
    return {"id": p.id, "name": p.name, "description": p.description}


@router.delete("/projects/{pid}")
def delete_project(request: Request, access: Access = Depends(project_access("owner")),
                   db: Session = Depends(get_db)):
    p = db.get(Project, access.project.id)
    keys = [d.blob_key for d in p.datasets]
    db.delete(p)
    db.commit()
    _drop_blobs(request, keys)
    return {"ok": True}


def _drop_blobs(request: Request, keys: list[str]) -> None:
    for key in keys:
        request.app.state.cache.drop(key)
        try:
            request.app.state.store.delete(key)
        except StorageError:
            pass                                  # the row is gone; an orphan blob is harmless


# --------------------------------------------------------------------- members
class MemberIn(BaseModel):
    email: str
    role: str = "viewer"


def _members(db: Session, pid: int) -> list[dict]:
    rows = db.execute(select(ProjectMember, User).join(User, User.id == ProjectMember.user_id)
                      .where(ProjectMember.project_id == pid).order_by(User.email)).all()
    return [{"user_id": u.id, "email": u.email, "name": u.name, "role": m.role,
             "is_active": u.is_active} for m, u in rows]


def _owner_count(db: Session, pid: int, excluding: int | None = None) -> int:
    stmt = select(func.count()).select_from(ProjectMember).where(
        ProjectMember.project_id == pid, ProjectMember.role == "owner")
    if excluding is not None:
        stmt = stmt.where(ProjectMember.user_id != excluding)
    return db.execute(stmt).scalar_one()


@router.get("/projects/{pid}/members")
def list_members(access: Access = Depends(project_access()), db: Session = Depends(get_db)):
    return _members(db, access.project.id)


@router.put("/projects/{pid}/members")
def set_member(body: MemberIn, access: Access = Depends(project_access("owner")),
               db: Session = Depends(get_db)):
    if body.role not in ROLES:
        raise HTTPException(422, "Role must be viewer, editor or owner")
    user = db.execute(select(User).where(User.email == body.email.strip().lower())).scalar_one_or_none()
    if user is None:
        raise HTTPException(404, "No user has that email. An administrator must create the account first")
    pid = access.project.id
    member = db.get(ProjectMember, (pid, user.id))
    if member is None:
        db.add(ProjectMember(project_id=pid, user_id=user.id, role=body.role))
    else:
        if member.role == "owner" and body.role != "owner" and _owner_count(db, pid, excluding=user.id) == 0:
            raise HTTPException(400, "A project needs at least one owner")
        member.role = body.role
    db.commit()
    return _members(db, pid)


@router.delete("/projects/{pid}/members/{uid}")
def remove_member(uid: int, access: Access = Depends(project_access("owner")),
                  db: Session = Depends(get_db)):
    pid = access.project.id
    member = db.get(ProjectMember, (pid, uid))
    if member is None:
        raise HTTPException(404, "That user is not a member")
    if member.role == "owner" and _owner_count(db, pid, excluding=uid) == 0:
        raise HTTPException(400, "A project needs at least one owner")
    db.delete(member)
    db.commit()
    return _members(db, pid)


# -------------------------------------------------------------------- datasets
@router.post("/projects/{pid}/datasets")
def upload_datasets(request: Request, files: list[UploadFile] = File(...),
                    access: Access = Depends(project_access("editor")), db: Session = Depends(get_db)):
    """Upload one or more debug zips. Each file succeeds or fails on its own."""
    settings = request.app.state.settings
    store = request.app.state.store
    pid = access.project.id
    outcomes = []
    for up in files:
        filename = PurePath(up.filename or "upload.zip").name[:300]
        key = None
        try:
            digest, size = hashlib.sha256(), 0
            up.file.seek(0)
            while chunk := up.file.read(1 << 20):
                digest.update(chunk)
                size += len(chunk)
            if size > settings.max_upload_mb * 1024 * 1024:
                raise ParseError(f"The file is larger than the {settings.max_upload_mb} MB limit")
            sha = digest.hexdigest()
            dup = db.execute(select(Dataset).where(Dataset.project_id == pid, Dataset.sha256 == sha)).scalar_one_or_none()
            if dup is not None and (dup.profile or {}).get("has_runoff"):
                raise ParseError(f"This zip is already in the project as '{dup.name}'")
            up.file.seek(0)
            data = parse_zip(up.file)
            profile = data.profile()
            key = f"p{pid}/{uuid.uuid4().hex}.npz"
            store.put(key, data.to_bytes())
            if dup is not None:
                # the same zip, stored before runoff_triangle was read: replace the parsed data in
                # place so the zip keeps its id, name, overrides and results (now out of date)
                old_key = dup.blob_key
                dup.blob_key, dup.profile, dup.meta, dup.size_bytes = key, profile, data.meta, size
                dup.uploaded_by, dup.uploaded_at = access.user.id, utcnow()
                mark_stale(db, dataset_id=dup.id)
                db.commit()
                _drop_blobs(request, [old_key])
                outcomes.append({"filename": filename, "ok": True, "updated": True, "dataset": dataset_out(dup),
                                 "message": "Updated with vintage data"})
                continue
            base = f"VB{data.category}" if data.category else PurePath(filename).stem
            ds = Dataset(project_id=pid, name=_unique_name(db, pid, base), category=data.category,
                         filename=filename, sha256=sha, size_bytes=size, meta=data.meta,
                         profile=profile, blob_key=key, uploaded_by=access.user.id)
            db.add(ds)
            db.commit()
            outcomes.append({"filename": filename, "ok": True, "dataset": dataset_out(ds)})
        except (ParseError, StorageError) as exc:
            db.rollback()
            outcomes.append({"filename": filename, "ok": False, "error": str(exc)})
        except Exception:                   # a bad file must not stop the files after it
            db.rollback()
            log.exception("Unexpected error reading upload %s", filename)
            if key is not None:
                try:
                    store.delete(key)       # do not leave stored data without a row
                except StorageError:
                    pass
            outcomes.append({"filename": filename, "ok": False,
                             "error": "The zip could not be read. Check that it is an unmodified debug zip"})
        finally:
            up.file.close()
    return outcomes


class DatasetPatch(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None


def _dataset(db: Session, pid: int, did: int) -> Dataset:
    ds = db.get(Dataset, did)
    if ds is None or ds.project_id != pid:
        raise HTTPException(404, "Zip not found")
    return ds


@router.patch("/projects/{pid}/datasets/{did}")
def patch_dataset(did: int, body: DatasetPatch, access: Access = Depends(project_access("editor")),
                  db: Session = Depends(get_db)):
    ds = _dataset(db, access.project.id, did)
    if body.name is not None:
        name = _clean_name(body.name)
        clash = db.execute(select(Dataset).where(Dataset.project_id == ds.project_id, Dataset.name == name,
                                                 Dataset.id != ds.id)).scalar_one_or_none()
        if clash:
            raise HTTPException(409, "Another zip in this project already has that name")
        ds.name = name
    if body.category is not None and body.category.strip() != ds.category:
        if len(body.category.strip()) > 100:
            raise HTTPException(422, "The category is too long")
        ds.category = body.category.strip()       # only the applied-curve match follows the category
    db.commit()
    return dataset_out(ds)


@router.delete("/projects/{pid}/datasets/{did}")
def delete_dataset(did: int, request: Request, access: Access = Depends(project_access("editor")),
                   db: Session = Depends(get_db)):
    ds = _dataset(db, access.project.id, did)
    key = ds.blob_key
    db.delete(ds)
    db.commit()
    _drop_blobs(request, [key])
    return {"ok": True}


# ---------------------------------------------------------------------- curves
@router.get("/projects/{pid}/curves")
def list_curves(access: Access = Depends(project_access()), db: Session = Depends(get_db)):
    return curve_rows(db, access.project.id)


@router.get("/projects/{pid}/curves/{label}")
def get_curve(label: str, kind: str = "applied", access: Access = Depends(project_access()),
              db: Session = Depends(get_db)):
    """An applied curve's monthly values on the face basis (cash as a share of the balance at default)."""
    if kind != "applied":
        raise HTTPException(404, "Only the client's applied curves are kept; the reference-curve method was removed")
    row = applied_curves(db, access.project.id).get(label)
    if row is None:
        raise HTTPException(404, "Curve not found")
    return {"label": label, "kind": "applied", "basis": row.basis,
            "values": to_face(np.asarray(row.values, dtype=float), row.basis).tolist(),
            "values_as_uploaded": [float(x) for x in row.values]}


@router.post("/projects/{pid}/curves")
def upload_curves(file: UploadFile = File(...), kind: str = Form("applied"), basis: str = Form("face"),
                  access: Access = Depends(project_access("editor")), db: Session = Depends(get_db)):
    """Upload a CSV or xlsx: first column t = 1, 2, ..., then one column per curve label.

    kind: applied (the client's applied curve, comparison only). The reference-shape kind was
    retired with the reference-curve method and is refused.
    basis: face (share of the balance at default) or outstanding (share of the balance still
    outstanding each month).
    """
    if kind == "shape":
        raise HTTPException(422, "Reference curves are no longer used: method 3 is the log-normal shape fitted to "
                                 "the zip's own data. Upload the client's curves as applied curves for comparison")
    if kind not in CURVE_KINDS:
        raise HTTPException(422, "kind must be applied")
    if basis not in CURVE_BASES:
        raise HTTPException(422, "basis must be face or outstanding")
    raw = file.file.read(20 * 1024 * 1024 + 1)
    if len(raw) > 20 * 1024 * 1024:
        raise HTTPException(413, "Curve files are limited to 20 MB")
    filename = PurePath(file.filename or "curves.csv").name
    try:
        parsed = parse_curve_file(raw, filename)
    except CurveError as exc:
        raise HTTPException(422, str(exc)) from None
    pid = access.project.id
    if basis == "outstanding" and any((np.asarray(v) > 1).any() for v in parsed.values()):
        raise HTTPException(422, "On the outstanding basis every monthly rate must be at most 1 (100%)")
    if basis == "face":
        over = [label for label, v in parsed.items() if float(np.asarray(v).sum()) > 1.0 + 1e-9]
        if over:
            raise HTTPException(422, "On the face value basis the monthly rates must add up to at most 1 (100% of the "
                                     "balance at default); these curves add up to more: " + ", ".join(over) +
                                     ". Were they meant to be on the outstanding basis?")
    for label, values in parsed.items():
        row = db.execute(select(ClientCurve).where(ClientCurve.project_id == pid, ClientCurve.label == label,
                                                   ClientCurve.kind == kind)).scalar_one_or_none()
        if row is None:
            row = ClientCurve(project_id=pid, label=label, kind=kind, uploaded_by=access.user.id)
            db.add(row)
        row.values = [float(x) for x in values]
        row.basis = basis
        row.source_filename = filename
        row.uploaded_by = access.user.id
    db.commit()                               # applied curves never enter the calculation
    return curve_rows(db, pid)


@router.delete("/projects/{pid}/curves/{cid}")
def delete_curve(cid: int, access: Access = Depends(project_access("editor")), db: Session = Depends(get_db)):
    row = db.get(ClientCurve, cid)
    if row is None or row.project_id != access.project.id:
        raise HTTPException(404, "Curve not found")
    db.delete(row)
    db.commit()
    return curve_rows(db, access.project.id)
