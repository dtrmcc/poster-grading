// Shows exactly what will be sent to the AI provider, after local de-identification.
function Highlighted({ text }) {
  const parts = text.split(/(\[(?:S\d+|ID|EMAIL|PHONE)\])/g);
  return parts.map((p, i) =>
    /^\[(S\d+|ID|EMAIL|PHONE)\]$/.test(p) ? <mark key={i}>{p}</mark> : <span key={i}>{p}</span>
  );
}

export default function AIReviewDialog({ preview, busy, error, onSend, onCancel }) {
  const system = preview.messages.find((m) => m.role === "system")?.content || "";
  const user = preview.messages.find((m) => m.role === "user")?.content || "";
  // Show the student's (de-identified) material first; the rubric part is the same every time.
  const cut = user.indexOf("=== POSTER TEXT ===");
  const rubric = cut > 0 ? user.slice(0, cut).trim() : "";
  const material = cut > 0 ? user.slice(cut) : user;
  return (
    <div className="modal-backdrop" onClick={busy ? undefined : onCancel}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">
          <h2>Review before sending</h2>
          <span className="muted">
            {preview.provider === "together" ? "Together AI" : preview.provider} · {preview.model}
          </span>
        </div>
        <p className="hint">
          This is the complete request. Names, matric numbers, e-mails and phone numbers were
          replaced on this computer ({preview.replacements} replacement{preview.replacements === 1 ? "" : "s"}, highlighted).
          Nothing has been sent yet.
        </p>
        {preview.warnings.map((w) => <div key={w} className="notice">{w}</div>)}
        {error && <div className="error inline">{error}</div>}
        <pre className="payload"><Highlighted text={material} /></pre>
        {rubric && (
          <details>
            <summary className="muted">Rubric sent with the request</summary>
            <pre className="payload small">{rubric}</pre>
          </details>
        )}
        <details>
          <summary className="muted">Instructions given to the model</summary>
          <pre className="payload small">{system}</pre>
        </details>
        <div className="modal-actions">
          <span className="muted small">Request #{preview.request_id} is kept in the local send log.</span>
          <span className="spacer" />
          <button onClick={onCancel} disabled={busy}>Cancel</button>
          <button className="primary" onClick={onSend} disabled={busy}>
            {busy ? "Waiting for the model…" : "Send to Together"}
          </button>
        </div>
      </div>
    </div>
  );
}
