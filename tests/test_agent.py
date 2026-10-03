"""The assistant, with the model stubbed: tool calls are scripted, so the test checks the glue
(state given to the model, validation of proposals, roles, logging), not Claude itself."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from hazard_ext.web.agent import TOOLS, AgentContext, run_agent
from hazard_ext.web.config import Settings
from hazard_ext.web.main import create_app

from .conftest import zip_path

H = {"X-Requested-With": "hazard-ext"}


def _text(t):
    return SimpleNamespace(type="text", text=t)


def _tool(name, args, id_="tu1"):
    return SimpleNamespace(type="tool_use", name=name, input=args, id=id_)


class FakeClient:
    """Plays a script: each call returns the next response. Records what it was sent."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append({**kw, "messages": list(kw["messages"])})     # snapshot: the agent mutates its list
        content, stop = self.script.pop(0)
        return SimpleNamespace(content=content, stop_reason=stop,
                               usage=SimpleNamespace(input_tokens=100, output_tokens=20))


# ------------------------------------------------------------- unit: context
STATE = {
    "zips": [{"name": "VB44", "id": 9, "category": "44"}, {"name": "VBALL", "id": 3, "category": "ALL"}],
    "scenarios": [{"name": "Exponential", "id": 4, "params": {"method": 1, "max_bucket": 480, "target_ts": 360},
                   "overrides": {"VBALL": {"method": 1}}}],
    "headline": [],
}


def test_proposal_is_validated_and_diffed():
    ctx = AgentContext(STATE, lambda *a: {})
    out = ctx.propose({"scope": "scenario", "scenario": "Exponential", "zip": "", "changes": {"max_bucket": 60, "method": 1},
                       "run_after": True, "reason": "test"})
    assert out["proposal_recorded"] and out["diff"] == [{"param": "max_bucket", "old": 480, "new": 60}]   # unchanged method dropped
    assert ctx.proposals[0]["scenario_id"] == 4 and ctx.proposals[0]["changes"] == {"max_bucket": 60}
    assert "error" in ctx.propose({"scope": "scenario", "scenario": "Nope", "zip": "", "changes": {"max_bucket": 60}, "run_after": True, "reason": ""})
    assert "Unknown parameter" in ctx.propose({"scope": "scenario", "scenario": "Exponential", "zip": "", "changes": {"maxstep": 60}, "run_after": True, "reason": ""})["error"]
    assert "Invalid value" in ctx.propose({"scope": "scenario", "scenario": "Exponential", "zip": "", "changes": {"max_bucket": 10 ** 9}, "run_after": True, "reason": ""})["error"]
    assert "No zip" in ctx.propose({"scope": "zip", "scenario": "Exponential", "zip": "VB99", "changes": {"last_ts": 77}, "run_after": True, "reason": ""})["error"]
    z = ctx.propose({"scope": "zip", "scenario": "Exponential", "zip": "VB44", "changes": {"last_ts": 77}, "run_after": True, "reason": ""})
    assert z["diff"] == [{"param": "last_ts", "old": None, "new": 77}]
    assert "already in place" in ctx.propose({"scope": "zip", "scenario": "Exponential", "zip": "VBALL", "changes": {"method": 1}, "run_after": True, "reason": ""})["note"]


def test_run_agent_loops_over_tools_until_text():
    client = FakeClient([
        ([_tool("get_state", {})], "tool_use"),
        ([_text("I'll propose that."), _tool("propose_change", {"scope": "scenario", "scenario": "Exponential", "zip": "",
                                                                "changes": {"max_bucket": 60}, "run_after": True, "reason": "asked"}, "tu2")], "tool_use"),
        ([_text("Proposed: MaxBucket 480 to 60 on Exponential, all zips. Confirm to apply and rerun.")], "end_turn"),
    ])
    ctx = AgentContext(STATE, lambda *a: {})
    out = run_agent(client, "claude-opus-5-5", [{"role": "user", "content": "set the max step to 60 on Exponential"}], ctx, "project page")
    assert out["proposals"][0]["changes"] == {"max_bucket": 60}
    assert "Proposed" in out["reply"] and out["usage"]["input_tokens"] == 300
    first = client.calls[0]
    assert first["model"] == "claude-opus-5-5" and first["tools"] is TOOLS and "project page" in first["system"]
    assert first["fallbacks"] == "default"
    # the tool result of get_state carried the project state back to the model
    second = client.calls[1]["messages"]
    assert second[-1]["role"] == "user" and json.loads(second[-1]["content"][0]["content"])["scenarios"][0]["name"] == "Exponential"


def test_tools_are_strict_with_closed_schemas():
    for t in TOOLS:
        assert t["strict"] is True and t["input_schema"]["additionalProperties"] is False
        assert set(t["input_schema"]["required"]) == set(t["input_schema"]["properties"].keys())


# ---------------------------------------------------------------- API flow
@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("agent")
    settings = Settings(_env_file=None, database_url=f"sqlite:///{(tmp / 'a.db').as_posix()}",
                        local_data_dir=str(tmp / "blobs"), admin_email="a@example.com", admin_password="long-password-1")
    app = create_app(settings)
    with TestClient(app) as c:
        assert c.post("/api/auth/login", json={"email": "a@example.com", "password": "long-password-1"}, headers=H).status_code == 200
        pid = c.post("/api/projects", json={"name": "P"}, headers=H).json()["id"]
        out = c.post(f"/api/projects/{pid}/datasets", files=[("files", ("debug (44).zip", zip_path("44").read_bytes(), "application/zip"))], headers=H).json()
        did = out[0]["dataset"]["id"]
        sid = c.post(f"/api/projects/{pid}/scenarios", json={"name": "Exponential", "params": {"method": 1}}, headers=H).json()["id"]
        c.post(f"/api/projects/{pid}/scenarios/{sid}/run", json={}, headers=H)
        yield SimpleNamespace(app=app, c=c, pid=pid, did=did, sid=sid)


def test_assistant_is_off_without_a_key(world):
    assert world.c.get("/api/features").json() == {"assistant": False}
    r = world.c.post(f"/api/projects/{world.pid}/agent", json={"history": [{"role": "user", "content": "hi"}]}, headers=H)
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]


def test_proposal_round_trip_through_the_api(world):
    pid, sid, did = world.pid, world.sid, world.did
    world.app.state.agent_client = FakeClient([
        ([_tool("get_result", {"scenario": "Exponential", "zip": "VB44", "termsteps": [1, 60]})], "tool_use"),
        ([_tool("propose_change", {"scope": "scenario", "scenario": "Exponential", "zip": "", "changes": {"max_bucket": 60},
                                   "run_after": True, "reason": "user asked"}, "tu2")], "tool_use"),
        ([_text("Proposed: MaxBucket 420 to 60 on Exponential for all zips.")], "end_turn"),
    ])
    r = world.c.post(f"/api/projects/{pid}/agent", json={"history": [{"role": "user", "content": "set max step to 60"}], "view": "project page"}, headers=H)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["can_apply"] is True and out["proposals"][0]["diff"] == [{"param": "max_bucket", "old": 420, "new": 60}]
    # the get_result tool read the real stored result
    sent = world.app.state.agent_client.calls[1]["messages"][-1]["content"][0]["content"]
    got = json.loads(sent)
    assert got["rows"][0]["termstep"] == 1 and 0 < got["averages"]["lgd_selected"] < 1
    # confirm = the ordinary endpoints, then mark the log entry applied
    p = out["proposals"][0]
    s = world.c.get(f"/api/projects/{pid}/scenarios/{sid}").json()
    assert world.c.put(f"/api/projects/{pid}/scenarios/{sid}", json={"params": {**s["params"], **p["changes"]}}, headers=H).status_code == 200
    assert world.c.post(f"/api/projects/{pid}/agent/{out['log_id']}/applied", headers=H).status_code == 200
    log = world.c.get(f"/api/projects/{pid}/agent/log").json()
    assert log[0]["applied"] is True and log[0]["instruction"] == "set max step to 60"
    assert world.c.get(f"/api/projects/{pid}/scenarios/{sid}").json()["params"]["max_bucket"] == 60


def test_viewer_can_ask_but_not_change(world):
    pid = world.pid
    world.c.post("/api/admin/users", json={"email": "v@example.com", "password": "viewer-password-1"}, headers=H)
    world.c.put(f"/api/projects/{pid}/members", json={"email": "v@example.com", "role": "viewer"}, headers=H)
    v = TestClient(world.app)
    assert v.post("/api/auth/login", json={"email": "v@example.com", "password": "viewer-password-1"}, headers=H).status_code == 200
    world.app.state.agent_client = FakeClient([
        ([_tool("propose_change", {"scope": "zip", "scenario": "Exponential", "zip": "VB44", "changes": {"last_ts": 77},
                                   "run_after": True, "reason": ""})], "tool_use"),
        ([_text("Proposed LastTS 77 for VB44.")], "end_turn"),
    ])
    out = v.post(f"/api/projects/{pid}/agent", json={"history": [{"role": "user", "content": "last ts 77 for vb44"}]}, headers=H).json()
    assert out["proposals"] == [] and out["can_apply"] is False and "viewer access" in out["reply"]


def test_model_errors_are_reported_not_500(world):
    class Broken:
        beta = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))))
    world.app.state.agent_client = Broken()
    r = world.c.post(f"/api/projects/{world.pid}/agent", json={"history": [{"role": "user", "content": "x"}]}, headers=H)
    assert r.status_code == 502 and "boom" in r.json()["detail"]
