// Router and page frame.

import { api, setSignedOutHandler } from "./api.js";
import { h, clear, toast, reportError, openDialog, field, fmt, dataTable } from "./ui.js";
import { projectView } from "./project.js";
import { zipView } from "./zip.js";
import { scenarioView } from "./scenario.js";
import { settingsView } from "./settings.js";
import { helpPage } from "./help.js";

const state = { user: null };
const main = document.getElementById("main");
const crumbs = document.getElementById("crumbs");
const userBox = document.getElementById("user");

setSignedOutHandler(() => { state.user = null; renderFrame(); showSignIn(); });

function renderFrame(trail = []) {
  clear(crumbs);
  trail.forEach((c, i) => {
    if (i) crumbs.append(h("span", { class: "sep" }, "/"));
    crumbs.append(c.href ? h("a", { href: c.href }, c.label) : h("span", null, c.label));
  });
  clear(userBox);
  if (!state.user) return;
  userBox.append(h("a", { href: "#/help" }, "Help"));
  if (state.user.is_admin) userBox.append(h("a", { href: "#/admin" }, "Users"));
  userBox.append(
    h("span", null, state.user.name || state.user.email),
    h("button", { class: "link", type: "button", onclick: changePassword }, "Change password"),
    h("button", { class: "link", type: "button", onclick: signOut }, "Sign out"));
}

async function signOut() {
  try { await api.post("/auth/logout"); } catch { /* already signed out */ }
  state.user = null;
  renderFrame();
  showSignIn();
}

function changePassword() {
  const cur = h("input", { type: "password", autocomplete: "current-password" });
  const next = h("input", { type: "password", autocomplete: "new-password" });
  openDialog({
    title: "Change password",
    body: h("div", null, field("Current password", cur), field("New password", next, "At least 10 characters. Other browsers signed in as you are signed out.")),
    actions: [{ label: "Cancel" }, {
      label: "Change password", kind: "primary",
      run: async () => { await api.post("/auth/password", { current: cur.value, new: next.value }); toast("Password changed"); },
    }],
  });
}

// ------------------------------------------------------------------ sign in
function showSignIn() {
  const email = h("input", { type: "email", autocomplete: "username", required: true });
  const pass = h("input", { type: "password", autocomplete: "current-password", required: true });
  const msg = h("p", { class: "notice error", hidden: true });
  const btn = h("button", { class: "primary", type: "submit" }, "Sign in");
  const form = h("form", { class: "block" }, msg, field("Email", email), field("Password", pass), btn);
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    btn.disabled = true;
    msg.hidden = true;
    try {
      state.user = await api.post("/auth/login", { email: email.value, password: pass.value });
      if (!location.hash || location.hash === "#/signin") location.hash = "#/projects";
      route();
    } catch (err) {
      msg.textContent = err.message;
      msg.hidden = false;
    } finally {
      btn.disabled = false;
    }
  });
  clear(main).append(h("div", { class: "signin" },
    h("h1", null, "LGD Tail Extension"),
    h("p", { class: "muted" }, "Extend truncated recovery triangles and compare the LGD under each tail shape, zip by zip."),
    h("section", { class: "block" }, form)));
  email.focus();
}

// ----------------------------------------------------------------- projects
async function projectsView() {
  const projects = await api.get("/projects");
  const create = () => {
    const name = h("input", { type: "text", placeholder: "Nutun July 2026" });
    const desc = h("textarea", { rows: 2 });
    openDialog({
      title: "New project",
      body: h("div", null, field("Name", name), field("Description", desc, "Optional.")),
      actions: [{ label: "Cancel" }, {
        label: "Create project", kind: "primary",
        run: async () => {
          const p = await api.post("/projects", { name: name.value, description: desc.value });
          location.hash = `#/p/${p.id}`;
        },
      }],
    });
  };
  const body = projects.length
    ? dataTable([
        { label: "Project", key: "name", fmt: (v, r) => h("a", { href: `#/p/${r.id}` }, v) },
        { label: "Description", key: "description" },
        { label: "Zips", key: "datasets", num: true },
        { label: "Scenarios", key: "scenarios", num: true },
        { label: "Your role", key: "role" },
        { label: "Created", key: "created_at", fmt: fmt.date },
      ], projects, { plain: true })
    : h("p", { class: "empty" }, "You are not a member of any project yet. Create one, or ask a project owner to add you.");
  return {
    trail: [{ label: "Projects" }],
    node: h("div", null,
      h("div", { class: "pagehead" },
        h("div", null, h("h1", null, "Projects"), h("div", { class: "sub" }, "Each project holds its own zips, scenarios, applied curves and members.")),
        h("div", { class: "actions" }, h("button", { class: "primary", type: "button", onclick: create }, "New project"))),
      h("section", { class: "block" }, body)),
  };
}

// -------------------------------------------------------------------- admin
async function adminView() {
  const users = await api.get("/admin/users");
  const refresh = () => route();
  const create = () => {
    const email = h("input", { type: "email" });
    const name = h("input", { type: "text" });
    const pass = h("input", { type: "text", autocomplete: "off" });
    const admin = h("input", { type: "checkbox" });
    openDialog({
      title: "New user",
      body: h("div", null, field("Email", email), field("Name", name),
        field("Initial password", pass, "At least 10 characters. Give it to the user; they can change it after signing in."),
        h("label", null, admin, " Administrator (can manage users and see every project)")),
      actions: [{ label: "Cancel" }, {
        label: "Create user", kind: "primary",
        run: async () => {
          await api.post("/admin/users", { email: email.value, name: name.value, password: pass.value, is_admin: admin.checked });
          toast("User created");
          refresh();
        },
      }],
    });
  };
  const patch = async (u, body, done) => {
    try { await api.patch(`/admin/users/${u.id}`, body); toast(done); refresh(); } catch (err) { reportError(err); }
  };
  const reset = (u) => {
    const pass = h("input", { type: "text", autocomplete: "off" });
    openDialog({
      title: `Reset password for ${u.email}`,
      body: field("New password", pass, "At least 10 characters. The user is signed out everywhere."),
      actions: [{ label: "Cancel" }, { label: "Reset password", kind: "primary",
        run: async () => { await api.patch(`/admin/users/${u.id}`, { password: pass.value }); toast("Password reset"); } }],
    });
  };
  const table = dataTable([
    { label: "Email", key: "email" },
    { label: "Name", key: "name" },
    { label: "Administrator", key: "is_admin", fmt: (v) => (v ? "Yes" : "No") },
    { label: "Status", key: "is_active", fmt: (v) => h("span", { class: "chip " + (v ? "ok" : "error") }, v ? "Active" : "Deactivated") },
    { label: "Created", key: "created_at", fmt: fmt.date },
    { label: "", key: "id", fmt: (_, u) => h("div", { class: "actions" },
        h("button", { type: "button", onclick: () => reset(u) }, "Reset password"),
        h("button", { type: "button", onclick: () => patch(u, { is_admin: !u.is_admin }, "Updated") }, u.is_admin ? "Remove administrator" : "Make administrator"),
        h("button", { type: "button", class: u.is_active ? "danger" : "", onclick: () => patch(u, { is_active: !u.is_active }, u.is_active ? "Deactivated" : "Reactivated") },
          u.is_active ? "Deactivate" : "Reactivate")) },
  ], users, { plain: true });
  return {
    trail: [{ label: "Projects", href: "#/projects" }, { label: "Users" }],
    node: h("div", null,
      h("div", { class: "pagehead" },
        h("div", null, h("h1", null, "Users"), h("div", { class: "sub" }, "Accounts are created here. Project owners then add users to their projects.")),
        h("div", { class: "actions" }, h("button", { class: "primary", type: "button", onclick: create }, "New user"))),
      h("section", { class: "block" }, table)),
  };
}

// ------------------------------------------------------------------- router
const routes = [
  [/^#\/projects$/, () => projectsView()],
  [/^#\/admin$/, () => adminView()],
  [/^#\/help$/, () => helpPage()],
  [/^#\/p\/(\d+)$/, (m) => projectView(Number(m[1]), ctx)],
  [/^#\/p\/(\d+)\/settings$/, (m) => settingsView(Number(m[1]), ctx)],
  [/^#\/p\/(\d+)\/scenario\/(\d+)$/, (m) => scenarioView(Number(m[1]), Number(m[2]), ctx)],
  [/^#\/p\/(\d+)\/zip\/(\d+)(?:\/(\d+))?$/, (m) => zipView(Number(m[1]), Number(m[2]), m[3] ? Number(m[3]) : null, ctx)],
];
const ctx = { route: () => route(), get user() { return state.user; } };
let navToken = 0;

async function route() {
  if (!state.user) {
    try { state.user = await api.get("/auth/me"); } catch { return; }   // the handler shows sign-in
  }
  const hash = location.hash || "#/projects";
  const token = ++navToken;
  for (const [re, view] of routes) {
    const m = hash.match(re);
    if (!m) continue;
    main.classList.add("busy");
    try {
      const page = await view(m);
      if (token !== navToken) return;                 // a newer navigation won
      renderFrame(page.trail);
      clear(main).append(page.node);
      if (page.after) page.after();
    } catch (err) {
      if (err.status === 401) return;
      renderFrame([{ label: "Projects", href: "#/projects" }]);
      clear(main).append(h("section", { class: "block" },
        h("h2", null, err.status === 404 ? "Not found" : "This page could not be loaded"),
        h("p", null, err.status === 404 ? "It does not exist, or you are not a member of the project." : err.message),
        h("a", { href: "#/projects" }, "Back to projects")));
    } finally {
      main.classList.remove("busy");
    }
    return;
  }
  location.hash = "#/projects";
}

window.addEventListener("hashchange", route);
route();
