import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, STATUS_DOT, STATUS_LABEL } from "./api.js";
import ImportDialog from "./ImportDialog.jsx";
import NotesDialog from "./NotesDialog.jsx";
import AIReviewDialog from "./AIReviewDialog.jsx";
import FeedbackPanel from "./FeedbackPanel.jsx";
import PosterViewer from "./PosterViewer.jsx";

const band = (letter) => (letter ? letter[0] : null); // "A-" -> "A"
const SAVE_DELAY = 700;

// Per-browser convenience only (last student viewed); safe to lose.
const remember = {
  get: (k) => { try { return localStorage.getItem(`pg.${k}`); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(`pg.${k}`, String(v)); } catch { /* ignore */ } },
};

function markError(value, dim, step) {
  if (value === "" || value == null) return null;
  const n = Number(value);
  if (Number.isNaN(n)) return "not a number";
  if (n < 0 || n > Number(dim.max_marks)) return `0–${Number(dim.max_marks)}`;
  if (Math.abs(n / step - Math.round(n / step)) > 1e-9) return `steps of ${step}`;
  return null;
}

export default function App() {
  const [health, setHealth] = useState(null);
  const [assignments, setAssignments] = useState([]);
  const [assignment, setAssignment] = useState(null);
  const [students, setStudents] = useState([]);
  const [groupFilter, setGroupFilter] = useState("all");
  const [currentId, setCurrentId] = useState(null);
  const [student, setStudent] = useState(null);     // server copy
  const [draft, setDraft] = useState(null);         // {presentation_note, comment, marks}
  const [saveState, setSaveState] = useState("saved"); // saved | pending | saving | error
  const [error, setError] = useState("");
  const [showImport, setShowImport] = useState(false);
  const [showNotes, setShowNotes] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [aiPreview, setAiPreview] = useState(null);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiError, setAiError] = useState("");
  const [tab, setTab] = useState("grading");      // grading | feedback
  const [feedbackKey, setFeedbackKey] = useState(0);
  const timer = useRef(null);
  const pending = useRef(null);  // {id, payload}

  // ---------- loading
  const loadStudents = useCallback(async (aid) => {
    const list = await api(`/assignments/${aid}/students`);
    setStudents(list);
    return list;
  }, []);

  useEffect(() => {
    api("/health").then(setHealth).catch((e) => setError(e.message));
    api("/assignments")
      .then(async (list) => {
        setAssignments(list);
        if (!list[0]) return;
        const a = await api(`/assignments/${list[0].id}`);
        setAssignment(a);
        const st = await loadStudents(a.assignment.id);
        const last = Number(remember.get("currentId"));
        const pick = st.find((x) => x.id === last) || st[0];
        if (pick) setCurrentId(pick.id);
      })
      .catch((e) => setError(e.message));
  }, [loadStudents]);

  const visible = useMemo(
    () => students.filter((s) => groupFilter === "all" || s.group === groupFilter),
    [students, groupFilter]
  );
  const index = visible.findIndex((s) => s.id === currentId);

  // ---------- saving
  const step = assignment ? Number(assignment.scoring.mark_step) : 0.25;

  const flush = useCallback(async () => {
    clearTimeout(timer.current);
    const job = pending.current;
    if (!job) return;
    pending.current = null;
    setSaveState("saving");
    try {
      const d = await api(`/students/${job.id}/assessment`, {
        method: "PUT",
        body: JSON.stringify(job.payload),
      });
      setStudents((list) => list.map((s) => (s.id === d.id ? { ...s, status: d.status } : s)));
      setStudent((cur) => (cur && cur.id === d.id ? d : cur));
      setSaveState(pending.current ? "pending" : "saved");
    } catch (e) {
      setSaveState("error");
      setError(`Save failed: ${e.message}`);
    }
  }, []);

  const edit = (patch) => {
    const next = { ...draft, ...patch };
    setDraft(next);
    const invalid = assignment.dimensions.some((d) => markError(next.marks[d.id], d, step));
    const marks = Object.fromEntries(
      Object.entries(next.marks).map(([k, v]) => [k, v === "" ? null : v])
    );
    const payload = { presentation_note: next.presentation_note, comment: next.comment };
    if (!invalid) payload.marks = marks;
    pending.current = { id: student.id, payload };
    setSaveState("pending");
    clearTimeout(timer.current);
    timer.current = setTimeout(flush, SAVE_DELAY);
  };

  // ---------- switching students
  useEffect(() => {
    if (!currentId) { setStudent(null); setDraft(null); return; }
    remember.set("currentId", currentId);
    let cancelled = false;
    (async () => {
      await flush();
      const d = await api(`/students/${currentId}`);
      if (cancelled) return;
      setStudent(d);
      setDraft({
        presentation_note: d.assessment.presentation_note,
        comment: d.assessment.comment,
        marks: d.assessment.marks,
      });
      setError("");
    })().catch((e) => setError(e.message));
    return () => { cancelled = true; };
  }, [currentId, flush, reloadKey]);

  const go = useCallback((delta) => {
    if (!visible.length) return;
    const i = index < 0 ? 0 : Math.min(visible.length - 1, Math.max(0, index + delta));
    setCurrentId(visible[i].id);
  }, [visible, index]);

  useEffect(() => {
    const onKey = (e) => {
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
      if (typing || showImport || showNotes || aiPreview || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "ArrowRight" || e.key === "j") { e.preventDefault(); go(1); }
      if (e.key === "ArrowLeft" || e.key === "k") { e.preventDefault(); go(-1); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, showImport, showNotes, aiPreview]);

  useEffect(() => {
    const warn = (e) => { if (pending.current) { flush(); e.preventDefault(); } };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [flush]);

  const changeGroup = (g) => {
    setGroupFilter(g);
    const cur = students.find((s) => s.id === currentId);
    if (g !== "all" && cur?.group !== g) {
      const first = students.find((s) => s.group === g);
      setCurrentId(first ? first.id : null);
    }
  };

  // ---------- AI requests (text, plus a redacted poster image for design)
  // aiPreview = { text, image, includeImage, imageError, studentId }
  const [imageRefreshing, setImageRefreshing] = useState(false);

  const previewImage = async (sid) => {
    try {
      const ip = await api(`/students/${sid}/ai/design-review/preview`, { method: "POST" });
      return { image: ip, imageError: "" };
    } catch (e) { return { image: null, imageError: `Poster image not included: ${e.message}` }; }
  };

  const sendAi = async (bundle) => {
    setAiBusy(true); setAiError("");
    let textSent = !!bundle.textSent;
    try {
      let d = null;
      if (!textSent) {
        d = await api(`/ai/requests/${bundle.text.request_id}/send`, { method: "POST" });
        textSent = true;
      }
      if (bundle.image && bundle.includeImage) {
        d = await api(`/ai/requests/${bundle.image.request_id}/send`, { method: "POST" });
      }
      setStudent((cur) => (cur && cur.id === d.id ? d : cur));
      setAiPreview(null);
      if (bundle.text.task === "draft_feedback") setFeedbackKey((k) => k + 1);
    } catch (e) {
      setAiError(textSent ? `The text suggestion arrived, but the image request failed: ${e.message}` : e.message);
      setAiPreview({ ...bundle, textSent });
      if (textSent) api(`/students/${bundle.studentId}`).then((d) => setStudent((cur) => (cur && cur.id === d.id ? d : cur)));
    } finally { setAiBusy(false); }
  };

  const requestAi = async (kind = "marks") => {
    setError(""); setAiError("");
    await flush();
    setAiBusy(true);
    try {
      const path = kind === "feedback" ? "draft-feedback" : "suggest-marks";
      const text = await api(`/students/${student.id}/ai/${path}/preview`, { method: "POST" });
      let bundle = { text, image: null, includeImage: false, imageError: "", studentId: student.id };
      if (kind === "marks" && health?.vision_configured) {
        const im = await previewImage(student.id);
        bundle = { ...bundle, ...im, includeImage: !!im.image && (im.image.has_text || im.image.boxes > 0) };
      }
      // Always show the review when an image is involved: only a person can check the image.
      if (text.require_confirmation || text.residuals.length || bundle.image) setAiPreview(bundle);
      else await sendAi(bundle);
    } catch (e) { setError(e.message); }
    finally { setAiBusy(false); }
  };

  const refreshImage = async () => {
    if (!aiPreview) return;
    setImageRefreshing(true);
    const im = await previewImage(aiPreview.studentId);
    setAiPreview((cur) => (cur ? { ...cur, ...im, includeImage: !!im.image && cur.includeImage } : cur));
    setImageRefreshing(false);
  };

  const aiDim = (id) => student?.ai_marks?.dimensions.find((d) => d.id === id);
  const useAi = (ids) => {
    const marks = { ...draft.marks };
    ids.forEach((id) => { const a = aiDim(id); if (a?.marks != null) marks[id] = a.marks; });
    edit({ marks });
  };

  const score = student?.score;
  const dimScore = (id) => score?.dimensions.find((d) => d.id === id);
  const counts = useMemo(() => ({
    marked: visible.filter((s) => s.status === "marked" || s.status === "exported").length,
    exported: visible.filter((s) => s.status === "exported").length,
    total: visible.length,
  }), [visible]);

  return (
    <div className="app">
      <header>
        <strong>Poster Grading</strong>
        <select
          value={assignment?.assignment.id || ""}
          onChange={(e) => api(`/assignments/${e.target.value}`).then(setAssignment)}
        >
          {assignments.map((a) => <option key={a.id} value={a.id}>{a.course} · {a.title}</option>)}
        </select>

        {assignment && (
          <select value={groupFilter} onChange={(e) => changeGroup(e.target.value)}>
            <option value="all">All groups</option>
            {assignment.assignment.groups.map((g) => <option key={g}>{g}</option>)}
          </select>
        )}

        {visible.length > 0 && (
          <div className="nav">
            <button onClick={() => go(-1)} disabled={index <= 0} title="Previous (←)">‹</button>
            <select value={currentId || ""} onChange={(e) => setCurrentId(Number(e.target.value))}>
              {visible.map((s, i) => (
                <option key={s.id} value={s.id}>
                  {STATUS_DOT[s.status]} {String(i + 1).padStart(2, "0")} · {s.name} · {s.group}
                </option>
              ))}
            </select>
            <button onClick={() => go(1)} disabled={index >= visible.length - 1} title="Next (→)">›</button>
            <span className="muted">{counts.marked}/{counts.total} marked · {counts.exported} exported</span>
          </div>
        )}

        <span className="spacer" />
        <span className={`save ${saveState}`}>
          {{ saved: "Saved", pending: "Unsaved…", saving: "Saving…", error: "Not saved" }[saveState]}
        </span>
        {assignment && <button onClick={() => setShowImport(true)}>Import posters</button>}
        {assignment && students.length > 0 && (
          <button onClick={async () => { await flush(); setShowNotes(true); }}>Presentation notes</button>
        )}
        {assignment && students.length > 0 && (
          <details className="menu">
            <summary>Export</summary>
            <div className="menu-items">
              {(() => {
                const q = groupFilter !== "all" ? `?group=${groupFilter}` : "";
                const scope = groupFilter !== "all" ? groupFilter : "all groups";
                return (<>
                  <a href={`/api/assignments/${assignment.assignment.id}/marks.csv${q}`}>Marks (CSV) — {scope}</a>
                  <a href={`/api/assignments/${assignment.assignment.id}/exports.zip${q}`}>Feedback PDFs (zip) — {scope}</a>
                </>);
              })()}
            </div>
          </details>
        )}
        {health && (
          <span className={`pill ${health.together_configured ? "ok" : "warn"}`}>
            Together API {health.together_configured ? "configured" : "not configured"}
          </span>
        )}
      </header>

      {error && <div className="error" onClick={() => setError("")}>{error} (click to dismiss)</div>}

      <main>
        <section className="pane poster">
          {student ? (
            <PosterViewer student={student} />
          ) : (
            <div className="placeholder">
              No posters yet
              <small>Use “Import posters” to add a group's PDF submissions.</small>
              {assignment && <button className="primary" onClick={() => setShowImport(true)}>Import posters</button>}
            </div>
          )}
        </section>

        <section className="pane side">
          {student && draft ? (
            <>
              <div className="student-head">
                <h1>{student.name}</h1>
                <span className="muted">{student.group} · {student.pseudonym} · {STATUS_LABEL[student.status]}</span>
                <span className="spacer" />
                {score?.complete && <span className="grade-chip">{score.letter} · {Number(score.total_marks)}/{Number(score.max_marks)}</span>}
                <div className="tabs">
                  <button className={tab === "grading" ? "on" : ""} onClick={() => setTab("grading")}>Grading</button>
                  <button className={tab === "feedback" ? "on" : ""} onClick={async () => { await flush(); setTab("feedback"); }}>Feedback</button>
                </div>
              </div>
              {tab === "feedback" ? (
                <FeedbackPanel
                  student={student}
                  marksComplete={!!score?.complete}
                  aiAvailable={!!health?.together_configured}
                  aiBusy={aiBusy}
                  reloadKey={feedbackKey}
                  onRequestDraft={() => requestAi("feedback")}
                  onStatus={(id, status) => {
                    setStudents((list) => list.map((x) => (x.id === id ? { ...x, status } : x)));
                    setStudent((cur) => (cur && cur.id === id ? { ...cur, status } : cur));
                  }}
                />
              ) : (<>
              <div className="inputs">
                <label>
                  Presentation note
                  <textarea value={draft.presentation_note}
                    onChange={(e) => edit({ presentation_note: e.target.value })}
                    placeholder="Paste this student's presentation note" />
                </label>
                <label>
                  My comments
                  <textarea value={draft.comment}
                    onChange={(e) => edit({ comment: e.target.value })}
                    placeholder="Short comments on the poster" />
                </label>
              </div>

              <div className="scoring">
                <div className="scoring-head">
                  <h2>Scoring</h2>
                  <span className="spacer" />
                  {student.ai_marks && (
                    <>
                      <span className="muted small">
                        AI ({student.ai_marks.model.split("/").pop()}),{" "}
                        {new Date(student.ai_marks.sent_at).toLocaleString([], { dateStyle: "short", timeStyle: "short" })}
                      </span>
                      <button onClick={() => useAi(assignment.dimensions.map((d) => d.id))}>Use all AI marks</button>
                    </>
                  )}
                  <button className="ai" onClick={() => requestAi("marks")}
                    disabled={aiBusy || !health?.together_configured}
                    title={health?.together_configured ? "Suggest marks with AI (you review the request first)"
                      : "Add the Together API key and model to .env first"}>
                    {aiBusy && !aiPreview ? "Preparing…" : student.ai_marks ? "✦ Suggest again" : "✦ AI suggest marks"}
                  </button>
                </div>
                {assignment.dimensions.map((d) => {
                  const s = dimScore(d.id);
                  const value = draft.marks[d.id] ?? "";
                  const err = markError(value, d, step);
                  return (
                    <div className="dim" key={d.id}>
                      <div className="dim-head">
                        <span className="dim-name">{d.name}</span>
                        <span className="dim-letter">{!err && s?.letter ? s.letter : "—"}</span>
                        <input
                          type="number" min="0" max={d.max_marks} step={step}
                          className={err ? "invalid" : ""} title={err || ""}
                          value={value}
                          onChange={(e) => edit({ marks: { ...draft.marks, [d.id]: e.target.value } })}
                        />
                        <span className="max">/ {Number(d.max_marks)}</span>
                      </div>
                      {err && <p className="descriptor bad">Use {err}</p>}
                      {aiDim(d.id) && (
                        <div className="ai-row">
                          <span className="ai-mark">AI {aiDim(d.id).marks ?? "—"}</span>
                          <span className="ai-why">{aiDim(d.id).rationale}</span>
                          {aiDim(d.id).marks != null && String(aiDim(d.id).marks) !== String(value) && (
                            <button className="link" onClick={() => useAi([d.id])}>Use</button>
                          )}
                        </div>
                      )}
                      {!err && s?.letter && d.descriptors[band(s.letter)] && (
                        <p className="descriptor">{d.descriptors[band(s.letter)]}</p>
                      )}
                    </div>
                  );
                })}
                {score && (
                  <div className="total">
                    <div>
                      <span className="big">{Number(score.total_marks)}</span>
                      <span> / {Number(score.max_marks)}</span>
                    </div>
                    <div>
                      {score.complete ? (
                        <><span className="big">{score.letter}</span><span> ({score.rounded_percent}%)</span></>
                      ) : (
                        <span className="muted">Enter all dimensions for a grade</span>
                      )}
                    </div>
                  </div>
                )}
              </div>
              </>)}
            </>
          ) : (
            <div className="placeholder small">Select or import a student to start grading.</div>
          )}
        </section>
      </main>

      {aiPreview && (
        <AIReviewDialog
          bundle={aiPreview}
          imageRefreshing={imageRefreshing}
          onToggleImage={(v) => setAiPreview((cur) => ({ ...cur, includeImage: v }))}
          onRedactionSaved={refreshImage}
          busy={aiBusy}
          error={aiError}
          onSend={() => sendAi(aiPreview)}
          onCancel={() => { setAiPreview(null); setAiError(""); }}
        />
      )}

      {showNotes && assignment && (
        <NotesDialog
          assignment={assignment}
          students={students}
          initialGroup={groupFilter !== "all" ? groupFilter : student?.group}
          onClose={() => setShowNotes(false)}
          onApplied={async () => {
            await loadStudents(assignment.assignment.id);
            setReloadKey((k) => k + 1);
          }}
        />
      )}

      {showImport && assignment && (
        <ImportDialog
          assignment={assignment}
          onClose={() => setShowImport(false)}
          onImported={async () => {
            const list = await loadStudents(assignment.assignment.id);
            if (!currentId && list[0]) setCurrentId(list[0].id);
          }}
        />
      )}
    </div>
  );
}
