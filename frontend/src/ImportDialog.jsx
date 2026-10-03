import { useRef, useState } from "react";
import { api } from "./api.js";

export default function ImportDialog({ assignment, onClose, onImported }) {
  const groups = assignment.assignment.groups;
  const [group, setGroup] = useState(groups[0]);
  const [files, setFiles] = useState([]);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [drag, setDrag] = useState(false);
  const input = useRef(null);

  const addFiles = (list) =>
    setFiles(Array.from(list).filter((f) => f.name.toLowerCase().endsWith(".pdf")));

  const upload = async () => {
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      form.append("group", group);
      files.forEach((f) => form.append("files", f, f.name));
      const r = await api(`/assignments/${assignment.assignment.id}/import`, {
        method: "POST",
        body: form,
      });
      setResult(r);
      setFiles([]);
      onImported();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>Import posters</h2>
        <label className="row">
          Group
          <select value={group} onChange={(e) => setGroup(e.target.value)}>
            {groups.map((g) => <option key={g}>{g}</option>)}
          </select>
        </label>

        <div
          className={`dropzone ${drag ? "drag" : ""}`}
          onClick={() => input.current.click()}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); addFiles(e.dataTransfer.files); }}
        >
          {files.length
            ? `${files.length} PDF file${files.length > 1 ? "s" : ""} selected`
            : "Drop the poster PDFs here, or click to choose"}
          <small>Files that are not student submissions (e.g. Rubric.pdf) are skipped.</small>
          <input ref={input} type="file" accept="application/pdf" multiple hidden
            onChange={(e) => addFiles(e.target.files)} />
        </div>

        {error && <div className="error inline">{error}</div>}

        {result && (
          <div className="import-result">
            <div>Added: <b>{result.added.length}</b> · Updated: <b>{result.updated.length}</b> ·
              Unchanged: <b>{result.unchanged.length}</b> · Skipped: <b>{result.errors.length}</b></div>
            {result.errors.length > 0 && (
              <ul>{result.errors.map((e) => <li key={e.file}>{e.file} — {e.error}</li>)}</ul>
            )}
          </div>
        )}

        <div className="modal-actions">
          <button onClick={onClose}>{result ? "Done" : "Cancel"}</button>
          <button className="primary" disabled={!files.length || busy} onClick={upload}>
            {busy ? "Importing…" : `Import into ${group}`}
          </button>
        </div>
      </div>
    </div>
  );
}
