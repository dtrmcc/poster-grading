import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";

// Import a group's presentation-note Word file, split into one block per student,
// and match blocks to students. Suggestions come from local keyword overlap.
export default function NotesDialog({ assignment, students, initialGroup, onClose, onApplied }) {
  const aid = assignment.assignment.id;
  const groups = assignment.assignment.groups;
  const [group, setGroup] = useState(initialGroup && groups.includes(initialGroup) ? initialGroup : groups[0]);
  const [data, setData] = useState(undefined);      // undefined = loading, null = none yet
  const [rows, setRows] = useState([]);              // [{block_id, student_id, ignored}]
  const [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [report, setReport] = useState(null);
  const input = useRef(null);

  const groupStudents = useMemo(() => students.filter((s) => s.group === group), [students, group]);
  const byId = useMemo(() => Object.fromEntries(groupStudents.map((s) => [s.id, s])), [groupStudents]);

  const load = (d) => {
    setData(d);
    setRows(d ? d.blocks.map((b) => ({ block_id: b.id, student_id: b.student_id, ignored: b.ignored })) : []);
    setReport(null);
  };

  useEffect(() => {
    setData(undefined);
    api(`/assignments/${aid}/notes/${group}`).then(load).catch((e) => setError(e.message));
  }, [aid, group]);

  const upload = async (file) => {
    if (!file) return;
    setBusy(true); setError("");
    try {
      const form = new FormData();
      form.append("group", group);
      form.append("file", file, file.name);
      load(await api(`/assignments/${aid}/notes/import`, { method: "POST", body: form }));
    } catch (e) { setError(e.message); }
    finally { setBusy(false); input.current.value = ""; }
  };

  const setRow = (i, patch) => setRows((r) => r.map((x, j) => (j === i ? { ...x, ...patch } : x)));

  const takenBy = useMemo(() => {
    const m = {};
    rows.forEach((r, i) => { if (r.student_id && !r.ignored) (m[r.student_id] ||= []).push(i + 1); });
    return m;
  }, [rows]);
  const withoutNotes = groupStudents.filter((s) => !takenBy[s.id]);
  const unmatched = rows.filter((r) => !r.student_id && !r.ignored).length;

  const apply = async () => {
    setBusy(true); setError("");
    try {
      const rep = await api(`/assignments/${aid}/notes/${group}/apply`, {
        method: "POST", body: JSON.stringify({ matches: rows, overwrite }),
      });
      setReport(rep);
      onApplied();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">
          <h2>Presentation notes</h2>
          <select value={group} onChange={(e) => setGroup(e.target.value)}>
            {groups.map((g) => <option key={g}>{g}</option>)}
          </select>
          {data && <span className="muted">{data.filename} · {data.blocks.length} blocks</span>}
          <span className="spacer" />
          <button onClick={() => input.current.click()} disabled={busy}>
            {data ? "Replace Word file…" : "Choose Word file…"}
          </button>
          <input ref={input} type="file" hidden
            accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            onChange={(e) => upload(e.target.files[0])} />
        </div>

        {error && <div className="error inline">{error}</div>}
        {!groupStudents.length && (
          <div className="error inline">Import {group}'s posters first, so there are students to match.</div>
        )}

        {data === undefined && <p className="muted">Loading…</p>}
        {data === null && (
          <div className="dropzone" onClick={() => input.current.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => { e.preventDefault(); upload(e.dataTransfer.files[0]); }}>
            Drop {group}'s presentation-note Word file here, or click to choose
            <small>Each top-level bullet starts a new student, in presentation order.</small>
          </div>
        )}

        {data && (
          <>
            <p className="hint">
              Match each block to a student. Suggestions come from words shared with the
              student's poster (computed on this computer); check them before applying.
              Blocks that look like absences are pre-ticked as ignored.
            </p>
            <div className="blocks">
              {data.blocks.map((b, i) => {
                const row = rows[i] || {};
                const isSuggested = b.suggested_student_id && row.student_id === b.suggested_student_id;
                const dup = row.student_id && takenBy[row.student_id]?.length > 1;
                return (
                  <div className={`block ${row.ignored ? "ignored" : ""}`} key={b.id}>
                    <div className="block-no">#{b.position}</div>
                    <pre className="block-text">{b.text}</pre>
                    <div className="block-match">
                      <select value={row.student_id || ""} disabled={row.ignored}
                        onChange={(e) => setRow(i, { student_id: e.target.value ? Number(e.target.value) : null })}>
                        <option value="">— not matched —</option>
                        {groupStudents.map((s) => {
                          const others = (takenBy[s.id] || []).filter((n) => n !== i + 1);
                          return (
                            <option key={s.id} value={s.id}>
                              {others.length ? "✓ " : ""}{s.name}{s.title ? ` — ${s.title}` : ""}
                            </option>
                          );
                        })}
                      </select>
                      <div className="block-flags">
                        {isSuggested && (
                          <span className={`pill ${b.suggestion_confident ? "ok" : "warn"}`}>
                            {b.suggestion_confident ? "Suggested · strong" : "Suggested · weak"}
                          </span>
                        )}
                        {dup && <span className="pill warn">Also on #{takenBy[row.student_id].filter((n) => n !== i + 1).join(", #")}</span>}
                        <label className="check">
                          <input type="checkbox" checked={!!row.ignored}
                            onChange={(e) => setRow(i, { ignored: e.target.checked })} />
                          Ignore (absent / not a student)
                        </label>
                      </div>
                      {row.student_id && byId[row.student_id]?.title && !row.ignored && (
                        <div className="muted small">Poster: {byId[row.student_id].title}</div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>

            <div className="notes-footer">
              <div className="muted">
                {unmatched > 0 && <span className="warn-text">{unmatched} block{unmatched > 1 ? "s" : ""} not matched. </span>}
                {withoutNotes.length > 0
                  ? <>Students without notes: {withoutNotes.map((s) => s.name).join(", ")}</>
                  : "Every student has a matched block."}
              </div>
              {report && (
                <div className="import-result">
                  Applied: <b>{report.applied.length}</b> · Unchanged: <b>{report.unchanged.length}</b> ·
                  Kept existing (edited) notes: <b>{report.kept_existing.length}</b>
                  {report.kept_existing.length > 0 && <> — {report.kept_existing.join(", ")}</>}
                </div>
              )}
              <div className="modal-actions">
                <label className="check">
                  <input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />
                  Overwrite notes I have already edited
                </label>
                <span className="spacer" />
                <button onClick={onClose}>{report ? "Done" : "Cancel"}</button>
                <button className="primary" disabled={busy || !groupStudents.length} onClick={apply}>
                  {busy ? "Applying…" : "Apply to students"}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
