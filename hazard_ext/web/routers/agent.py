"""The assistant endpoint: natural-language instructions turned into confirmable proposals."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agent import AgentContext, run_agent
from ..deps import Access, csrf_guard, get_db, project_access
from ..models import AgentLog, Dataset, Result, Scenario, iso, utcnow

router = APIRouter(prefix="/api", dependencies=[Depends(csrf_guard)])


class Turn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=8000)


class AgentIn(BaseModel):
    history: list[Turn] = Field(max_length=40)
    view: str = Field("", max_length=300)


def _project_state(db: Session, pid: int) -> dict:
    datasets = list(db.execute(select(Dataset).where(Dataset.project_id == pid).order_by(Dataset.name)).scalars())
    scenarios = list(db.execute(select(Scenario).where(Scenario.project_id == pid).order_by(Scenario.name)).scalars())
    by_id = {d.id: d for d in datasets}
    results = {(r.scenario_id, r.dataset_id): r for r in db.execute(
        select(Result).join(Scenario, Scenario.id == Result.scenario_id).where(Scenario.project_id == pid)).scalars()}
    return {
        "zips": [{"name": d.name, "id": d.id, "category": d.category, "observed_termsteps": d.profile.get("max_ts"),
                  "rate_in_file": d.profile.get("implied_rate"), "vintage_filter_available": bool(d.profile.get("vintage_filter")),
                  "first_vintage": d.profile.get("cohort_first"), "last_vintage": d.profile.get("cohort_last")} for d in datasets],
        "scenarios": [{"name": s.name, "id": s.id, "description": s.description, "params": s.params,
                       "overrides": {by_id[o.dataset_id].name: o.params for o in s.overrides if o.dataset_id in by_id}}
                      for s in scenarios],
        "headline": [{"scenario": s.name, "zip": d.name, "status": r.status if r else "not run",
                      "out_of_date": bool(r and r.stale),
                      "lgd_selected": (r.summary or {}).get("lgd_selected") if r and r.status == "ok" else None,
                      "lgd_file": (r.summary or {}).get("lgd_file") if r and r.status == "ok" else None,
                      "method": (r.summary or {}).get("method_label") if r and r.status == "ok" else None}
                     for s in scenarios for d in datasets for r in [results.get((s.id, d.id))]],
    }


def _result_reader(db: Session, pid: int):
    def read(scenario: str, zip_name: str, termsteps: list[int]) -> dict:
        s = db.execute(select(Scenario).where(Scenario.project_id == pid, Scenario.name == scenario)).scalar_one_or_none()
        d = db.execute(select(Dataset).where(Dataset.project_id == pid, Dataset.name == zip_name)).scalar_one_or_none()
        if s is None or d is None:
            return {"error": "Unknown scenario or zip name; use the exact names from get_state"}
        r = db.get(Result, (s.id, d.id))
        if r is None or r.status != "ok":
            return {"error": "This scenario has not been run for this zip" if r is None else "The run failed: " + r.error}
        pay = r.payload
        L = pay["lgd_ts"]
        rows = []
        for t in termsteps[:40]:
            if 1 <= t <= len(L["ts"]):
                i = t - 1
                rows.append({"termstep": t, "lgd_final": L["lgd_final"][i], "lgd_file": L["lgd_file"][i],
                             "source": L["source"][i], "lgd_within_valuation_horizon": L["lgd_valuation_horizon"][i]})
        return {"scenario": scenario, "zip": zip_name, "out_of_date": r.stale, "averages": pay["averages"],
                "config": {k: pay["config"].get(k) for k in ("rate", "lam", "gam", "mu", "sigma", "half_life", "last_ts",
                                                           "target_ts", "max_bucket", "method_label", "vintage_filter",
                                                           "vintage_start_effective", "cohorts_included", "cohorts_total")},
                "warnings": pay["warnings"], "rows": rows, "effective_params": r.effective_params}
    return read


@router.post("/projects/{pid}/agent")
def ask_agent(body: AgentIn, request: Request, access: Access = Depends(project_access()),
              db: Session = Depends(get_db)):
    """One assistant turn. Returns the reply and any proposals, which the user confirms through the
    normal scenario and override endpoints."""
    client = request.app.state.agent_client
    if client is None:
        raise HTTPException(503, "The assistant is not configured: set ANTHROPIC_API_KEY on the server")
    if not body.history or body.history[-1].role != "user":
        raise HTTPException(422, "The last turn must be the user's message")
    pid = access.project.id
    ctx = AgentContext(_project_state(db, pid), _result_reader(db, pid))
    try:
        out = run_agent(client, request.app.state.settings.agent_model,
                        [t.model_dump() for t in body.history], ctx, body.view)
    except Exception as exc:                       # the SDK raises typed errors; report, do not 500
        raise HTTPException(502, f"The assistant could not be reached: {type(exc).__name__}: {str(exc)[:200]}") from None
    if access.role == "viewer":
        out["proposals"] = []                      # a viewer may ask, not change
        if ctx.proposals:
            out["reply"] += "\n\n(You have viewer access on this project, so the change cannot be applied.)"
    log = AgentLog(project_id=pid, user_id=access.user.id, instruction=body.history[-1].content[:2000],
                   reply=out["reply"][:4000], proposals=out["proposals"], applied=False)
    db.add(log)
    db.commit()
    return {**out, "log_id": log.id, "can_apply": access.role != "viewer"}


@router.post("/projects/{pid}/agent/{log_id}/applied")
def mark_applied(log_id: int, access: Access = Depends(project_access("editor")), db: Session = Depends(get_db)):
    log = db.get(AgentLog, log_id)
    if log is None or log.project_id != access.project.id:
        raise HTTPException(404, "Not found")
    log.applied = True
    log.applied_at = utcnow()
    db.commit()
    return {"ok": True}


@router.get("/projects/{pid}/agent/log")
def agent_log(access: Access = Depends(project_access()), db: Session = Depends(get_db), limit: int = 50):
    rows = db.execute(select(AgentLog).where(AgentLog.project_id == access.project.id)
                      .order_by(AgentLog.id.desc()).limit(min(limit, 200))).scalars()
    return [{"id": r.id, "user_id": r.user_id, "instruction": r.instruction, "reply": r.reply,
             "proposals": r.proposals, "applied": r.applied, "created_at": iso(r.created_at),
             "applied_at": iso(r.applied_at)} for r in rows]
