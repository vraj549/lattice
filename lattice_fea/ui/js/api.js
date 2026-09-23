async function jr(resp) {
  if (!resp.ok) {
    let msg = `${resp.status} ${resp.statusText}`.trim();
    try {
      const d = (await resp.json()).detail;
      // request validation errors arrive as a list of {loc, msg}
      if (Array.isArray(d)) msg = d.map((e) => `${(e.loc || []).join(".")}: ${e.msg}`).join("; ");
      else if (d) msg = String(d);
    } catch { /* not JSON: keep the status line */ }
    throw new Error(msg);
  }
  return resp.json();
}

export const api = {
  get: (u) => fetch(u).then(jr),
  post: (u, body) => fetch(u, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  }).then(jr),
  put: (u, body) => fetch(u, {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(jr),
  del: (u) => fetch(u, { method: "DELETE" }).then(jr),
  upload: (u, fields) => {
    const fd = new FormData();
    for (const [k, v] of Object.entries(fields)) fd.append(k, v);
    return fetch(u, { method: "POST", body: fd }).then(jr);
  },
};
