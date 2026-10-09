// Small DOM helpers. Everything is built with textContent / attributes, never innerHTML,
// so names coming from uploads and the API cannot inject markup.

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "checked" || k === "disabled" || k === "selected" || k === "hidden") el[k] = Boolean(v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    if (Array.isArray(c)) append(el, c);
    else el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

// ------------------------------------------------------------------ formats
const nf0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
export const blank = "–";
export const fmt = {
  lgd: (v) => (v === null || v === undefined ? blank : (Math.abs(v) < 5e-5 ? 0 : v).toFixed(4)),
  lgd3: (v) => (v === null || v === undefined ? blank : v.toFixed(3)),
  signed: (v) => (v === null || v === undefined ? blank : Math.abs(v) < 5e-5 ? "0.0000" : (v > 0 ? "+" : "−") + Math.abs(v).toFixed(4)),
  int: (v) => (v === null || v === undefined ? blank : nf0.format(v)),
  money: (v) => (v === null || v === undefined ? blank : nf0.format(v)),
  moneyShort: (v) => {
    if (v === null || v === undefined) return blank;
    if (Math.abs(v) >= 1e9) return "R" + (v / 1e9).toFixed(2) + "bn";
    if (Math.abs(v) >= 1e6) return "R" + (v / 1e6).toFixed(1) + "m";
    return "R" + nf0.format(v);
  },
  pct: (v, d = 2) => (v === null || v === undefined ? blank : (v * 100).toFixed(d) + "%"),
  num: (v, d = 4) => (v === null || v === undefined ? blank : v.toFixed(d)),
  sci: (v) => (v === null || v === undefined ? blank : Math.abs(v) < 5e-10 ? "0.0000" : v.toExponential(1)),
  date: (iso) => {
    if (!iso) return blank;
    const d = new Date(iso);
    return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) +
      " " + d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  },
};

// -------------------------------------------------------------------- toast
export function toast(message, bad = false) {
  const box = document.getElementById("toast");
  const item = h("div", { class: bad ? "bad" : "", role: bad ? "alert" : "status" }, message);
  box.append(item);
  setTimeout(() => item.remove(), bad ? 9000 : 4000);
}

export function reportError(err) {
  if (err && err.status === 401) return;         // the router shows the sign-in page
  toast(err && err.message ? err.message : String(err), true);
}

// ------------------------------------------------------------------ dialogs
export function openDialog({ title, body, actions, wide = false }) {
  const dlg = h("dialog", { class: wide ? "wide" : "" });
  const foot = h("div", { class: "foot" });
  const close = () => { dlg.close(); dlg.remove(); };
  for (const a of actions) {
    const btn = h("button", { class: a.kind || "", type: "button" }, a.label);
    btn.addEventListener("click", async () => {
      if (!a.run) return close();
      btn.disabled = true;
      try {
        const keepOpen = await a.run();
        if (keepOpen !== true) close();
      } catch (err) {
        reportError(err);
      } finally {
        btn.disabled = false;
      }
    });
    foot.append(btn);
  }
  dlg.append(h("h2", null, title), body, foot);
  dlg.addEventListener("cancel", () => dlg.remove());
  document.body.append(dlg);
  dlg.showModal();
  return { close, dlg };
}

export function confirmDialog(title, text, label, run, danger = true) {
  return openDialog({
    title, body: h("p", null, text),
    actions: [{ label: "Cancel" }, { label, kind: danger ? "danger" : "primary", run }],
  });
}

export function field(label, control, hint) {
  const id = "f" + Math.random().toString(36).slice(2, 9);
  control.id = id;
  return h("div", { class: "field" }, h("label", { for: id }, label), control, hint ? h("div", { class: "hint" }, hint) : null);
}

// --------------------------------------------------------------------- tabs
export function tabs(items) {
  // items: [{label, render: () => Node}]
  const bar = h("div", { class: "tabs", role: "tablist" });
  const pane = h("div", { role: "tabpanel" });
  const select = (i) => {
    [...bar.children].forEach((b, j) => b.setAttribute("aria-selected", String(i === j)));
    clear(pane).append(items[i].render());
  };
  items.forEach((it, i) => bar.append(h("button", { type: "button", role: "tab", onclick: () => select(i) }, it.label)));
  select(0);
  return h("div", null, bar, pane);
}

// -------------------------------------------------------------------- table
export function dataTable(columns, rows, opts = {}) {
  // columns: [{label, key, fmt, num, cls}] ; rows: array of objects
  const thead = h("thead", null, h("tr", null, columns.map((c) => h("th", { class: c.num ? "num" : "" }, c.label))));
  const tbody = h("tbody");
  for (const r of rows) {
    const tr = h("tr", { class: opts.rowClass ? opts.rowClass(r) : "" });
    for (const c of columns) {
      const raw = r[c.key];
      const text = c.fmt ? c.fmt(raw, r) : raw === null || raw === undefined ? blank : raw;
      tr.append(h("td", { class: [c.num ? "num" : "", c.cls || ""].join(" ").trim() }, text));
    }
    tbody.append(tr);
  }
  return h("div", { class: "tablewrap" + (opts.plain ? " plain" : "") }, h("table", { class: opts.cls || "" }, thead, tbody));
}

export function columnsToRows(table) {
  // {a: [..], b: [..]} -> [{a, b}, ...]
  const keys = Object.keys(table);
  const n = table[keys[0]].length;
  const rows = new Array(n);
  for (let i = 0; i < n; i++) {
    const r = {};
    for (const k of keys) r[k] = table[k][i];
    rows[i] = r;
  }
  return rows;
}

// --------------------------------------------------------------------- busy
// One indicator for the whole app. `beginBusy` returns a function that ends that piece of
// work; the indicator stays while any piece is running. Blocking work (anything that changes
// data, a download, a page load) covers the page and swallows clicks straight away, and shows
// its panel after a short delay so a quick save does not flash. Reads only show the top bar.
const busyState = { count: 0, blocking: 0, labels: [], timer: null };

function paintBusy() {
  const el = document.getElementById("busy");
  if (!el) return;
  const on = busyState.count > 0;
  el.classList.toggle("on", on);
  el.classList.toggle("quiet", on && busyState.blocking === 0);
  const label = busyState.labels[busyState.labels.length - 1] || "Working…";
  el.querySelector(".label").textContent = label;
  if (!on) {
    el.classList.remove("show");
    clearTimeout(busyState.timer);
    busyState.timer = null;
  } else if (busyState.blocking > 0 && !busyState.timer && !el.classList.contains("show")) {
    busyState.timer = setTimeout(() => { busyState.timer = null; if (busyState.blocking > 0) el.classList.add("show"); }, 250);
  }
}

export function beginBusy({ block = true, label = "" } = {}) {
  busyState.count += 1;
  if (block) {
    busyState.blocking += 1;
    if (label) busyState.labels.push(label);
  }
  paintBusy();
  let done = false;
  return () => {
    if (done) return;
    done = true;
    busyState.count -= 1;
    if (block) {
      busyState.blocking -= 1;
      if (label) busyState.labels.splice(busyState.labels.lastIndexOf(label), 1);
    }
    paintBusy();
  };
}

// Runs `fn` under the blocking indicator and returns its result.
export async function withBusy(label, fn) {
  const end = beginBusy({ block: true, label });
  try { return await fn(); } finally { end(); }
}
