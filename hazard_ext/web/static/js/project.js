// Project home: the scenario-by-zip matrix, the zips and the scenarios.

import { api } from "./api.js";
import { h, clear, toast, reportError, openDialog, confirmDialog, field, fmt, dataTable } from "./ui.js";
import { METHODS } from "./params.js";
import { applyFilters, compareBlock, filterBar, loadView, saveView } from "./compare.js";
import { assistantEnabled, assistantPanel } from "./assistant.js";

export function statusChip(cell) {
  if (cell.status === "none") return h("span", { class: "chip" }, "Not run");
  if (cell.status === "error") return h("span", { class: "chip error", title: cell.error }, "Error");
  if (cell.stale) return h("span", { class: "chip stale", title: "Inputs changed since this was run" }, "Out of date");
  return null;
}

export async function projectView(pid, ctx) {
  const [project, matrix, scenarios, series] = await Promise.all([
    api.get(`/projects/${pid}`), api.get(`/projects/${pid}/matrix`), api.get(`/projects/${pid}/scenarios`),
    api.get(`/projects/${pid}/series`)]);
  const canEdit = project.role !== "viewer";
  const refresh = ctx.route;
  let view = loadView(pid);

  // ------------------------------------------------------------------ matrix
  // Rows are cohorts and columns scenarios, or the other way round (Pivot). Filters hide
  // cohorts or scenarios from the matrix and the comparison chart alike.
  const cells = new Map(matrix.cells.map((c) => [`${c.scenario_id}:${c.dataset_id}`, c]));
  const matrixHost = h("div");
  const compareHost = h("div");
  const filterHost = h("div");

  const cellNode = (s, d) => {
    const c = cells.get(`${s.id}:${d.id}`);
    const sm = c.summary || {};
    const td = h("td", { class: "cell" + (c.status === "ok" ? "" : " none") });
    if (c.status === "ok") {
      td.append(
        h("div", { class: "lgd" }, fmt.lgd(sm.lgd_selected)),
        h("div", { class: "delta" }, `from ${fmt.lgd(sm.lgd_file)}, uplift ${fmt.signed(sm.uplift)}`),
        h("div", { class: "delta" }, sm.method_label, c.has_override ? ", overridden" : ""));
      td.addEventListener("click", () => { location.hash = `#/p/${pid}/zip/${d.id}/${s.id}`; });
      td.tabIndex = 0;
      td.addEventListener("keydown", (ev) => { if (ev.key === "Enter") location.hash = `#/p/${pid}/zip/${d.id}/${s.id}`; });
    }
    const chip = statusChip(c);
    if (chip) td.append(h("div", null, chip));
    if (c.status === "error") td.append(h("div", { class: "delta" }, c.error.length > 90 ? c.error.slice(0, 90) + "…" : c.error));
    return td;
  };
  const drawMatrix = () => {
    clear(matrixHost);
    const { datasets, scenarios: scns } = applyFilters(view, matrix.datasets, matrix.scenarios);
    if (!matrix.datasets.length || !matrix.scenarios.length) {
      matrixHost.append(h("p", { class: "empty" },
        !matrix.datasets.length ? "Upload the debug zips below to begin." : "Create a scenario below, then run it to fill this table."));
      return;
    }
    if (!datasets.length || !scns.length) {
      matrixHost.append(h("p", { class: "empty" }, "Everything is filtered out. Tick a cohort and a scenario above."));
      return;
    }
    const pivot = view.pivot === true;        // true: scenarios down the side, cohorts across
    const rowItems = pivot ? scns : datasets, colItems = pivot ? datasets : scns;
    const rowLink = (it) => pivot ? h("a", { href: `#/p/${pid}/scenario/${it.id}` }, it.name)
      : h("div", null, h("a", { href: `#/p/${pid}/zip/${it.id}` }, it.name), h("div", { class: "small muted" }, it.category ? `Category ${it.category}` : "No category"));
    const colLink = (it) => pivot ? h("a", { href: `#/p/${pid}/zip/${it.id}` }, it.name + (it.category ? ` (${it.category})` : ""))
      : h("a", { href: `#/p/${pid}/scenario/${it.id}` }, it.name);
    const thead = h("thead", null, h("tr", null, h("th", null, pivot ? "Scenario" : "Zip"),
      colItems.map((it) => h("th", { class: "scn" }, colLink(it)))));
    const tbody = h("tbody");
    for (const r of rowItems) {
      const tr = h("tr", null, h("td", null, rowLink(r)));
      for (const c of colItems) tr.append(pivot ? cellNode(r, c) : cellNode(c, r));
      tbody.append(tr);
    }
    matrixHost.append(h("div", { class: "tablewrap plain" }, h("table", { class: "matrix" }, thead, tbody)));
  };
  const drawCompare = () => {
    clear(compareHost);
    const { datasets, scenarios: scns } = applyFilters(view, matrix.datasets, matrix.scenarios);
    if (!datasets.length || !scns.length || !series.length) return;
    const block = compareBlock({ pid, datasets, scenarios: scns, series, view,
      onView: (v) => { view = { ...view, ...v }; saveView(pid, view); } });
    compareHost.append(block.node);
    block.draw();
  };
  const onFilter = (v) => { view = { ...view, ...v }; saveView(pid, view); drawMatrix(); drawCompare(); };
  const pivotBtn = h("button", { type: "button", onclick: () => { view = { ...view, pivot: !view.pivot }; saveView(pid, view); drawMatrix(); } }, "Pivot rows and columns");
  if (matrix.datasets.length && matrix.scenarios.length) {
    filterHost.append(filterBar(pid, matrix.datasets, matrix.scenarios, view, onFilter));
  }
  drawMatrix();
  const matrixNode = h("div", null, filterHost, matrixHost);
  const runAll = async (btn) => {
    btn.disabled = true;
    btn.textContent = "Running…";
    try {
      const r = await api.post(`/projects/${pid}/run-all`);
      toast(`${r.ok} run${r.ok === 1 ? "" : "s"} completed` + (r.errors ? `, ${r.errors} with errors` : ""), r.errors > 0);
      refresh();
    } catch (err) { reportError(err); btn.disabled = false; btn.textContent = "Run all scenarios"; }
  };

  // -------------------------------------------------------------------- zips
  const uploads = h("ul", { class: "uploads" });
  const input = h("input", { type: "file", accept: ".zip", multiple: true });
  const drop = h("div", { class: "drop", tabindex: "0", role: "button" },
    "Drop debug zips here, or click to choose. Several at once is fine. Only lgd_recovery.csv and debug.json are read.", input);
  const send = async (files) => {
    const list = [...files].filter((f) => f.name.toLowerCase().endsWith(".zip"));
    if (!list.length) return toast("Choose .zip files", true);
    drop.classList.add("busy");
    clear(uploads);
    let any = false;
    for (const file of list) {                       // one request per file, so one slow zip does not hold the rest
      const li = h("li", null, h("span", null, file.name), h("span", null, "Uploading…"));
      uploads.append(li);
      const form = new FormData();
      form.append("files", file);
      try {
        const [out] = await api.upload(`/projects/${pid}/datasets`, form);
        li.className = out.ok ? "good" : "bad";
        li.lastChild.textContent = out.ok
          ? `Added as ${out.dataset.name}: TermStep 1–${out.dataset.profile.max_ts}, rate ${fmt.pct(out.dataset.profile.implied_rate)}`
          : out.error;
        any = any || out.ok;
      } catch (err) {
        li.className = "bad";
        li.lastChild.textContent = err.message;
      }
    }
    drop.classList.remove("busy");
    if (any) { toast("Upload finished"); setTimeout(refresh, 1200); }
  };
  if (canEdit) {
    drop.addEventListener("click", () => input.click());
    drop.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); input.click(); } });
    input.addEventListener("change", () => send(input.files));
    drop.addEventListener("dragover", (ev) => { ev.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (ev) => { ev.preventDefault(); drop.classList.remove("over"); send(ev.dataTransfer.files); });
  }
  const editZip = (d) => {
    const name = h("input", { type: "text", value: d.name });
    const cat = h("input", { type: "text", value: d.category });
    openDialog({
      title: `Edit ${d.name}`,
      body: h("div", null, field("Name", name),
        field("Category", cat, "Decides which reference curve the zip uses when the scenario leaves the reference curve empty.")),
      actions: [{ label: "Cancel" }, { label: "Save changes", kind: "primary",
        run: async () => { await api.patch(`/projects/${pid}/datasets/${d.id}`, { name: name.value, category: cat.value }); toast("Saved"); refresh(); } }],
    });
  };
  const deleteZip = (d) => confirmDialog(`Delete ${d.name}?`,
    "Its stored data, overrides and results in every scenario are deleted. The original zip is not kept, so it would have to be uploaded again.",
    "Delete zip", async () => { await api.del(`/projects/${pid}/datasets/${d.id}`); toast("Deleted"); refresh(); });
  const zipTable = project.datasets.length ? dataTable([
    { label: "Zip", key: "name", fmt: (v, d) => h("div", null, h("a", { href: `#/p/${pid}/zip/${d.id}` }, v), h("div", { class: "small muted" }, d.filename)) },
    { label: "Category", key: "category" },
    { label: "TermSteps", key: "profile", num: true, fmt: (p) => `1–${p.max_ts}` },
    { label: "Last observed bucket", key: "profile", num: true, fmt: (p) => p.last_obs_bucket },
    { label: "Rate in file", key: "profile", num: true, fmt: (p) => fmt.pct(p.implied_rate) },
    { label: "Opening exposure", key: "profile", num: true, fmt: (p) => fmt.moneyShort(p.opening_exposure) },
    { label: "LGD window", key: "parameters", fmt: (p) => (p.LgdMinDate ? `${p.LgdMinDate} to ${p.LgdMaxDate}` : "–") },
    ...(canEdit ? [{ label: "", key: "id", fmt: (_, d) => h("div", { class: "actions" },
        h("button", { type: "button", onclick: () => editZip(d) }, "Edit"),
        h("button", { type: "button", class: "danger", onclick: () => deleteZip(d) }, "Delete")) }] : []),
  ], project.datasets, { plain: true }) : h("p", { class: "empty" }, "No zips yet.");

  // --------------------------------------------------------------- scenarios
  const newScenario = () => {
    const name = h("input", { type: "text", placeholder: "Base – reference curve shape" });
    const desc = h("textarea", { rows: 2 });
    openDialog({
      title: "New scenario",
      body: h("div", null, field("Name", name), field("Description", desc, "Optional."),
        h("p", { class: "muted small" }, "It starts with the workbook defaults: reference curve shape, Target TermStep 300, MaxBucket 420, MinExposure R100m. You set the parameters on the next page.")),
      actions: [{ label: "Cancel" }, { label: "Create scenario", kind: "primary",
        run: async () => {
          const s = await api.post(`/projects/${pid}/scenarios`, { name: name.value, description: desc.value });
          location.hash = `#/p/${pid}/scenario/${s.id}`;
        } }],
    });
  };
  const clone = (sc) => {
    const name = h("input", { type: "text", value: sc.name + " copy" });
    openDialog({
      title: `Copy ${sc.name}`,
      body: field("Name of the copy", name, "Parameters and per-zip overrides are copied."),
      actions: [{ label: "Cancel" }, { label: "Copy scenario", kind: "primary",
        run: async () => { const s = await api.post(`/projects/${pid}/scenarios/${sc.id}/clone`, { name: name.value }); location.hash = `#/p/${pid}/scenario/${s.id}`; } }],
    });
  };
  const runOne = async (sc, btn) => {
    btn.disabled = true;
    try {
      const out = await api.post(`/projects/${pid}/scenarios/${sc.id}/run`, {});
      const bad = out.filter((o) => o.status === "error").length;
      toast(`${sc.name}: ${out.length - bad} of ${out.length} zips run` + (bad ? `, ${bad} with errors` : ""), bad > 0);
      refresh();
    } catch (err) { reportError(err); btn.disabled = false; }
  };
  const delScenario = (sc) => confirmDialog(`Delete ${sc.name}?`, "The scenario, its overrides and its results are deleted.",
    "Delete scenario", async () => { await api.del(`/projects/${pid}/scenarios/${sc.id}`); toast("Deleted"); refresh(); });
  const scTable = scenarios.length ? dataTable([
    { label: "Scenario", key: "name", fmt: (v, s) => h("div", null, h("a", { href: `#/p/${pid}/scenario/${s.id}` }, v),
        s.description ? h("div", { class: "small muted" }, s.description) : null) },
    { label: "Method", key: "params", fmt: (p) => METHODS[p.method] },
    { label: "Target / MaxBucket", key: "params", num: true, fmt: (p) => `${p.target_ts} / ${p.max_bucket}` },
    { label: "MinExposure", key: "params", num: true, fmt: (p) => (p.min_exposure_mode === "pct" ? `${p.min_exposure}% of opening` : fmt.moneyShort(p.min_exposure)) },
    { label: "Overrides", key: "overrides", num: true, fmt: (o) => Object.keys(o).length || "–" },
    ...(canEdit ? [{ label: "", key: "id", fmt: (_, s) => {
        const run = h("button", { type: "button", class: "primary" }, "Run");
        run.addEventListener("click", () => runOne(s, run));
        return h("div", { class: "actions" }, run,
          h("button", { type: "button", onclick: () => clone(s) }, "Copy"),
          h("button", { type: "button", class: "danger", onclick: () => delScenario(s) }, "Delete"));
      } }] : []),
  ], scenarios, { plain: true }) : h("p", { class: "empty" }, "No scenarios yet. A scenario is a named set of parameters that runs against every zip.");

  const runAllBtn = h("button", { type: "button", class: "primary" }, "Run all scenarios");
  runAllBtn.addEventListener("click", () => runAll(runAllBtn));

  const assistant = (await assistantEnabled())
    ? assistantPanel({ pid, view: `the project page of ${project.name}`, canApply: canEdit, onApplied: refresh }) : null;

  return {
    after: drawCompare,
    trail: [{ label: "Projects", href: "#/projects" }, { label: project.name }],
    node: h("div", null,
      h("div", { class: "pagehead" },
        h("div", null, h("h1", null, project.name), project.description ? h("div", { class: "sub" }, project.description) : null),
        h("div", { class: "actions" },
          canEdit && matrix.datasets.length && matrix.scenarios.length ? runAllBtn : null,
          matrix.cells.length ? h("a", { class: "btn", href: `/api/projects/${pid}/export/summary`,
            onclick: () => toast("Building the summary workbook: every zip and scenario with LGD by TermStep, marginal recoveries and analytics. This takes up to a minute.") }, "Download summary (Excel)") : null,
          h("a", { class: "btn", href: `#/p/${pid}/settings` }, "Members and curves"))),
      h("section", { class: "block" },
        h("header", null, h("h2", null, "Exposure-weighted selected LGD"),
          h("div", { class: "actions" },
            h("span", { class: "muted small" }, "Select a figure to open that zip under that scenario."),
            matrix.datasets.length && matrix.scenarios.length ? pivotBtn : null)),
        matrixNode),
      series.length ? h("section", { class: "block" },
        h("header", null, h("h2", null, "Compare final LGD by TermStep")),
        compareHost) : null,
      h("section", { class: "block" },
        h("header", null, h("h2", null, "Zips")),
        zipTable, canEdit ? h("div", { style: null }, h("div", { class: "small" }, " "), drop, uploads) : null),
      h("section", { class: "block" },
        h("header", null, h("h2", null, "Scenarios"),
          canEdit ? h("button", { type: "button", onclick: newScenario }, "New scenario") : null),
        scTable),
      assistant),
  };
}
