// Comparison of final LGD by TermStep across cohorts or across scenarios.

import { api } from "./api.js";
import { h, clear, reportError } from "./ui.js";
import { lineChart, COLORS } from "./chart.js";

// Remembered filters and pivot per project, in this browser only.
export function loadView(pid) {
  try { return JSON.parse(localStorage.getItem(`view:${pid}`) || "{}"); } catch { return {}; }
}
export function saveView(pid, view) {
  try { localStorage.setItem(`view:${pid}`, JSON.stringify(view)); } catch { /* storage unavailable */ }
}

// Tick-box filters for cohorts and scenarios. onChange receives only the changed keys.
export function filterBar(pid, datasets, scenarios, view, onChange) {
  const hidden = { d: new Set(view.hideDatasets || []), s: new Set(view.hideScenarios || []) };
  const chip = (kind, id, label) => {
    const box = h("input", { type: "checkbox", checked: !hidden[kind].has(id) });
    box.addEventListener("change", () => {
      box.checked ? hidden[kind].delete(id) : hidden[kind].add(id);
      onChange({ hideDatasets: [...hidden.d], hideScenarios: [...hidden.s] });
    });
    return h("label", { class: "chip filter" }, box, label);
  };
  const all = (kind, on) => () => {
    (kind === "d" ? datasets : scenarios).forEach((x) => (on ? hidden[kind].delete(x.id) : hidden[kind].add(x.id)));
    onChange({ hideDatasets: [...hidden.d], hideScenarios: [...hidden.s] });
  };
  return h("div", { class: "filters" },
    h("div", { class: "filterrow" }, h("span", { class: "muted small" }, "Cohorts"),
      datasets.map((d) => chip("d", d.id, d.name)),
      h("button", { type: "button", class: "link small", onclick: all("d", true) }, "all"),
      h("button", { type: "button", class: "link small", onclick: all("d", false) }, "none")),
    h("div", { class: "filterrow" }, h("span", { class: "muted small" }, "Scenarios"),
      scenarios.map((s) => chip("s", s.id, s.name)),
      h("button", { type: "button", class: "link small", onclick: all("s", true) }, "all"),
      h("button", { type: "button", class: "link small", onclick: all("s", false) }, "none")));
}

export function applyFilters(view, datasets, scenarios) {
  const hd = new Set(view.hideDatasets || []), hs = new Set(view.hideScenarios || []);
  return { datasets: datasets.filter((d) => !hd.has(d.id)), scenarios: scenarios.filter((s) => !hs.has(s.id)) };
}

// A block with an axis switch: one scenario across cohorts, or one cohort across scenarios.
// `series` is the /projects/{pid}/series response. `fixed` = {axis: "scenario"|"dataset", id}.
export function compareBlock({ pid, datasets, scenarios, series, view, onView, title }) {
  const host = h("div");
  const chartHost = h("div");
  const state = { axis: view.axis || "scenario", scenarioId: view.scenarioId || (scenarios[0] && scenarios[0].id),
                  datasetId: view.datasetId || (datasets[0] && datasets[0].id) };
  const axisSel = h("select", null,
    h("option", { value: "scenario", selected: state.axis === "scenario" }, "One scenario, all cohorts"),
    h("option", { value: "dataset", selected: state.axis === "dataset" }, "One cohort, all scenarios"));
  const scenSel = h("select", null, scenarios.map((s) => h("option", { value: s.id, selected: s.id === state.scenarioId }, s.name)));
  const dsSel = h("select", null, datasets.map((d) => h("option", { value: d.id, selected: d.id === state.datasetId }, d.name)));
  for (const el of [axisSel, scenSel, dsSel]) el.style.width = "auto";
  const note = h("span", { class: "muted small" });

  const draw = () => {
    const byScenario = state.axis === "scenario";
    scenSel.hidden = !byScenario; dsSel.hidden = byScenario;
    const fixedId = byScenario ? Number(scenSel.value) : Number(dsSel.value);
    const items = byScenario ? datasets : scenarios;
    const shown = items.slice(0, COLORS.scenario.length);
    const lines = [];
    shown.forEach((it, i) => {
      const s = series.find((x) => byScenario ? (x.scenario_id === fixedId && x.dataset_id === it.id)
                                              : (x.dataset_id === fixedId && x.scenario_id === it.id));
      if (!s) return;
      lines.push({ name: it.name + (s.stale ? " (out of date)" : ""), color: COLORS.scenario[i % COLORS.scenario.length],
                   pts: s.ts.map((t, k) => [t, s.lgd_final[k]]) });
    });
    const fixedName = byScenario ? (scenarios.find((s) => s.id === fixedId) || {}).name : (datasets.find((d) => d.id === fixedId) || {}).name;
    note.textContent = items.length > shown.length ? `The first ${shown.length} of ${items.length} are shown; use the filters to choose.` : "";
    if (!lines.length) {
      clear(chartHost).classList.add("chart");
      chartHost.append(h("h3", null, title || "Final LGD by TermStep"), h("p", { class: "empty" }, "No computed results for this selection. Run the scenario, or widen the filters."));
      return;
    }
    lineChart(chartHost, {
      title: byScenario ? `${fixedName}: final LGD by TermStep for each cohort` : `${fixedName}: final LGD by TermStep under each scenario`,
      subtitle: byScenario ? "One line per cohort under the chosen scenario." : "One line per scenario for the chosen cohort.",
      xLabel: "TermStep", series: lines,
    });
    onView && onView({ axis: state.axis, scenarioId: Number(scenSel.value), datasetId: Number(dsSel.value) });
  };
  axisSel.addEventListener("change", () => { state.axis = axisSel.value; draw(); });
  scenSel.addEventListener("change", draw);
  dsSel.addEventListener("change", draw);
  host.append(h("div", { class: "actions comparectl" }, h("span", { class: "muted small" }, "Compare"), axisSel, scenSel, dsSel, note), chartHost);
  return { node: host, draw };
}
