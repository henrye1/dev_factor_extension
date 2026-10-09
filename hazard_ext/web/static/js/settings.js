// Project settings: details, members and the client's applied curves.

import { api } from "./api.js";
import { h, clear, toast, reportError, confirmDialog, field, dataTable } from "./ui.js";
import { lineChart, COLORS } from "./chart.js";

export async function settingsView(pid, ctx) {
  const [project, members] = await Promise.all([api.get(`/projects/${pid}`), api.get(`/projects/${pid}/members`)]);
  const isOwner = project.role === "owner";
  const canEdit = project.role !== "viewer";
  const refresh = ctx.route;

  // ---------------------------------------------------------------- details
  const name = h("input", { type: "text", value: project.name, disabled: !isOwner });
  const desc = h("input", { type: "text", disabled: !isOwner, value: project.description });
  const saveDetails = async () => {
    try { await api.patch(`/projects/${pid}`, { name: name.value, description: desc.value }); toast("Project saved"); refresh(); }
    catch (err) { reportError(err); }
  };
  const removeProject = () => confirmDialog(`Delete ${project.name}?`,
    "Every zip, scenario, curve and result in the project is deleted. This cannot be undone.",
    "Delete project", async () => { await api.del(`/projects/${pid}`); toast("Project deleted"); location.hash = "#/projects"; });

  // ---------------------------------------------------------------- members
  const email = h("input", { type: "email", placeholder: "colleague@example.com" });
  const role = h("select", null, h("option", { value: "viewer" }, "Viewer: read and export"),
    h("option", { value: "editor" }, "Editor: upload, edit and run"), h("option", { value: "owner" }, "Owner: also manage members"));
  const addMember = async () => {
    try { await api.put(`/projects/${pid}/members`, { email: email.value, role: role.value }); toast("Member added"); refresh(); }
    catch (err) { reportError(err); }
  };
  const setRole = async (m, value) => {
    try { await api.put(`/projects/${pid}/members`, { email: m.email, role: value }); toast("Role changed"); refresh(); }
    catch (err) { reportError(err); refresh(); }
  };
  const removeMember = (m) => confirmDialog(`Remove ${m.email}?`, "They lose access to this project.", "Remove member",
    async () => { await api.del(`/projects/${pid}/members/${m.user_id}`); toast("Member removed"); refresh(); });
  const memberTable = dataTable([
    { label: "Email", key: "email" },
    { label: "Name", key: "name" },
    { label: "Role", key: "role", fmt: (v, m) => {
        if (!isOwner) return v;
        const sel = h("select", { "aria-label": `Role of ${m.email}` },
          ["viewer", "editor", "owner"].map((r) => h("option", { value: r, selected: r === v }, r)));
        sel.style.width = "auto";
        sel.addEventListener("change", () => setRole(m, sel.value));
        return sel;
      } },
    ...(isOwner ? [{ label: "", key: "user_id", fmt: (_, m) => h("button", { type: "button", class: "danger", onclick: () => removeMember(m) }, "Remove") }] : []),
  ], members, { plain: true });

  // ------------------------------------------------- client applied curves
  const appliedCurves = project.curves.filter((c) => c.kind === "applied");
  const appliedFile = h("input", { type: "file", accept: ".csv,.xlsx" });
  const basisSel = h("select", null,
    h("option", { value: "face" }, "Share of the balance at default (face value)"),
    h("option", { value: "outstanding" }, "Share of the balance still outstanding each month"));
  const uploadApplied = async () => {
    if (!appliedFile.files.length) return toast("Choose a .csv or .xlsx file first", true);
    const form = new FormData();
    form.append("file", appliedFile.files[0]);
    form.append("kind", "applied");
    form.append("basis", basisSel.value);
    try { await api.upload(`/projects/${pid}/curves`, form); toast("Applied curves uploaded"); refresh(); }
    catch (err) { reportError(err); }
  };
  const removeApplied = (c) => confirmDialog(`Remove the applied curve ${c.label}?`,
    "It disappears from the comparison charts. Results are not affected.",
    "Remove curve", async () => { await api.del(`/projects/${pid}/curves/${c.id}`); toast("Curve removed"); refresh(); });
  const appliedTable = appliedCurves.length ? dataTable([
    { label: "Label (matches the zip's category)", key: "label" },
    { label: "Uploaded as", key: "basis", fmt: (v) => (v === "outstanding" ? "Share of outstanding balance" : "Share of face value") },
    { label: "File", key: "source_filename" },
    { label: "Months", key: "length", num: true },
    { label: "Total recovery on face", key: "total", num: true, fmt: (v) => (v * 100).toFixed(1) + "%" },
    ...(canEdit ? [{ label: "", key: "id", fmt: (_, c) => h("button", { type: "button", class: "danger", onclick: () => removeApplied(c) }, "Remove") }] : []),
  ], appliedCurves, { plain: true }) : h("p", { class: "empty" }, "No applied curves yet. They are drawn on the results charts as a dashed comparison and never change the calculation.");
  const appliedChart = h("div");

  const drawCurves = async () => {
    try {
      if (appliedCurves.length) {
        const all = await Promise.all(appliedCurves.slice(0, COLORS.scenario.length).map((c) =>
          api.get(`/projects/${pid}/curves/${encodeURIComponent(c.label)}?kind=applied`)));
        lineChart(appliedChart, {
          title: "Client applied recovery curves: monthly cash as a share of face value",
          subtitle: "Log scale. Curves uploaded on the outstanding basis are shown converted to the face basis.",
          xLabel: "Month since default", yLog: true,
          series: all.map((c, i) => ({ name: c.label, color: COLORS.scenario[i], pts: c.values.map((v, t) => [t + 1, v]) })),
        });
      }
    } catch (err) { reportError(err); }
  };

  // ---------------------------------- compare the client's curves with our fitted tails
  const cmpHost = h("div");
  const cmpTable = h("div");
  const zipSel = h("select", null, project.datasets.map((d) => h("option", { value: d.id }, d.name + (d.category ? ` (${d.category})` : ""))));
  const scnSel = h("select", null, project.scenarios.map((s) => h("option", { value: s.id }, s.name)));
  const cmpBasis = h("select", null,
    h("option", { value: "face" }, "Share of the balance at the TermStep (face value)"),
    h("option", { value: "outstanding" }, "Share of the balance still outstanding each step"));
  const tsInput = h("input", { type: "number", min: 1, value: 1, "aria-label": "TermStep" });
  for (const el of [zipSel, scnSel, cmpBasis]) el.style.width = "auto";
  tsInput.style.width = "5rem";
  let cmpToken = 0;
  const drawComparison = async () => {
    if (!project.datasets.length || !project.scenarios.length) return;
    const token = ++cmpToken;
    const did = Number(zipSel.value), sid = Number(scnSel.value);
    const ts = Math.max(1, Math.round(Number(tsInput.value) || 1));
    tsInput.value = ts;
    try {
      const c = await api.get(`/projects/${pid}/results/${sid}/${did}/compare_curves?ts=${ts}&basis=${cmpBasis.value}`);
      if (token !== cmpToken) return;
      const out = cmpBasis.value === "outstanding";
      const pts = (ys) => c.bucket.map((b, i) => [b, ys[i]]);
      const S = c.series;
      const name = (k, label) => label + (k === c.selected ? " (selected)" : "");
      lineChart(cmpHost, {
        title: `TermStep ${ts}: the client's applied curve against our fitted tails` + (c.vintages ? ` (vintages from ${c.vintages})` : ""),
        subtitle: (out ? "Each step's cash as a share of what is still outstanding at the start of that step. "
                       : "Each step's cash as a share of the balance at the TermStep. ") +
          (c.applied_label ? `Client curve ${c.applied_label}, uploaded on the ${c.applied_basis === "outstanding" ? "outstanding" : "face value"} basis.`
                           : "No client applied curve for this cohort: upload one above."),
        xLabel: "Bucket", yLog: true,
        refs: [{ x: c.last_cred, label: "Last credible" }],
        series: [
          { name: "Observed", color: COLORS.final, dots: true, pts: pts(S.observed) },
          { name: name("exp", "Exponential"), color: COLORS.exp, pts: pts(S.exp) },
          { name: name("power", "Power law"), color: COLORS.power, pts: pts(S.power) },
          ...(S.logn ? [{ name: name("logn", "Log-normal"), color: COLORS.logn, pts: pts(S.logn) }] : []),
          ...(S.applied ? [{ name: `Client applied (${c.applied_label})`, color: "#4a3aa7", dash: true, width: 2.5, pts: pts(S.applied) }] : []),
        ],
      });
      clear(cmpTable).append(
        h("p", { class: "muted small" }, `Cumulative recovery from TermStep ${ts} as a share of the balance at that TermStep: ` +
          `our ${c.method_label.toLowerCase()} against the client's applied curve. Positive difference = we recover more.`),
        dataTable([
          { label: "Within months", key: "months", num: true },
          { label: `Ours (${c.method_label})`, key: "ours", num: true, fmt: (v) => fmtPct(v) },
          { label: "Client applied", key: "client", num: true, fmt: (v) => fmtPct(v) },
          { label: "Difference", key: "difference", num: true, fmt: (v) => (v === null ? "–" : (v >= 0 ? "+" : "−") + (Math.abs(v) * 100).toFixed(2) + "%") },
        ], c.cumulative, { plain: true }));
    } catch (err) {
      if (err.status === 404) {
        clear(cmpHost).classList.add("chart");
        cmpHost.append(h("h3", null, "Compare with the client's curve"), h("p", { class: "empty" }, "This scenario has not been run for this zip yet. Run it from the project page first."));
        clear(cmpTable);
      } else reportError(err);
    }
  };
  const fmtPct = (v) => (v === null || v === undefined ? "–" : (v * 100).toFixed(2) + "%");
  for (const el of [zipSel, scnSel, cmpBasis]) el.addEventListener("change", drawComparison);
  tsInput.addEventListener("change", drawComparison);
  const comparison = project.datasets.length && project.scenarios.length ? h("section", { class: "block" },
    h("header", null, h("h2", null, "Compare the client's curves with our fitted tails")),
    h("p", { class: "muted" }, "Pick a cohort, scenario and TermStep. The chart puts the observed data, our three fitted tails and " +
      "the client's applied curve on one basis. Switch the basis to see every curve the way the client applies it: as a share " +
      "of the balance at the TermStep, or as a share of what is still outstanding at the start of each step."),
    h("div", { class: "actions comparectl" },
      h("span", { class: "muted small" }, "Cohort"), zipSel, h("span", { class: "muted small" }, "Scenario"), scnSel,
      h("span", { class: "muted small" }, "TermStep"), tsInput, h("span", { class: "muted small" }, "Basis"), cmpBasis),
    cmpHost, h("div", { class: "small" }, "\u00a0"), cmpTable) : null;

  // ------------------------------------------------------- curves download
  const dlScn = h("select", null, project.scenarios.map((s) => h("option", { value: s.id }, s.name)));
  dlScn.style.width = "auto";
  const dlLink = h("a", { class: "btn", href: "#" }, "Download LGD and recovery curves (Excel)");
  const setLink = () => { dlLink.href = `/api/projects/${pid}/export/curves?scenario_id=${dlScn.value}`; };
  dlScn.addEventListener("change", setLink);
  if (project.scenarios.length) setLink();
  dlLink.addEventListener("click", (ev) => {
    ev.preventDefault();
    if (!project.scenarios.length) return;
    api.download(dlLink.href, "Building the curves workbook for every cohort. This takes up to half a minute…").catch(reportError);
  });
  const downloads = project.scenarios.length ? h("section", { class: "block" },
    h("header", null, h("h2", null, "Download curves")),
    h("p", { class: "muted" }, "One workbook per scenario: final LGD by TermStep for every cohort, each cohort's marginal recovery " +
      "curve on the face-value and outstanding-balance bases next to the client's applied curve, " +
      "cumulative recovery, and a sheet per cohort of marginal recoveries by TermStep for the first 120 remaining steps."),
    h("div", { class: "actions" }, h("span", { class: "muted small" }, "Scenario"), dlScn, dlLink)) : null;

  return {
    trail: [{ label: "Projects", href: "#/projects" }, { label: project.name, href: `#/p/${pid}` }, { label: "Members and curves" }],
    after: () => { drawCurves(); drawComparison(); },
    node: h("div", null,
      h("div", { class: "pagehead" }, h("div", null, h("h1", null, "Members and curves"),
        h("div", { class: "sub" }, `${project.name}. Your role: ${project.role}.`))),
      h("section", { class: "block" },
        h("header", null, h("h2", null, "Client applied recovery curves")),
        h("p", { class: "muted" }, "The curves the client actually applies, for comparison only. Each zip is matched to the curve whose " +
          "label equals its category, and the results charts show it as a dashed line: the monthly rate rolled forward to each " +
          "TermStep, and the LGD it implies at the file's discount rate. Uploading or removing one never changes a result. " +
          "The three tail methods (exponential, power law, log-normal) are fitted to each zip's own data and take no curve."),
        appliedTable, h("div", { class: "small" }, "\u00a0"), appliedChart,
        canEdit ? h("div", null, h("div", { class: "small" }, "\u00a0"),
          h("div", { class: "row" },
            field("Upload applied curves", appliedFile, "A .csv or .xlsx: first column t = 1, 2, 3 and so on, then one column per cohort label (11, 15, 22, 23, 25, 44, ALL). A label that is already uploaded is replaced."),
            field("The monthly rates are a", basisSel, "Face value: each month's cash as a share of the balance at default. Outstanding: a share of what is still owed at the start of that month."),
            h("button", { type: "button", onclick: uploadApplied }, "Upload applied curves"))) : null),
      comparison,
      downloads,
      h("section", { class: "block" },
        h("header", null, h("h2", null, "Members")),
        memberTable,
        isOwner ? h("div", null, h("div", { class: "small" }, " "),
          h("div", { class: "row" }, field("Add a member by email", email, "The account must already exist. An administrator creates accounts."),
            field("Role", role), h("button", { type: "button", class: "primary", onclick: addMember }, "Add member"))) : null),
      isOwner ? h("section", { class: "block" },
        h("header", null, h("h2", null, "Project details")),
        h("div", { class: "row" }, field("Name", name), field("Description", desc)),
        h("div", { class: "small" }, " "),
        h("div", { class: "actions" },
          h("button", { type: "button", class: "primary", onclick: saveDetails }, "Save project"),
          h("button", { type: "button", class: "danger", onclick: removeProject }, "Delete project"))) : null),
  };
}
