// The scenario parameter form. Used in full for a scenario and, with a tick box per field,
// for a zip's override.

import { h } from "./ui.js";
import { helpButton } from "./help.js";

export const METHODS = { 1: "1 – Exponential", 2: "2 – Power law", 3: "3 – Log-normal" };

// kind: int | float | select | text | vintage ; blank: the field may be left empty
// A "vintage" field is one control that writes two parameters (vintage_years, vintage_start).
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
  { group: "Tail", name: "min_exposure_mode", label: "MinExposure basis", kind: "select",
    options: () => [["abs", "Rand amount"], ["pct", "% of TermStep 1 opening exposure"]],
    hint: "How the credibility cut below is stated." },
  { group: "Tail", name: "min_exposure", label: "MinExposure (credibility cut)", kind: "float",
    hint: "Buckets with less exposure are replaced by the fitted tail." },
  { group: "Tail", name: "window", label: "Window W (buckets)", kind: "int",
    hint: "The last W credible buckets set the level of the tail." },
  { group: "Tail", name: "floor", label: "Hazard floor (per bucket)", kind: "float",
    hint: "Minimum RecoveryPct on the extended tail." },

  { group: "Decay fit", name: "ref_ts", label: "Reference TermStep for the fit", kind: "int",
    hint: "The row whose tail is fitted. 1 is the longest." },
  { group: "Decay fit", name: "fit_start", label: "FitStart bucket", kind: "int",
    hint: "Start of the regression window. Skips the early hump." },
  { group: "Decay fit", name: "lambda_override", label: "λ override", kind: "float", blank: true,
    hint: "Leave empty to use the fitted value." },
  { group: "Decay fit", name: "gamma_override", label: "γ override", kind: "float", blank: true,
    hint: "Leave empty to use the fitted value." },
  { group: "Decay fit", name: "mu_override", label: "Log-normal μ override", kind: "float", blank: true,
    hint: "Location on ln b. Leave empty to use the fitted value." },
  { group: "Decay fit", name: "sigma_override", label: "Log-normal σ override", kind: "float", blank: true,
    hint: "Spread on ln b, above 0. Leave empty to use the fitted value." },

  { group: "Derived LGD", name: "base_ts", label: "Base TermStep", kind: "int",
    hint: "The row rolled forward for TermSteps beyond LastTS." },
  { group: "Derived LGD", name: "last_ts", label: "LastTS (last own row used)", kind: "int", blank: true,
    hint: "Leave empty for the last observed TermStep. Lower it to derive thin late rows." },

  { group: "Data", name: "vintages", label: "Vintages", kind: "vintage", blank: true,
    params: ["vintage_years", "vintage_start"],
    hint: "Which default vintages feed the triangle: all of them, the last N years of each zip's own vintages, or vintages from a month. Needs the zip's runoff data." },
  { group: "Data", name: "event_type", label: "EventType", kind: "select",
    options: (ctx) => ctx.eventTypes.map((e) => [e, e]),
    hint: "Which block of lgd_recovery is used." },
  { group: "Data", name: "rate", label: "Discount rate p.a.", kind: "float", blank: true,
    hint: "As a fraction, for example 0.1771. Leave empty to use the rate implied by each file." },
];

const LABELS = { vintage_years: "Vintages, last N years", vintage_start: "Vintages from" };

// "Vintages: last 10 years" / "Vintages: from 2016-08" / "Vintages: all"
export function vintageText(params) {
  if (params.vintage_years !== null && params.vintage_years !== undefined) return `last ${params.vintage_years} years`;
  if (params.vintage_start) return `from ${params.vintage_start}`;
  return "all";
}

export function describe(name, value) {
  const f = FIELDS.find((x) => x.name === name);
  const label = f ? f.label : LABELS[name] || name;
  let text = value === null || value === "" ? "empty" : String(value);
  if (typeof value === "number" && Math.abs(value) >= 1000) text = value.toLocaleString("en-US");
  if (name === "method") text = METHODS[value] || text;
  if (name === "min_exposure_mode") text = value === "pct" ? "% of opening" : "Rand";
  return `${label}: ${text}`;
}

// The text for one form field given a full parameter object (handles the compound vintage field).
export function fieldText(f, params) {
  if (f.kind === "vintage") return `${f.label}: ${vintageText(params)}`;
  return describe(f.name, params[f.name]);
}

// Whether a parameter object (an override) sets a field.
export function fieldSet(f, params) {
  const keys = f.params || [f.name];
  return keys.some((k) => Object.prototype.hasOwnProperty.call(params, k));
}

function setDisabled(el, flag) {
  el.disabled = flag;
  for (const c of el.querySelectorAll ? el.querySelectorAll("select,input") : []) c.disabled = flag;
}

function vintageControl(value) {
  const mode = h("select", null,
    h("option", { value: "all" }, "All vintages"),
    h("option", { value: "years" }, "Last N years of each zip's vintages"),
    h("option", { value: "date" }, "From a month"));
  const years = h("input", { type: "number", step: "1", min: "1", max: "50", placeholder: "years", "aria-label": "Number of years" });
  const start = h("input", { type: "month", "aria-label": "First vintage month" });
  const show = () => {
    years.hidden = mode.value !== "years";
    start.hidden = mode.value !== "date";
  };
  mode.addEventListener("change", show);
  const wrap = h("div", { class: "vintage" }, mode, years, start);
  wrap.set = (p) => {
    if (p.vintage_years !== null && p.vintage_years !== undefined) { mode.value = "years"; years.value = String(p.vintage_years); start.value = ""; }
    else if (p.vintage_start) { mode.value = "date"; start.value = p.vintage_start; years.value = ""; }
    else { mode.value = "all"; years.value = ""; start.value = ""; }
    show();
  };
  wrap.read = () => {
    if (mode.value === "years") {
      const n = Number(years.value);
      if (!Number.isInteger(n) || n < 1 || n > 50) throw new Error("Vintages: the number of years must be a whole number from 1 to 50");
      return { vintage_years: n, vintage_start: null };
    }
    if (mode.value === "date") {
      if (!/^\d{4}-\d{2}$/.test(start.value)) throw new Error("Vintages: choose the first month to include");
      return { vintage_years: null, vintage_start: start.value };
    }
    return { vintage_years: null, vintage_start: null };
  };
  wrap.set(value || {});
  return wrap;
}

function control(f, value, ctx) {
  if (f.kind === "vintage") return vintageControl(value);
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
  if (f.kind === "vintage") return el.read();
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
      root.append(h("h3", { style: null }, group), grid, h("div", { class: "small" }, " "));
    }
    const isOverridden = override !== null && fieldSet(f, override);
    const value = f.kind === "vintage" ? (isOverridden ? override : base) : (isOverridden ? override[f.name] : base[f.name]);
    const el = control(f, value, ctx);
    setDisabled(el, readOnly || (override !== null && !isOverridden));
    controls.set(f.name, el);
    const id = "p_" + f.name + (override !== null ? "_o" : "");
    if (f.kind === "vintage") el.firstChild.id = id; else el.id = id;
    let labelNode;
    if (override !== null) {
      const tick = h("input", { type: "checkbox", checked: isOverridden, disabled: readOnly, "aria-label": `Override ${f.label}` });
      tick.addEventListener("change", () => {
        setDisabled(el, !tick.checked);
        if (!tick.checked) {
          if (f.kind === "vintage") el.set(base);
          else {
            const b = base[f.name];
            el.value = b === null || b === undefined ? "" : String(b);
          }
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
        const v = read(f, controls.get(f.name));
        if (f.kind === "vintage") Object.assign(out, v); else out[f.name] = v;
      }
      return out;
    },
  };
}
