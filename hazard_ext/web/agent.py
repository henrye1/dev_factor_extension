"""The assistant: turns an instruction such as "set MaxBucket to 60 on the Exponential scenario"
into a concrete, confirmable change.

Claude is given the project's state and a few tools. Read tools run here. The only writing tool,
``propose_change``, does not change anything: it records a proposal that the browser shows with
the old and new values, and the user confirms it through the ordinary scenario and override
endpoints, with the same role checks as any other edit.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from ..engine.params import FIELDS, Params, merge_params

log = logging.getLogger("hazard_ext")

MAX_TOOL_ROUNDS = 8

GLOSSARY = """Parameters of a scenario (the JSON key, then what people call it):
- target_ts: "Target TermStep", "target", "LGD table length". The last TermStep in the LGD table.
- max_bucket: "MaxBucket", "max bucket", "max step", "maximum bucket", "how far the tail runs". The last bucket every row is extended to.
- horizon2: "valuation horizon" (months). horizon: "short horizon", "12-month horizon".
- method: 1 = exponential, 2 = power law, 3 = log-normal ("lognormal", "the client's curve form"). The old
  reference-curve shape no longer exists; method 3 now means log-normal.
- min_exposure_mode: "abs" (Rand) or "pct" (% of TermStep 1 opening exposure). min_exposure: "MinExposure", "credibility cut".
- window: "window", "W", "anchor window" (buckets). fit_start: "FitStart". ref_ts: "reference TermStep for lambda/gamma".
- lambda_override / gamma_override: numbers or null for fitted. mu_override / sigma_override: the log-normal μ and σ
  (on ln b), numbers or null for fitted. floor: "hazard floor".
- base_ts: "base TermStep", "base row". last_ts: "LastTS", "last own row", null = last observed TermStep.
- event_type: "EventType". rate: "discount rate" as a fraction (0.1771 = 17.71%), null = implied by the file.
- vintage_years: "last N years of vintages", "vintages from the last 10 years" (an integer); vintage_start: "vintages
  from 2016-08", a month as "YYYY-MM". Set one or the other, never both; null = all vintages. A zip needs vintage
  data (vintage_filter_available in get_state); otherwise say the zip must be uploaded again.
Zips are also called cohorts or categories; a zip's category is its label (11, 15, 22, 23, 25, 44, ALL).
"""

SYSTEM = """You are the assistant inside LGD Tail Extension, an app that extends truncated recovery
triangles and reports LGD by TermStep for several zips (cohorts) under named scenarios. You help the
signed-in analyst read results and change assumptions.

Rules:
- To change any assumption you MUST call propose_change. Never claim a change was made; a proposal is
  applied only after the user confirms it in the app. Say "proposed" rather than "done".
- A change for one zip is a per-zip override (scope "zip"); a change for every zip is a scenario change
  (scope "scenario"). If the user names a zip, use scope "zip". If they say "all zips" or name no zip
  while looking at the project page, use scope "scenario". When it is genuinely unclear which scenario
  or which parameter they mean, ask one short question instead of guessing.
- Use the exact scenario and zip names from the project state. Parameter values must respect the bounds
  in the glossary and the app (TermSteps and buckets 1 to 2000; MinExposure in Rand or a percentage).
- Keep replies short and concrete: name the scenario, zip, parameter, old value and new value. Use plain
  numbers. Do not use markdown headings.
- Read tools (get_state, get_result) are free to use; call them before answering questions about figures.

""" + GLOSSARY

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_state",
        "description": "The project's zips, scenarios with their parameters and per-zip overrides, and the headline LGD per zip and scenario.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False, "required": []},
        "strict": True,
    },
    {
        "name": "get_result",
        "description": "Headline figures and selected rows of the LGD table for one zip under one scenario.",
        "input_schema": {
            "type": "object",
            "properties": {
                "scenario": {"type": "string", "description": "Exact scenario name"},
                "zip": {"type": "string", "description": "Exact zip name, e.g. VB44"},
                "termsteps": {"type": "array", "items": {"type": "integer"},
                              "description": "TermSteps to report, e.g. [1, 12, 60, 120]. Empty for the headline only."},
            },
            "additionalProperties": False, "required": ["scenario", "zip", "termsteps"],
        },
        "strict": True,
    },
    {
        "name": "propose_change",
        "description": "Propose a change of assumptions for the user to confirm. Does not apply anything.",
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "enum": ["scenario", "zip"],
                          "description": "scenario = applies to every zip; zip = override for one zip"},
                "scenario": {"type": "string", "description": "Exact scenario name"},
                "zip": {"type": "string", "description": "Exact zip name when scope is zip, otherwise empty string"},
                "changes": {"type": "object",
                            "description": "Parameter JSON keys to new values, e.g. {\"max_bucket\": 60}. Use null to clear an optional value.",
                            "additionalProperties": True},
                "run_after": {"type": "boolean", "description": "Rerun after applying. Usually true."},
                "reason": {"type": "string", "description": "One sentence on why, in the user's words"},
            },
            "additionalProperties": False, "required": ["scope", "scenario", "zip", "changes", "run_after", "reason"],
        },
        "strict": True,
    },
]


class AgentContext:
    """What the agent may read, and how a proposal is checked. Built per request from the database."""

    def __init__(self, state: dict, result_reader):
        self.state = state                      # from routers.agent._project_state
        self.result_reader = result_reader      # (scenario_name, zip_name, termsteps) -> dict
        self.proposals: list[dict] = []

    def _scenario(self, name: str) -> dict | None:
        return next((s for s in self.state["scenarios"] if s["name"] == name), None)

    def _zip(self, name: str) -> dict | None:
        return next((d for d in self.state["zips"] if d["name"] == name), None)

    def run_tool(self, name: str, args: dict) -> dict:
        if name == "get_state":
            return self.state
        if name == "get_result":
            return self.result_reader(args["scenario"], args["zip"], args.get("termsteps") or [])
        if name == "propose_change":
            return self.propose(args)
        return {"error": f"Unknown tool {name}"}

    def propose(self, args: dict) -> dict:
        s = self._scenario(args["scenario"])
        if s is None:
            return {"error": f"No scenario named {args['scenario']!r}. Scenarios: " + ", ".join(x["name"] for x in self.state["scenarios"])}
        z = None
        if args["scope"] == "zip":
            z = self._zip(args.get("zip") or "")
            if z is None:
                return {"error": f"No zip named {args.get('zip')!r}. Zips: " + ", ".join(x["name"] for x in self.state["zips"])}
        changes = dict(args.get("changes") or {})
        unknown = [k for k in changes if k not in Params.model_fields]
        if unknown:
            return {"error": "Unknown parameter(s): " + ", ".join(unknown) + ". Use the JSON keys from the glossary."}
        if not changes:
            return {"error": "No changes given"}
        current_override = (s["overrides"].get(z["name"]) if z else None) or {}
        try:
            if z is not None:
                merged = merge_params(s["params"], {**current_override, **changes})
            else:
                merged = merge_params({**s["params"], **changes})
                for zn, ov in s["overrides"].items():          # existing overrides must still be valid
                    merge_params({**s["params"], **changes}, ov)
        except (ValidationError, ValueError) as exc:
            return {"error": "Invalid value: " + str(exc).splitlines()[0]}
        before = {**s["params"], **current_override} if z else s["params"]
        diff = []
        for k, v in changes.items():
            old = before.get(k)
            new = getattr(merged, k)
            if old != new:
                diff.append({"param": k, "old": old, "new": new})
        if not diff:
            return {"note": "Those values are already in place; nothing to change."}
        proposal = {
            "scope": args["scope"], "scenario": s["name"], "scenario_id": s["id"],
            "zip": z["name"] if z else None, "zip_id": z["id"] if z else None,
            "changes": {d["param"]: d["new"] for d in diff}, "diff": diff,
            "run_after": bool(args.get("run_after", True)), "reason": args.get("reason", ""),
        }
        self.proposals.append(proposal)
        return {"proposal_recorded": True, "diff": diff,
                "note": "The user must confirm this in the app before it is applied."}


def run_agent(client, model: str, history: list[dict], ctx: AgentContext, view: str = "") -> dict:
    """One assistant turn. ``history`` is prior user/assistant text turns plus the new user message.
    Returns {"reply": text, "proposals": [...], "usage": {...}}."""
    system = SYSTEM + ("\nThe user is currently looking at: " + view if view else "")
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    usage = {"input_tokens": 0, "output_tokens": 0}
    reply_parts: list[str] = []
    for _ in range(MAX_TOOL_ROUNDS):
        response = client.beta.messages.create(
            model=model, max_tokens=4000, system=system, messages=messages, tools=TOOLS,
            tool_choice={"type": "auto"}, output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
        usage["input_tokens"] += getattr(response.usage, "input_tokens", 0) or 0
        usage["output_tokens"] += getattr(response.usage, "output_tokens", 0) or 0
        if response.stop_reason == "refusal":
            reply_parts.append("I can't help with that request.")
            break
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        for b in response.content:
            if b.type == "text" and b.text.strip():
                reply_parts.append(b.text.strip())
        if not tool_uses:
            break
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for tu in tool_uses:
            args = tu.input if isinstance(tu.input, dict) else json.loads(tu.input or "{}")
            try:
                out = ctx.run_tool(tu.name, args)
            except Exception as exc:                 # a tool failure is reported to the model, not raised
                log.exception("agent tool %s failed", tu.name)
                out = {"error": str(exc)}
            results.append({"type": "tool_result", "tool_use_id": tu.id, "content": json.dumps(out, default=str),
                            "is_error": "error" in out})
        messages.append({"role": "user", "content": results})
        if response.stop_reason == "max_tokens":
            break
    reply = "\n\n".join(reply_parts).strip() or "I have nothing to add."
    return {"reply": reply, "proposals": ctx.proposals, "usage": usage}


def param_label(name: str) -> str:
    return name
