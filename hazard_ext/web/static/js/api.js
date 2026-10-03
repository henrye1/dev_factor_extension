// Thin wrapper over fetch for the JSON API.

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

async function request(method, path, { json, form } = {}) {
  const init = { method, headers: { ...HEADER }, credentials: "same-origin" };
  if (json !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(json);
  } else if (form) {
    init.body = form;
  }
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
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, json = {}) => request("POST", path, { json }),
  put: (path, json = {}) => request("PUT", path, { json }),
  patch: (path, json = {}) => request("PATCH", path, { json }),
  del: (path) => request("DELETE", path),
  upload: (path, form) => request("POST", path, { form }),
};
