import RedactionEditor from "./RedactionEditor.jsx";

// Shows exactly what will be sent to the AI provider(s), after local de-identification.
function Highlighted({ text }) {
  const parts = text.split(/(\[(?:S\d+|ID|EMAIL|PHONE)\])/g);
  return parts.map((p, i) =>
    /^\[(S\d+|ID|EMAIL|PHONE)\]$/.test(p) ? <mark key={i}>{p}</mark> : <span key={i}>{p}</span>
  );
}

const providerName = (p) => (p === "together" ? "Together AI" : p);

function TextRequest({ preview }) {
  const system = preview.messages.find((m) => m.role === "system")?.content || "";
  const user = preview.messages.find((m) => m.role === "user")?.content || "";
  // Show the student's (de-identified) material first; the rubric part is the same every time.
  const marker = preview.task === "draft_feedback" ? "=== INSTRUCTOR COMMENTS ===" : "=== POSTER TEXT ===";
  const cut = user.indexOf(marker);
  const rubric = cut > 0 ? user.slice(0, cut).trim() : "";
  const material = cut > 0 ? user.slice(cut) : user;
  return (
    <div className="req">
      <div className="req-head">
        <b>Text</b> → {providerName(preview.provider)} · {preview.model}
        <span className="muted small"> · {preview.replacements} replacement{preview.replacements === 1 ? "" : "s"} (highlighted)</span>
      </div>
      {preview.warnings.map((w) => <div key={w} className="notice">{w}</div>)}
      <pre className="payload"><Highlighted text={material} /></pre>
      {rubric && (
        <details>
          <summary className="muted">{preview.task === "draft_feedback" ? "Grades and rubric sent with the request" : "Rubric sent with the request"}</summary>
          <pre className="payload small">{rubric}</pre>
        </details>
      )}
      <details>
        <summary className="muted">Instructions given to the model</summary>
        <pre className="payload small">{system}</pre>
      </details>
    </div>
  );
}

export default function AIReviewDialog({ bundle, busy, error, onSend, onCancel, onToggleImage,
  onRedactionSaved, imageRefreshing }) {
  const { text, image, includeImage, studentId, imageError } = bundle;
  const isFeedback = text.task === "draft_feedback";
  return (
    <div className="modal-backdrop" onClick={busy ? undefined : onCancel}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">
          <h2>Review before sending — {isFeedback ? "draft feedback" : "suggest marks"}</h2>
        </div>
        <p className="hint">
          This is everything that will be sent. Names, matric numbers, e-mails and phone numbers were
          replaced on this computer{image ? "; on the poster image they are painted black" : ""}.
          Nothing has been sent yet.
        </p>
        {error && <div className="error inline">{error}</div>}

        <div className={image || imageError ? "req-grid" : ""}>
          <TextRequest preview={text} />
          {(image || imageError) && (
            <div className="req">
              <div className="req-head">
                <label className="check">
                  <input type="checkbox" checked={includeImage} disabled={!image || busy}
                    onChange={(e) => onToggleImage(e.target.checked)} />
                  <b>Poster image</b>
                </label>
                {image && <> → {providerName(image.provider)} · {image.model}
                  <span className="muted small"> · for poster design · {image.boxes} box{image.boxes === 1 ? "" : "es"}</span></>}
              </div>
              {imageError && <div className="notice">{imageError}</div>}
              {image && image.warnings.map((w) => <div key={w} className="notice">{w}</div>)}
              {image && (
                <>
                  <RedactionEditor studentId={studentId} onSaved={onRedactionSaved} />
                  <div className="muted small">
                    {imageRefreshing ? "Updating the image to be sent…"
                      : <a href={image.images[0]} target="_blank" rel="noreferrer">Open the exact image that will be sent ↗</a>}
                  </div>
                </>
              )}
            </div>
          )}
        </div>

        <div className="modal-actions">
          <span className="muted small">
            Request{image && includeImage ? "s" : ""} #{text.request_id}{image && includeImage ? ` and #${image.request_id}` : ""} kept in the local send log.
          </span>
          <span className="spacer" />
          <button onClick={onCancel} disabled={busy}>Cancel</button>
          <button className="primary" onClick={onSend} disabled={busy || imageRefreshing}>
            {busy ? "Waiting for the model…" : image && includeImage ? "Send both" : "Send to Together"}
          </button>
        </div>
      </div>
    </div>
  );
}
