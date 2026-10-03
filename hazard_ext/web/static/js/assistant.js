// The assistant panel: type an instruction, get a reply, confirm any proposed change.

import { api } from "./api.js";
import { h, clear, toast, reportError } from "./ui.js";
import { describe } from "./params.js";

let enabled = null;

export async function assistantEnabled() {
  if (enabled === null) {
    try { enabled = (await api.get("/features")).assistant === true; } catch { enabled = false; }
  }
  return enabled;
}

// Mounts a collapsible panel. `view` describes what the user is looking at; `onApplied` reloads the page.
export function assistantPanel({ pid, view, canApply, onApplied }) {
  const history = [];                       // {role, content} text turns sent back each time
  const log = h("div", { class: "asst-log" });
  const input = h("textarea", { rows: 2, placeholder: "For example: set MaxBucket to 60 on the Exponential scenario, or: what is the LGD for VB44 at TermStep 60?" });
  const send = h("button", { type: "button", class: "primary" }, "Send");
  const panel = h("div", { class: "asst", hidden: true });
  const toggle = h("button", { type: "button", class: "asst-toggle" }, "Assistant");
  toggle.addEventListener("click", () => { panel.hidden = !panel.hidden; if (!panel.hidden) input.focus(); });

  const bubble = (role, text) => {
    const el = h("div", { class: "asst-msg " + role });
    text.split(/\n{2,}/).forEach((para) => el.append(h("p", null, para)));
    return el;
  };
  const scroll = () => { log.scrollTop = log.scrollHeight; };

  const proposalCard = (p, logId) => {
    const rows = p.diff.map((d) => {
      const before = describe(d.param, d.old), after = describe(d.param, d.new);
      const i = before.indexOf(": ");
      return h("tr", null, h("td", null, before.slice(0, i)), h("td", { class: "num" }, before.slice(i + 2)), h("td", { class: "num" }, after.slice(after.indexOf(": ") + 2)));
    });
    const confirm = h("button", { type: "button", class: "primary" }, p.run_after ? "Confirm and run" : "Confirm");
    const cancel = h("button", { type: "button" }, "Cancel");
    const card = h("div", { class: "asst-proposal" },
      h("div", { class: "asst-ptitle" }, p.scope === "zip" ? `Override for ${p.zip} under ${p.scenario}` : `Scenario ${p.scenario}, all zips`),
      p.reason ? h("div", { class: "muted small" }, p.reason) : null,
      h("table", null, h("thead", null, h("tr", null, h("th", null, "Assumption"), h("th", { class: "num" }, "Now"), h("th", { class: "num" }, "Proposed"))), h("tbody", null, rows)),
      h("div", { class: "actions" }, confirm, cancel));
    cancel.addEventListener("click", () => { card.replaceWith(bubble("sys", "Cancelled; nothing was changed.")); });
    confirm.addEventListener("click", async () => {
      confirm.disabled = cancel.disabled = true;
      confirm.textContent = "Applying…";
      try {
        if (p.scope === "zip") {
          const s = await api.get(`/projects/${pid}/scenarios/${p.scenario_id}`);
          const current = s.overrides[String(p.zip_id)] || {};
          await api.put(`/projects/${pid}/scenarios/${p.scenario_id}/overrides/${p.zip_id}`, { params: { ...current, ...p.changes } });
        } else {
          const s = await api.get(`/projects/${pid}/scenarios/${p.scenario_id}`);
          await api.put(`/projects/${pid}/scenarios/${p.scenario_id}`, { params: { ...s.params, ...p.changes } });
        }
        let note = "Applied.";
        if (p.run_after) {
          const out = await api.post(`/projects/${pid}/scenarios/${p.scenario_id}/run`, p.scope === "zip" ? { dataset_id: p.zip_id } : {});
          const bad = out.filter((o) => o.status === "error");
          note = bad.length ? `Applied and run; ${bad.length} zip(s) failed: ${bad[0].error}` : `Applied and run (${out.length} zip${out.length === 1 ? "" : "s"}).`;
          const ok = out.filter((o) => o.status === "ok");
          if (ok.length) note += " Selected LGD: " + ok.map((o) => `${o.dataset} ${o.summary.lgd_selected.toFixed(4)}`).join(", ") + ".";
        }
        await api.post(`/projects/${pid}/agent/${logId}/applied`);
        history.push({ role: "user", content: `(The user confirmed the proposal: ${JSON.stringify(p.changes)} on ${p.scenario}${p.zip ? " for " + p.zip : ""}.)` });
        history.push({ role: "assistant", content: note });
        card.replaceWith(bubble("sys", note));
        toast(note);
        onApplied && onApplied();
      } catch (err) {
        reportError(err);
        confirm.disabled = cancel.disabled = false;
        confirm.textContent = p.run_after ? "Confirm and run" : "Confirm";
      }
    });
    return card;
  };

  const ask = async () => {
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    history.push({ role: "user", content: text });
    log.append(bubble("user", text));
    const thinking = bubble("asst", "…");
    log.append(thinking); scroll();
    send.disabled = true;
    try {
      const out = await api.post(`/projects/${pid}/agent`, { history: history.slice(-20), view });
      history.push({ role: "assistant", content: out.reply });
      thinking.replaceWith(bubble("asst", out.reply));
      for (const p of out.proposals || []) {
        if (canApply && out.can_apply) log.append(proposalCard(p, out.log_id));
      }
    } catch (err) {
      thinking.replaceWith(bubble("sys", err.message));
    } finally {
      send.disabled = false; scroll(); input.focus();
    }
  };
  send.addEventListener("click", ask);
  input.addEventListener("keydown", (ev) => { if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); ask(); } });

  panel.append(
    h("div", { class: "asst-head" }, h("b", null, "Assistant"),
      h("span", { class: "muted small" }, "Changes are proposed first and applied only when you confirm."),
      h("button", { type: "button", class: "link", onclick: () => { panel.hidden = true; } }, "Close")),
    log, h("div", { class: "asst-input" }, input, send));
  return h("div", { class: "asst-wrap" }, toggle, panel);
}
