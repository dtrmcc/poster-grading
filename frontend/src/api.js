export async function api(path, opts = {}) {
  const isForm = opts.body instanceof FormData;
  const r = await fetch(`/api${path}`, {
    ...opts,
    headers: isForm ? opts.headers : { "Content-Type": "application/json", ...opts.headers },
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : r.statusText);
  }
  return r.json();
}

export const STATUS_LABEL = {
  not_started: "Not started",
  in_progress: "In progress",
  marked: "Marked",
};

export const STATUS_DOT = { not_started: "○", in_progress: "◐", marked: "●" };
