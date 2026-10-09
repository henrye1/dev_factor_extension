// Results for one zip under one scenario.

import { api } from "./api.js";
import { h, clear, toast, reportError, fmt, dataTable, tabs, columnsToRows, blank, openDialog } from "./ui.js";
import { lineChart, COLORS } from "./chart.js";
import { FIELDS, fieldSet, fieldText, paramForm } from "./params.js";
import { helpButton } from "./help.js";
import { compareBlock } from "./compare.js";
import { assistantEnabled, assistantPanel } from "./assistant.js";

const SHAPES = [
  { key: "lgd_exp", name: "Exponential", color: COLORS.exp, method: 1 },
  { key: "lgd_power", name: "Power law", color: COLORS.power, method: 2 },
  { key: "lgd_logn", name: "Log-normal", color: COLORS.logn, method: 3 },
];

function rail(avg, method) {
  // Where each tail shape puts the exposure-weighted LGD, against the file's own figure.
  const marks = [{ name: "Original", v: avg.lgd_file, color: COLORS.file }];
  for (const s of SHAPES) if (avg[s.key] !== null) marks.push({ name: s.name, v: avg[s.key], color: s.color, selected: s.method === method });
  const vals = marks.map((m) => m.v);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = Math.max((hi - lo) * 0.18, 0.002);
  lo -= pad; hi += pad;
  const el = h("div", { class: "rail", role: "img",
    "aria-label": marks.map((m) => `${m.name} ${m.v.toFixed(4)}`).join(", ") });
  el.append(h("div", { class: "axis" }));
  marks.sort((a, b) => a.v - b.v);
  marks.forEach((m, i) => { m.at = ((m.v - lo) / (hi - lo)) * 100; m.label = m.at; m.row = i % 2 ? "down" : "up"; });
  // ticks sit at the true value; labels in the same row are pushed apart so they stay readable
  for (const row of ["up", "down"]) {
    const group = marks.filter((m) => m.row === row);
    const gap = 17;
    for (let i = 1; i < group.length; i++) if (group[i].label - group[i - 1].label < gap) group[i].label = group[i - 1].label + gap;
    const over = group.length ? group[group.length - 1].label - 94 : 0;
    if (over > 0) group.forEach((m) => { m.label -= over; });
    for (let i = group.length - 2; i >= 0; i--) if (group[i + 1].label - group[i].label < gap) group[i].label = group[i + 1].label - gap;
  }
  for (const m of marks) {
    const tick = h("div", { class: "tick" });
    tick.style.left = m.at + "%";
    tick.style.background = m.color;
    const key = h("i");
    const k = h("span", { class: "k" }, key, m.name + (m.selected ? " (selected)" : ""));
    key.style.color = m.color;
    const mark = h("div", { class: `mark ${m.row}` + (m.selected ? " selected" : "") },
      m.row === "down" ? [k, h("span", { class: "v" }, m.v.toFixed(4))] : [h("span", { class: "v" }, m.v.toFixed(4)), k]);
    mark.style.left = `clamp(70px, ${m.label}%, calc(100% - 70px))`;
    el.append(tick, mark);
  }
  return el;
}

// A download link that fetches under the busy panel instead of navigating, so a slow workbook
// cannot be requested twice.
const dl = (label) => (ev) => { ev.preventDefault(); api.download(ev.currentTarget.href, label).catch(reportError); };

function facts(items) {
  return h("dl", { class: "facts" }, items.filter(Boolean).map(([k, v]) => h("div", null, h("dt", null, k), h("dd", null, v))));
}

export async function zipView(pid, did, sid, ctx) {
  const [project, matrix] = await Promise.all([api.get(`/projects/${pid}`), api.get(`/projects/${pid}/matrix`)]);
  const dataset = project.datasets.find((d) => d.id === did);
  if (!dataset) throw Object.assign(new Error("Zip not found"), { status: 404 });
  const canEdit = project.role !== "viewer";
  const mine = matrix.cells.filter((c) => c.dataset_id === did);
  if (sid === null && matrix.scenarios.length) {
    const ok = mine.find((c) => c.status === "ok");
    sid = (ok ? ok.scenario_id : matrix.scenarios[0].id);
  }
  const scenario = matrix.scenarios.find((s) => s.id === sid) || null;
  const cell = mine.find((c) => c.scenario_id === sid) || { status: "none" };
  const [result, sdetail] = await Promise.all([
    cell.status !== "none" ? api.get(`/projects/${pid}/results/${sid}/${did}`) : null,
    sid !== null ? api.get(`/projects/${pid}/scenarios/${sid}`) : null,
  ]);
  const base = `/api/projects/${pid}/results/${sid}/${did}`;
  const p = dataset.profile;

  // ------------------------------------------------------------ zip switcher
  const zips = project.datasets;
  const idx = zips.findIndex((d) => d.id === did);
  const go = (d) => { location.hash = `#/p/${pid}/zip/${d.id}` + (sid !== null ? `/${sid}` : ""); };
  const zipPicker = h("select", { "aria-label": "Zip", id: "zip" },
    zips.map((d) => h("option", { value: d.id, selected: d.id === did },
      d.name + (d.category && d.category !== d.name.replace(/^VB/, "") ? ` (category ${d.category})` : ""))));
  zipPicker.addEventListener("change", () => go(zips.find((d) => String(d.id) === zipPicker.value)));
  zipPicker.style.width = "auto";
  const zipNav = h("span", { class: "zipnav" },
    h("button", { type: "button", disabled: idx <= 0, title: "Previous zip", "aria-label": "Previous zip", onclick: () => go(zips[idx - 1]) }, "\u2039"),
    zipPicker,
    h("button", { type: "button", disabled: idx >= zips.length - 1, title: "Next zip", "aria-label": "Next zip", onclick: () => go(zips[idx + 1]) }, "\u203a"));

  // ----------------------------------------------------------------- header
  const picker = h("select", { "aria-label": "Scenario" },
    matrix.scenarios.map((s) => {
      const c = mine.find((x) => x.scenario_id === s.id) || { status: "none" };
      const tag = c.status === "none" ? " (not run)" : c.status === "error" ? " (error)" : c.stale ? " (out of date)" : "";
      return h("option", { value: s.id, selected: s.id === sid }, s.name + tag);
    }));
  picker.addEventListener("change", () => { location.hash = `#/p/${pid}/zip/${did}/${picker.value}`; });
  const runBtn = h("button", { type: "button", class: "primary" }, cell.status === "none" ? "Run scenario" : "Run again");
  runBtn.addEventListener("click", async () => {
    runBtn.disabled = true;
    runBtn.textContent = "Running…";
    try {
      const [out] = await api.post(`/projects/${pid}/scenarios/${sid}/run`, { dataset_id: did });
      toast(out.status === "ok" ? "Run completed" : "The run failed: " + out.error, out.status !== "ok");
      ctx.route();
    } catch (err) { reportError(err); runBtn.disabled = false; runBtn.textContent = "Run again"; }
  });

  const head = h("div", { class: "pagehead" },
    h("div", null, h("h1", null, dataset.name),
      h("div", { class: "sub" },
        `${dataset.category ? "Category " + dataset.category : "No category"}, TermStep 1–${p.max_ts}, last observed bucket ${p.last_obs_bucket}, ` +
        `rate in file ${fmt.pct(p.implied_rate)}, opening exposure ${fmt.moneyShort(p.opening_exposure)}`)),
    h("div", { class: "actions" },
      h("label", { class: "small muted", for: "zip" }, "Zip"), zipNav,
      scenario ? h("label", { class: "small muted", for: "scn" }, "Scenario") : null,
      scenario ? Object.assign(picker, { id: "scn" }) : null,
      scenario && canEdit ? runBtn : null,
      scenario ? h("a", { class: "btn", href: `#/p/${pid}/scenario/${sid}` }, canEdit ? "Edit scenario" : "View scenario") : null));
  picker.style.width = "auto";

  const trail = [{ label: "Projects", href: "#/projects" }, { label: project.name, href: `#/p/${pid}` }, { label: dataset.name }];
  if (!scenario) {
    return { trail, node: h("div", null, head, h("section", { class: "block" },
      h("p", { class: "empty" }, "This project has no scenarios yet. Create one on the project page, then run it."))) };
  }

  // ------------------------------------------------------------- assumptions
  // The current scenario values with this zip's own values laid on top. Editable here so a
  // reviewer can change an assumption and rerun without leaving the results.
  const override = sdetail.overrides[String(did)] || {};
  const effective = { ...sdetail.params, ...override };
  const formCtx = {
    eventTypes: [...new Set(project.datasets.flatMap((d) => d.profile.event_types || []))],
  };
  if (!formCtx.eventTypes.length) formCtx.eventTypes = ["Lifetime", "LifetimeSingle", "TwelveMonthSingle"];
  const editAssumptions = () => {
    const form = paramForm(sdetail.params, formCtx, { override });
    const runAfter = async (datasetId) => {
      const out = await api.post(`/projects/${pid}/scenarios/${sid}/run`, datasetId ? { dataset_id: datasetId } : {});
      const bad = out.filter((o) => o.status === "error");
      toast(bad.length ? `Saved. ${bad.length} of ${out.length} zip(s) failed: ${bad[0].error}` : `Saved and run (${out.length} zip${out.length === 1 ? "" : "s"})`, bad.length > 0);
      ctx.route();
    };
    openDialog({
      title: `Assumptions for ${dataset.name} under ${scenario.name}`, wide: true,
      body: h("div", null,
        h("p", { class: "muted" }, "Tick an assumption to give this zip its own value; unticked ones follow the scenario. " +
          "Save for this zip keeps every other zip as it is. Save for all zips writes the ticked values into the scenario itself."),
        form.node),
      actions: [{ label: "Cancel" }, {
        label: "Save for all zips and run",
        run: async () => {
          let values;
          try { values = form.values(); } catch (err) { toast(err.message, true); return true; }
          if (!Object.keys(values).length) { toast("Tick at least one assumption", true); return true; }
          await api.put(`/projects/${pid}/scenarios/${sid}`, { params: { ...sdetail.params, ...values } });
          const rest = Object.fromEntries(Object.entries(override).filter(([k]) => !(k in values)));
          if (Object.keys(rest).length !== Object.keys(override).length) {
            await api.put(`/projects/${pid}/scenarios/${sid}/overrides/${did}`, { params: rest });
          }
          await runAfter(null);
        },
      }, {
        label: "Save for this zip and run", kind: "primary",
        run: async () => {
          let values;
          try { values = form.values(); } catch (err) { toast(err.message, true); return true; }
          await api.put(`/projects/${pid}/scenarios/${sid}/overrides/${did}`, { params: values });
          await runAfter(did);
        },
      }],
    });
  };
  const nOverride = Object.keys(override).length;
  const assumptions = h("section", { class: "block" },
    h("header", null,
      h("h2", null, "Assumptions"),
      h("div", { class: "actions" },
        h("span", { class: "muted small" }, nOverride
          ? `${nOverride} value${nOverride === 1 ? "" : "s"} specific to this zip, shown in blue. The rest follow ${scenario.name}.`
          : `All values follow ${scenario.name}. Select ? on any assumption for an explanation.`),
        canEdit ? h("button", { type: "button", onclick: editAssumptions }, "Edit assumptions") : null)),
    h("div", { class: "assume" }, FIELDS.map((f) => {
      const text = fieldText(f, effective);
      const i = text.indexOf(": ");
      const own = fieldSet(f, override);
      return h("div", null,
        h("span", { class: "k" }, text.slice(0, i), helpButton(f.name)),
        h("span", { class: "v" + (own ? " ovr" : ""), title: own ? "This zip has its own value" : "" }, text.slice(i + 2)));
    })));

  if (!result) {
    return { trail, node: h("div", null, head, assumptions, h("section", { class: "block" },
      h("p", { class: "empty" }, `${scenario.name} has not been run for this zip yet.` + (canEdit ? " Select Run scenario, or edit the assumptions and save." : "")))) };
  }
  if (result.status === "error") {
    return { trail, node: h("div", null, head,
      h("p", { class: "notice error" }, "This run failed: " + result.error), assumptions,
      h("section", { class: "block" }, h("h2", null, "Parameters used in the failed run"), paramTable(result.effective_params))) };
  }

  const pay = result.payload, avg = pay.averages, cfg = pay.config, prm = result.effective_params;
  const R = pay.results, L = pay.lgd_ts;
  if (result.legacy) {
    // computed before the reference-curve method was replaced by log-normal: its figures are not current
    return { trail, node: h("div", null, head,
      h("p", { class: "notice" }, "This result was computed before the reference-curve method was replaced by the log-normal method (9 October 2026). Run the scenario again to see current figures."),
      assumptions) };
  }
  const hasLogn = avg.lgd_logn !== null && avg.lgd_logn !== undefined;
  const fileLabel = cfg.lgd_file_label || "Original LGD (file)";
  const vintageLine = cfg.vintage_filter
    ? `Vintages: ${cfg.vintage_start_effective} to ${cfg.vintage_last} (${cfg.cohorts_included} of ${cfg.cohorts_total}, ${Math.round(cfg.exposure_share * 100)}% of exposure)`
    : cfg.vintage_first ? `All vintages (${cfg.vintage_first} to ${cfg.vintage_last}, ${cfg.cohorts_total} cohorts)` : "All vintages";
  head.querySelector(".sub").append(h("div", null, vintageLine));

  // ---------------------------------------------------------------- notices
  const notices = h("div");
  if (result.stale) notices.append(h("p", { class: "notice" }, "The scenario, an override or the zip's data changed after this run. The figures below are from the earlier inputs. Run again to refresh them and to export to Excel."));
  for (const w of pay.warnings) notices.append(h("p", { class: "notice" }, w));

  // --------------------------------------------------------------- headline
  const tie = Math.max(Math.abs(avg.tie_max ?? 0), Math.abs(avg.tie_min ?? 0));
  const headline = h("section", { class: "block" },
    h("header", null, h("h2", null, "Exposure-weighted LGD across all TermSteps"),
      h("span", { class: "muted small" }, `Run ${fmt.date(result.computed_at)}`)),
    rail(avg, cfg.method),
    facts([
      ["Uplift in PV recoveries", `${fmt.signed(avg.uplift)} weighted, ${fmt.signed(avg.uplift_simple)} simple average`],
      ["Discount rate", fmt.pct(cfg.rate)],
      ["λ", `${fmt.num(cfg.lam, 4)}${prm.lambda_override !== null ? " (override)" : ""}`],
      ["Half-life", cfg.half_life === null ? blank : `${cfg.half_life.toFixed(1)} buckets`],
      ["γ", `${fmt.num(cfg.gam, 2)}${prm.gamma_override !== null ? " (override)" : ""}`],
      ["Log-normal μ, σ", hasLogn ? `${fmt.num(cfg.mu, 3)}${prm.mu_override !== null ? " (override)" : ""}, ${fmt.num(cfg.sigma, 3)}${prm.sigma_override !== null ? " (override)" : ""}` : "undefined"],
      ["Log-normal peak bucket", cfg.logn_mode === null || cfg.logn_mode === undefined ? blank : cfg.logn_mode.toFixed(1)],
      ["Credibility cut", fmt.moneyShort(cfg.min_exposure_abs)],
      ["Tie-out to file", tie < 1e-9 ? "0.0000" : tie.toExponential(1)],
      ["Own rows to TermStep", String(cfg.last_ts)],
    ]));

  // ------------------------------------------------ forecast curve parameters
  // Every number behind the three forecast curves: the shape's parameters as fitted and as
  // used, and the level that anchors the shape on TermStep 1 and on the base row.
  const TF = pay.tail_fit;
  const baseRow = Math.max(0, Math.min(pay.n, prm.base_ts) - 1);
  const pv = (fitted, used, over, d) => fitted === null && used === null ? "undefined"
    : `${fmt.num(used, d)}${over ? " (override, fitted " + fmt.num(fitted, d) + ")" : ""}`;
  const curveRows = [
    { shape: "Exponential", formula: "s(b) = e^(−λb)", params: `λ = ${pv(cfg.lam_fit, cfg.lam, prm.lambda_override !== null, 4)}`,
      extra: cfg.half_life === null ? blank : `half-life ${cfg.half_life.toFixed(1)} buckets`, k: "scale_exp", sel: cfg.method === 1 },
    { shape: "Power law", formula: "s(b) = b^(−γ)", params: `γ = ${pv(cfg.gam_fit, cfg.gam, prm.gamma_override !== null, 3)}`,
      extra: blank, k: "scale_power", sel: cfg.method === 2 },
    { shape: "Log-normal", formula: "s(b) = (1/b)·exp(−(ln b − μ)² / (2σ²))",
      params: `μ = ${pv(cfg.mu_fit, cfg.mu, prm.mu_override !== null, 3)}; σ = ${pv(cfg.sigma_fit, cfg.sigma, prm.sigma_override !== null, 3)}`,
      extra: cfg.logn_mode === null || cfg.logn_mode === undefined ? blank : `peak at bucket ${cfg.logn_mode.toFixed(1)}, median ${cfg.logn_median.toFixed(1)}`,
      k: "scale_logn", sel: cfg.method === 3 },
  ].map((r) => ({ ...r, shape: r.shape + (r.sel ? " (selected)" : ""), scale1: TF[r.k][0], scaleBase: TF[r.k][baseRow] }));
  const sig6 = (v) => (v === null || v === undefined ? blank : v.toPrecision(6));
  const curveParams = h("section", { class: "block" },
    h("header", null, h("h2", null, "Forecast curve parameters"),
      h("span", { class: "muted small" },
        `Fitted on TermStep ${prm.ref_ts}, buckets ${prm.fit_start} to ${cfg.ref_last_cred} (${cfg.fit_points} points` +
        `${cfg.logn_points !== cfg.fit_points ? `, ${cfg.logn_points} for the log-normal` : ""}). ` +
        `Beyond the last credible bucket, RecoveryPct(ts, b) = max(${prm.floor}, scale(ts) × s(b)).`)),
    dataTable([
      { label: "Shape", key: "shape" },
      { label: "Formula", key: "formula" },
      { label: "Parameters used (fitted)", key: "params" },
      { label: "Derived", key: "extra" },
      { label: "Scale, TermStep 1", key: "scale1", num: true, fmt: sig6 },
      { label: `Scale, base row ${prm.base_ts}`, key: "scaleBase", num: true, fmt: sig6 },
    ], curveRows, { plain: true, rowClass: (r) => (r.sel ? "selectedrow" : "") }));

  // ----------------------------------------------------------------- charts
  // charts stop at the last TermStep that has data; the tables keep every row
  const ts = R.ts.filter((t) => t <= cfg.last_data_ts), T = L.ts;
  const pts = (xs, ys) => xs.map((x, i) => [x, ys[i]]);
  const cA = h("div"), cB = h("div"), cC = h("div"), cD = h("div"), cE = h("div"), cF = h("div");
  const APPLIED = "#4a3aa7";
  let applied = { available: false };
  const drawCharts = async () => {
    try { applied = await api.get(`/projects/${pid}/results/${sid}/${did}/applied`); } catch (err) { reportError(err); }
    const appliedLgd = applied.available
      ? [{ name: `Client applied (${applied.label}), implied LGD`, color: APPLIED, dash: true, pts: pts(applied.ts, applied.lgd) }] : [];
    lineChart(cA, {
      title: cfg.vintage_filter ? "LGD by TermStep: vintage subset against each tail shape" : "LGD by TermStep: file against each tail shape",
      subtitle: "The gap to the dashed line is what truncation of the triangle was costing.",
      xLabel: "TermStep",
      series: [
        { name: fileLabel, color: COLORS.file, dash: true, pts: pts(ts, R.lgd_file) },
        { name: "Exponential", color: COLORS.exp, pts: pts(ts, R.lgd_exp) },
        { name: "Power law", color: COLORS.power, pts: pts(ts, R.lgd_power) },
        ...(hasLogn ? [{ name: "Log-normal", color: COLORS.logn, pts: pts(ts, R.lgd_logn) }] : []),
        ...(applied.available ? [{ name: `Client applied (${applied.label}), implied LGD`, color: APPLIED, dash: true,
          pts: pts(ts, applied.lgd.slice(0, ts.length)) }] : []),
      ],
    });
    lineChart(cB, {
      title: `Uplift in PV recoveries by TermStep (${cfg.method_label.toLowerCase()})`,
      subtitle: "Selected PV recoveries less the observed-only PV, as a share of the balance at that TermStep.",
      xLabel: "TermStep",
      series: [{ name: "Uplift", color: SHAPES[cfg.method - 1].color, pts: pts(ts, R.uplift) }],
    });
    lineChart(cC, {
      title: `LGD to TermStep ${cfg.target_ts}`,
      subtitle: `Own observed-and-extended rows to TermStep ${cfg.last_ts}, then rolled forward from row ${prm.base_ts}.`,
      xLabel: "TermStep",
      refs: [{ x: cfg.last_ts, label: "LastTS" }],
      series: [
        { name: fileLabel, color: COLORS.file, dash: true, pts: pts(T, L.lgd_file) },
        { name: "Derived from base row", color: SHAPES[cfg.method - 1].color, pts: pts(T, L.derived_selected) },
        { name: "Final", color: COLORS.final, width: 2.5, pts: pts(T, L.lgd_final) },
        ...appliedLgd,
      ],
    });
    lineChart(cD, {
      title: "Lifetime LGD against horizon LGD",
      subtitle: `Recoveries counted to MaxBucket, within ${prm.horizon2} months, and within ${prm.horizon} months.`,
      xLabel: "TermStep",
      series: [
        // one measure at three horizons: shades of the ink, longest horizon darkest
        { name: "Lifetime (final)", color: COLORS.final, width: 2.5, pts: pts(T, L.lgd_final) },
        { name: `Within ${prm.horizon2} months`, color: "#5b6b8c", pts: pts(T, L.lgd_valuation_horizon) },
        { name: `Within ${prm.horizon} months`, color: "#a3adc2", pts: pts(T, L.lgd_short_horizon) },
      ],
    });
    drawCurve(1);
    drawCompare();
  };

  const tsInput = h("input", { type: "number", min: 1, max: pay.n, value: 1, "aria-label": "TermStep" });
  let curveToken = 0;
  async function drawCurve(t) {
    const token = ++curveToken;
    try {
      const c = await api.get(`/projects/${pid}/results/${sid}/${did}/curve?ts=${t}`);
      if (token !== curveToken) return;
      lineChart(cE, {
        title: `RecoveryPct by bucket at TermStep ${c.ts}`,
        subtitle: "Log scale. Observed buckets are kept to the last credible bucket; each shape continues from there.",
        xLabel: "Bucket", yLog: true,
        control: [h("span", null, "TermStep"), tsInput],
        refs: [{ x: c.last_cred, label: "Last credible" }],
        series: [
          { name: "Observed", color: COLORS.final, dots: true, pts: pts(c.bucket, c.observed) },
          { name: "Exponential", color: COLORS.exp, pts: pts(c.bucket, c.exp) },
          { name: "Power law", color: COLORS.power, pts: pts(c.bucket, c.power) },
          ...(hasLogn ? [{ name: "Log-normal", color: COLORS.logn, pts: pts(c.bucket, c.logn) }] : []),
          ...(c.applied ? [{ name: `Client applied (${c.applied_label})`, color: APPLIED, dash: true, pts: pts(c.bucket, c.applied) }] : []),
        ],
      });
    } catch (err) { reportError(err); }
  }
  tsInput.addEventListener("change", () => {
    const t = Math.min(pay.n, Math.max(1, Math.round(Number(tsInput.value) || 1)));
    tsInput.value = t;
    drawCurve(t);
  });

  async function drawCompare() {
    // One cohort across scenarios, or this scenario across cohorts: the same switch as the project page.
    try {
      const series = await api.get(`/projects/${pid}/series`);
      clear(cF).className = "";
      const block = compareBlock({
        pid, datasets: project.datasets, scenarios: matrix.scenarios, series,
        view: { axis: "dataset", datasetId: did, scenarioId: sid },
      });
      cF.append(block.node);
      block.draw();
    } catch (err) { reportError(err); }
  }

  // ----------------------------------------------------------------- tables
  const sel = SHAPES[cfg.method - 1].key;
  const lgdCol = (label, key) => ({ label, key, num: true, fmt: fmt.lgd, cls: key === sel ? "sel" : "" });
  const resultsTable = () => dataTable([
    { label: "TermStep", key: "ts", num: true },
    { label: "Exposure at ts (R)", key: "exposure", num: true, fmt: fmt.money },
    lgdCol(fileLabel, "lgd_file"),
    lgdCol("Replica LGD", "lgd_replica"),
    { label: "Tie-out", key: "tie_out", num: true, fmt: fmt.sci },
    { label: "File LGD floor", key: "file_floor_gap", num: true, fmt: (v) => (v === null ? blank : v > 1e-12 ? v.toFixed(6) : "0") },
    lgdCol("LGD exponential", "lgd_exp"), lgdCol("LGD power", "lgd_power"), lgdCol("LGD log-normal", "lgd_logn"),
    { label: "LGD selected", key: "lgd_selected", num: true, fmt: fmt.lgd, cls: "sel" },
    lgdCol("Uplift in recoveries", "uplift"),
    lgdCol(`LGD within ${prm.horizon} months`, "lgd_horizon"),
    lgdCol("Undiscounted, observed", "undisc_observed"), lgdCol("Undiscounted, selected", "undisc_selected"),
    { label: "Last credible bucket", key: "last_cred", num: true },
    { label: "Buckets added", key: "buckets_added", num: true },
  ], columnsToRows(R));
  const dsel = ["derived_exp", "derived_power", "derived_logn"][cfg.method - 1];
  const dCol = (label, key) => ({ label, key, num: true, fmt: fmt.lgd, cls: key === dsel ? "sel" : "" });
  const lgdTable = () => dataTable([
    { label: "TermStep", key: "ts", num: true },
    { label: "Source", key: "source" },
    { label: "LGD final", key: "lgd_final", num: true, fmt: fmt.lgd, cls: "sel" },
    lgdCol(fileLabel, "lgd_file"),
    lgdCol("Own-row LGD", "lgd_own"),
    dCol("Derived, exponential", "derived_exp"), dCol("Derived, power", "derived_power"), dCol("Derived, log-normal", "derived_logn"),
    lgdCol("Validation (own − derived)", "validation"),
    lgdCol("Balance factor", "balance_factor"),
    lgdCol(`LGD within ${prm.horizon2} months`, "lgd_valuation_horizon"),
    lgdCol(`LGD within ${prm.horizon} months`, "lgd_short_horizon"),
    lgdCol("Undiscounted remaining", "undisc_remaining"),
    { label: "Exposure at ts (R)", key: "exposure", num: true, fmt: fmt.money },
  ], columnsToRows(L), { rowClass: (r) => (r.source === "derived" ? "derived" : "") });
  const six = (v) => (v === null ? blank : v.toPrecision(6));
  const tailTable = () => dataTable([
    { label: "TermStep", key: "ts", num: true },
    { label: "Last observed bucket", key: "last_obs", num: true },
    { label: "Last credible bucket", key: "last_cred", num: true },
    { label: "Window start", key: "win_start", num: true },
    { label: "Window end", key: "win_end", num: true },
    { label: "Buckets in window", key: "n_win", num: true },
    { label: "Σ observed over window", key: "sum_obs", num: true, fmt: six },
    { label: "Scale, exponential", key: "scale_exp", num: true, fmt: six },
    { label: "Scale, power", key: "scale_power", num: true, fmt: six },
    { label: "Scale, log-normal", key: "scale_logn", num: true, fmt: six },
  ], columnsToRows(pay.tail_fit));
  const s = pay.lgd_ts_summary;
  const tables = h("section", { class: "block" },
    h("header", null, h("h2", null, "Tables"),
      h("div", { class: "actions" },
        // Excel workbooks are rebuilt from the current inputs, so they wait for a fresh run
        result.stale
          ? h("button", { type: "button", disabled: true, title: "Run the scenario again to export to Excel" }, "Excel, values")
          : h("a", { class: "btn", href: `${base}/export?kind=values`, onclick: dl("Building the values workbook…") }, "Excel, values"),
        result.stale
          ? h("button", { type: "button", disabled: true, title: "Run the scenario again to export to Excel" }, "Excel, live formulas")
          : h("a", { class: "btn", href: `${base}/export?kind=formula`, onclick: dl("Building the formula workbook. Large zips take up to a minute…") }, "Excel, live formulas"),
        h("a", { class: "btn", href: `${base}/export?kind=csv&table=results`, onclick: dl("Preparing the CSV…") }, "CSV, results"),
        h("a", { class: "btn", href: `${base}/export?kind=csv&table=lgd_ts`, onclick: dl("Preparing the CSV…") }, "CSV, LGD to Target"),
        h("a", { class: "btn", href: `${base}/export?kind=csv&table=tail_fit`, onclick: dl("Preparing the CSV…") }, "CSV, tail fit"),
        h("a", { class: "btn", href: `/api/projects/${pid}/export/results?scenario_id=${sid}`, title: "One workbook with these tables for every cohort under this scenario",
          onclick: dl(`Building the all-cohorts workbook for ${scenario.name}…`) }, "Excel, all cohorts"))),
    tabs([
      { label: `LGD to TermStep ${cfg.target_ts}`, render: () => h("div", null,
          facts([["Validation, own row less derived", `${fmt.signed(s.validation_min)} to ${fmt.signed(s.validation_max)}`],
                 [`Balance factor at TermStep ${cfg.target_ts}`, fmt.lgd(s.balance_factor_target)]]),
          h("div", { class: "small" }, " "), lgdTable()) },
      { label: "Results by TermStep", render: resultsTable },
      { label: "Tail fit", render: tailTable },
      { label: "Parameters used", render: () => paramTable(prm, result.overrides) },
    ]));

  const assistant = (await assistantEnabled())
    ? assistantPanel({ pid, view: `the results of zip ${dataset.name} (category ${dataset.category}) under scenario ${scenario.name}`,
                       canApply: canEdit, onApplied: ctx.route }) : null;
  return {
    trail,
    node: h("div", null, head, notices, assumptions, headline, curveParams,
      h("div", { class: "charts" }, cA, cC, cE, cB, cD, cF), tables, assistant),
    after: drawCharts,
  };
}

function paramTable(params) {
  const rows = FIELDS.map((f) => {
    const text = fieldText(f, params);
    const i = text.indexOf(": ");
    return { label: text.slice(0, i), value: text.slice(i + 2), hint: f.hint };
  });
  return dataTable([{ label: "Parameter", key: "label" }, { label: "Value", key: "value" }, { label: "Meaning", key: "hint" }], rows, { plain: true });
}
