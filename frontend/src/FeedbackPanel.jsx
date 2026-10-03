import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api.js";

const SAVE_DELAY = 700;

// Feedback for one student: AI draft → instructor edits → export PDF (versioned archive).
export default function FeedbackPanel({ student, marksComplete, aiAvailable, aiBusy, reloadKey,
  onRequestDraft, onStatus }) {
  const [fb, setFb] = useState(null);
  const [draft, setDraft] = useState({ poster: "", presentation: "" });
  const [saveState, setSaveState] = useState("saved");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const timer = useRef(null);
  const pending = useRef(null);

  const flush = useCallback(async () => {
    clearTimeout(timer.current);
    const job = pending.current;
    if (!job) return;
    pending.current = null;
    setSaveState("saving");
    try {
      const f = await api(`/students/${job.id}/feedback`, { method: "PUT", body: JSON.stringify(job.body) });
      setFb((cur) => (cur && job.id === student.id ? { ...f } : cur));
      onStatus(job.id, f.status);
      setSaveState(pending.current ? "pending" : "saved");
    } catch (e) { setSaveState("error"); setError(e.message); }
  }, [student.id, onStatus]);

  useEffect(() => {
    let cancelled = false;
    setError("");
    api(`/students/${student.id}/feedback`).then((f) => {
      if (cancelled) return;
      setFb(f);
      setDraft({ poster: f.poster, presentation: f.presentation });
    }).catch((e) => setError(e.message));
    return () => { cancelled = true; flush(); };
  }, [student.id, reloadKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const edit = (patch) => {
    const next = { ...draft, ...patch };
    setDraft(next);
    pending.current = { id: student.id, body: next };
    setSaveState("pending");
    clearTimeout(timer.current);
    timer.current = setTimeout(flush, SAVE_DELAY);
  };

  const exportPdf = async () => {
    setBusy(true); setError("");
    try {
      await flush();
      const f = await api(`/students/${student.id}/export`, { method: "POST" });
      setFb(f);
      onStatus(student.id, f.status);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  if (!fb) return <p className="muted">Loading…</p>;
  const hasText = draft.poster.trim() || draft.presentation.trim();
  const sections = [
    ["poster", "Feedback on your poster", fb.ai_poster],
    ["presentation", "Feedback on your presentation", fb.ai_presentation],
  ];

  return (
    <div className="feedback">
      <div className="feedback-bar">
        <button className="ai" onClick={async () => { await flush(); onRequestDraft(); }}
          disabled={!marksComplete || !aiAvailable || aiBusy}
          title={!marksComplete ? "Enter marks for every criterion first"
            : !aiAvailable ? "Add the Together API key and model to .env first"
            : "Draft both sections with AI (you review the request first)"}>
          {aiBusy ? "Preparing…" : fb.ai_poster != null ? "✦ Draft again" : "✦ Draft with AI"}
        </button>
        <span className={`save ${saveState}`}>
          {{ saved: "Saved", pending: "Unsaved…", saving: "Saving…", error: "Not saved" }[saveState]}
        </span>
        <span className="spacer" />
        <button className="primary" onClick={exportPdf} disabled={busy || !marksComplete || !hasText}
          title={!marksComplete ? "Enter marks for every criterion first" : !hasText ? "Write the feedback first" : ""}>
          {busy ? "Exporting…" : fb.exports.length ? "Export new PDF version" : "Approve & export PDF"}
        </button>
      </div>
      {!marksComplete && <div className="notice">Enter marks for every criterion before drafting or exporting feedback.</div>}
      {error && <div className="error inline" onClick={() => setError("")}>{error}</div>}

      {sections.map(([key, title, ai]) => (
        <label className="fb-section" key={key}>
          <span className="fb-title">
            {title}
            {ai != null && ai !== draft[key] && (
              <button className="link" onClick={(e) => { e.preventDefault(); edit({ [key]: ai }); }}
                title="Replace this section with the latest AI draft">
                Use latest AI draft
              </button>
            )}
          </span>
          <textarea value={draft[key]} onChange={(e) => edit({ [key]: e.target.value })}
            placeholder={key === "poster" ? "Feedback on the poster (drafted from your comments)…"
              : "Feedback on the presentation (drafted from your presentation note)…"} />
        </label>
      ))}

      <div className="exports">
        <span className="fb-title">Exported PDFs</span>
        {fb.exports.length === 0 ? (
          <span className="muted small">None yet. “Approve & export PDF” saves a copy in the archive.</span>
        ) : (
          <ul>
            {fb.exports.map((e) => (
              <li key={e.id}>
                v{e.version} · {e.letter} · {new Date(e.created_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}
                {" — "}<a href={e.url} target="_blank" rel="noreferrer">Open</a>
                {" · "}<a href={`${e.url}?download=true`}>Download</a>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
