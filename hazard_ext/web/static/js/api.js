// Thin wrapper over fetch for the JSON API.

import { beginBusy } from "./ui.js";

const HEADER = { "X-Requested-With": "hazard-ext" };
let onSignedOut = () => {};

export function setSignedOutHandler(fn) { onSignedOut = fn; }

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function detailText(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => `${(d.loc || []).filter((x) => x !== "body").join(".")}: ${d.msg}`).join("; ");
  }
  return "The request failed";
}

// What the busy panel says while a request that changes something runs.
function busyLabel(method, path) {
  if (path.endsWith("/run-all")) return "Running every scenario on every zip…";
  if (path.endsWith("/run")) return "Running the scenario…";
  if (path.includes("/datasets") && method === "POST") return "Reading the zip…";
  if (path.includes("/curves") && method === "POST") return "Reading the curves…";
  if (path.includes("/agent")) return "Asking the assistant…";
  if (path.includes("/auth/")) return "Signing in…";
  if (method === "DELETE") return "Deleting…";
  return "Saving…";
}

async function request(method, path, { json, form } = {}) {
  const init = { method, headers: { ...HEADER }, credentials: "same-origin" };
  if (json !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(json);
  } else if (form) {
    init.body = form;
  }
  // reads show the top bar only; anything that changes data covers the page until it is done
  const end = beginBusy(method === "GET" ? { block: false } : { block: true, label: busyLabel(method, path) });
  try {
    let resp;
    try {
      resp = await fetch("/api" + path, init);
    } catch {
      throw new ApiError(0, "The server could not be reached");
    }
    if (resp.status === 401 && !path.startsWith("/auth/login")) {
      onSignedOut();
      throw new ApiError(401, "Not signed in");
    }
    let data = null;
    const type = resp.headers.get("content-type") || "";
    if (type.includes("application/json")) data = await resp.json();
    if (!resp.ok) throw new ApiError(resp.status, detailText(data && data.detail));
    return data;
  } finally {
    end();
  }
}

// Fetches a file from the API under the busy panel and hands it to the browser as a download,
// so a workbook that takes a minute to build cannot be requested twice by an impatient click.
export async function download(path, label = "Building the file…") {
  const end = beginBusy({ block: true, label });
  try {
    let resp;
    try {
      resp = await fetch(path, { headers: { ...HEADER }, credentials: "same-origin" });
    } catch {
      throw new ApiError(0, "The server could not be reached");
    }
    if (resp.status === 401) {
      onSignedOut();
      throw new ApiError(401, "Not signed in");
    }
    if (!resp.ok) {
      let detail = null;
      try { detail = (await resp.json()).detail; } catch { /* not JSON */ }
      throw new ApiError(resp.status, detailText(detail));
    }
    const blob = await resp.blob();
    const m = /filename="?([^";]+)"?/.exec(resp.headers.get("content-disposition") || "");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = m ? m[1] : "download";
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 60_000);
  } finally {
    end();
  }
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, json = {}) => request("POST", path, { json }),
  put: (path, json = {}) => request("PUT", path, { json }),
  patch: (path, json = {}) => request("PATCH", path, { json }),
  del: (path) => request("DELETE", path),
  upload: (path, form) => request("POST", path, { form }),
  download,
};
