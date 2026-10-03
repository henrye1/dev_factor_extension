// The scenario parameter form. Used in full for a scenario and, with a tick box per field,
// for a zip's override.

import { h } from "./ui.js";
import { helpButton } from "./help.js";

export const METHODS = { 1: "1 – Exponential", 2: "2 – Power law", 3: "3 – Reference curve shape" };

// kind: int | float | select | text ; blank: the field may be left empty
export const FIELDS = [
  { group: "Horizon", name: "target_ts", label: "Target TermStep", kind: "int",
    hint: "The LGD table runs from TermStep 1 to this." },
  { group: "Horizon", name: "max_bucket", label: "MaxBucket", kind: "int",
    hint: "Buckets are extended to this index. Usually Target + the valuation horizon." },
  { group: "Horizon", name: "horizon2", label: "Valuation horizon (months)", kind: "int",
    hint: "LGD is also shown within this many months of each TermStep." },
  { group: "Horizon", name: "horizon", label: "Short horizon (months)", kind: "int",
    hint: "The 12-month basis." },

  { group: "Tail", name: "method", label: "Method", kind: "select",
    options: () => Object.entries(METHODS).map(([v, l]) => [Number(v), l]),
    hint: "The shape used for the selected LGD. All three are always computed." },
  { group: "Tail", name: "client_cohort", label: "Reference curve", kind: "select", blank: true,
    options: (ctx) => [["", "The zip's own category"], ...ctx.curves.map((c) => [c, c])],
    hint: "The curve method 3 scales its tail to; also drawn on the comparison chart." },
  { group: "Tail", name: "min_exposure_mode", label: "MinExposure basis", kind: "select",
    options: () => [["abs", "Rand amount"], ["pct", "% of TermStep 1 opening exposure"]],
    hint: "How the credibility cut below is stated." },
  { group: "Tail", name: "min_exposure", label: "MinExposure (credibility cut)", kind: "float",
    hint: "Buckets with less exposure are replaced by the fitted tail." },
  { group: "Tail", name: "window", label: "Window W (buckets)", kind: "int",
    hint: "The last W credible buckets set the level of the tail." },
  { group: "Tail", name: "floor", label: "Hazard floor (per bucket)", kind: "float",
    hint: "Minimum RecoveryPct on the extended tail." },

  { group: "Decay fit", name: "ref_ts", label: "Reference TermStep for λ / γ", kind: "int",
    hint: "The row whose tail is fitted. 1 is the longest." },
  { group: "Decay fit", name: "fit_start", label: "FitStart bucket", kind: "int",
    hint: "Start of the regression window. Skips the early hump." },
  { group: "Decay fit", name: "lambda_override", label: "λ override", kind: "float", blank: true,
    hint: "Leave empty to use the fitted value." },
  { group: "Decay fit", name: "gamma_override", label: "γ override", kind: "float", blank: true,
    hint: "Leave empty to use the fitted value." },

  { group: "Derived LGD", name: "base_ts", label: "Base TermStep", kind: "int",
    hint: "The row rolled forward for TermSteps beyond LastTS." },
  { group: "Derived LGD", name: "last_ts", label: "LastTS (last own row used)", kind: "int", blank: true,
    hint: "Leave empty for the last observed TermStep. Lower it to derive thin late rows." },

  { group: "Source", name: "event_type", label: "EventType", kind: "select",
    options: (ctx) => ctx.eventTypes.map((e) => [e, e]),
    hint: "Which block of lgd_recovery is used." },
  { group: "Source", name: "rate", label: "Discount rate p.a.", kind: "float", blank: true,
    hint: "As a fraction, for example 0.1771. Leave empty to use the rate implied by each file." },
];

export function describe(name, value) {
  const f = FIELDS.find((x) => x.name === name);
  const label = f ? f.label : name;
  let text = value === null || value === "" ? "empty" : String(value);
  if (typeof value === "number" && Math.abs(value) >= 1000) text = value.toLocaleString("en-US");
  if (name === "method") text = METHODS[value] || text;
  if (name === "min_exposure_mode") text = value === "pct" ? "% of opening" : "Rand";
  return `${label}: ${text}`;
}

function control(f, value, ctx) {
  if (f.kind === "select") {
    const sel = h("select");
    const opts = f.options(ctx);
    const current = value === null || value === undefined ? "" : value;
    if (!opts.some(([v]) => String(v) === String(current))) opts.push([current, String(current)]);
    for (const [v, l] of opts) sel.append(h("option", { value: String(v), selected: String(v) === String(current) }, l));
    return sel;
  }
  return h("input", {
    type: "number", step: f.kind === "int" ? "1" : "any",
    value: value === null || value === undefined ? "" : String(value),
    placeholder: f.blank ? "empty" : "",
  });
}

function read(f, el) {
  const raw = el.value.trim();
  if (raw === "") {
    if (f.blank) return null;
    throw new Error(`${f.label} cannot be empty`);
  }
  if (f.kind === "int") {
    const n = Number(raw);
    if (!Number.isInteger(n)) throw new Error(`${f.label} must be a whole number`);
    return n;
  }
  if (f.kind === "float") {
    const n = Number(raw);
    if (!Number.isFinite(n)) throw new Error(`${f.label} must be a number`);
    return n;
  }
  if (f.name === "method") return Number(raw);
  return raw;
}

// Full form: every field. `values()` returns the complete parameter object.
// Override form: each field has a tick box; `values()` returns only the ticked ones.
export function paramForm(base, ctx, { override = null, readOnly = false } = {}) {
  const root = h("div");
  const controls = new Map();
  const ticks = new Map();
  let group = null, grid = null;
  for (const f of FIELDS) {
    if (f.group !== group) {
      group = f.group;
      grid = h("div", { class: "params" });
      root.append(h("h3", { style: null }, group), grid, h("div", { class: "small" }, " "));
    }
    const isOverridden = override !== null && Object.prototype.hasOwnProperty.call(override, f.name);
    const value = isOverridden ? override[f.name] : base[f.name];
    const el = control(f, value, ctx);
    el.disabled = readOnly || (override !== null && !isOverridden);
    controls.set(f.name, el);
    const id = "p_" + f.name + (override !== null ? "_o" : "");
    el.id = id;
    let labelNode;
    if (override !== null) {
      const tick = h("input", { type: "checkbox", checked: isOverridden, disabled: readOnly, "aria-label": `Override ${f.label}` });
      tick.addEventListener("change", () => {
        el.disabled = !tick.checked;
        if (!tick.checked) {
          const b = base[f.name];
          el.value = b === null || b === undefined ? "" : String(b);
        }
      });
      ticks.set(f.name, tick);
      labelNode = h("label", { for: id }, tick, " ", f.label);
    } else {
      labelNode = h("label", { for: id }, f.label);
    }
    grid.append(h("div", { class: "field" }, h("div", { class: "lbl" }, labelNode, helpButton(f.name)), el,
                         h("div", { class: "hint" }, f.hint)));
  }
  return {
    node: root,
    values() {
      const out = {};
      for (const f of FIELDS) {
        if (override !== null && !ticks.get(f.name).checked) continue;
        out[f.name] = read(f, controls.get(f.name));
      }
      return out;
    },
  };
}
