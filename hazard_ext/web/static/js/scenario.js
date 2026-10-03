// Scenario editor: the parameters, and one override row per zip.

import { api } from "./api.js";
import { h, toast, reportError, openDialog, field, dataTable } from "./ui.js";
import { paramForm, describe } from "./params.js";
import { statusChip } from "./project.js";

export async function scenarioView(pid, sid, ctx) {
  const [project, scenario, matrix] = await Promise.all([
    api.get(`/projects/${pid}`), api.get(`/projects/${pid}/scenarios/${sid}`), api.get(`/projects/${pid}/matrix`)]);
  const canEdit = project.role !== "viewer";
  const eventTypes = [...new Set(project.datasets.flatMap((d) => d.profile.event_types || []))];
  const formCtx = {
    curves: project.curves.map((c) => c.label),
    eventTypes: eventTypes.length ? eventTypes : ["Lifetime", "LifetimeSingle", "TwelveMonthSingle"],
  };
  const cells = new Map(matrix.cells.filter((c) => c.scenario_id === sid).map((c) => [c.dataset_id, c]));

  const name = h("input", { type: "text", value: scenario.name, disabled: !canEdit });
  const desc = h("input", { type: "text", disabled: !canEdit, value: scenario.description });
  const form = paramForm(scenario.params, formCtx, { readOnly: !canEdit });

  const saveBtn = h("button", { type: "button", onclick: () => save(false) }, "Save changes");
  const runBtn = h("button", { type: "button", class: "primary", onclick: () => save(true) }, "Save and run all zips");
  const save = async (thenRun) => {
    let params;
    try { params = form.values(); } catch (err) { return toast(err.message, true); }
    saveBtn.disabled = runBtn.disabled = true;
    const label = runBtn.textContent;
    if (thenRun) runBtn.textContent = "Saving and running…";
    try {
      await api.put(`/projects/${pid}/scenarios/${sid}`, { name: name.value, description: desc.value, params });
      if (thenRun) {
        const out = await api.post(`/projects/${pid}/scenarios/${sid}/run`, {});
        const bad = out.filter((o) => o.status === "error").length;
        toast(`Saved. ${out.length - bad} of ${out.length} zips run` + (bad ? `, ${bad} with errors` : ""), bad > 0);
      } else {
        toast("Scenario saved");
      }
      ctx.route();
    } catch (err) { reportError(err); saveBtn.disabled = runBtn.disabled = false; runBtn.textContent = label; }
  };

  const editOverride = (d) => {
    const current = scenario.overrides[String(d.id)] || {};
    const of = paramForm(scenario.params, formCtx, { override: current });
    openDialog({
      title: `Override for ${d.name}`, wide: true,
      body: h("div", null,
        h("p", { class: "muted" }, "Tick a parameter to give this zip its own value. Unticked parameters follow the scenario."),
        of.node),
      actions: [{ label: "Cancel" }, {
        label: "Save override", kind: "primary",
        run: async () => {
          let params;
          try { params = of.values(); } catch (err) { toast(err.message, true); return true; }
          await api.put(`/projects/${pid}/scenarios/${sid}/overrides/${d.id}`, { params });
          toast(Object.keys(params).length ? "Override saved" : "Override removed");
          ctx.route();
        },
      }],
    });
  };
  const runZip = async (d, btn) => {
    btn.disabled = true;
    try {
      const [out] = await api.post(`/projects/${pid}/scenarios/${sid}/run`, { dataset_id: d.id });
      toast(out.status === "ok" ? `${d.name} run` : `${d.name}: ${out.error}`, out.status !== "ok");
      ctx.route();
    } catch (err) { reportError(err); btn.disabled = false; }
  };

  const overrides = project.datasets.length ? dataTable([
    { label: "Zip", key: "name", fmt: (v, d) => h("a", { href: `#/p/${pid}/zip/${d.id}/${sid}` }, v) },
    { label: "Category", key: "category" },
    { label: "Own values for this zip", key: "id", fmt: (id) => {
        const o = scenario.overrides[String(id)];
        if (!o || !Object.keys(o).length) return h("span", { class: "muted" }, "Follows the scenario");
        return h("div", { class: "ovr" }, Object.entries(o).map(([k, v]) => h("span", { class: "kv" }, describe(k, v))));
      } },
    { label: "Last run", key: "id", fmt: (id) => {
        const c = cells.get(id) || { status: "none" };
        const chip = statusChip(c);
        if (c.status === "ok") return h("span", null, `LGD ${c.summary.lgd_selected.toFixed(4)} `, chip);
        return h("span", null, chip, c.status === "error" ? " " + c.error : "");
      } },
    ...(canEdit ? [{ label: "", key: "id", fmt: (_, d) => {
        const run = h("button", { type: "button" }, "Run");
        run.addEventListener("click", () => runZip(d, run));
        return h("div", { class: "actions" }, h("button", { type: "button", onclick: () => editOverride(d) }, "Edit override"), run);
      } }] : []),
  ], project.datasets, { plain: true, cls: "overrides" }) : h("p", { class: "empty" }, "Upload zips on the project page to give them their own values here.");

  return {
    trail: [{ label: "Projects", href: "#/projects" }, { label: project.name, href: `#/p/${pid}` }, { label: scenario.name }],
    node: h("div", null,
      h("div", { class: "pagehead" },
        h("div", null, h("h1", null, scenario.name),
          h("div", { class: "sub" }, "These parameters apply to every zip in the project, except where a zip has its own value below.")),
        canEdit ? h("div", { class: "actions" }, saveBtn, runBtn) : null),
      h("section", { class: "block" },
        h("div", { class: "row" }, field("Name", name), field("Description", desc)),
        h("div", { class: "small" }, " "),
        form.node),
      h("section", { class: "block" },
        h("header", null, h("h2", null, "Per-zip overrides")),
        overrides)),
  };
}
