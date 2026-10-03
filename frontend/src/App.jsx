import { useEffect, useMemo, useState } from "react";

const api = async (path, opts) => {
  const r = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};

const band = (letter) => (letter ? letter[0] : null); // "A-" -> "A"

export default function App() {
  const [health, setHealth] = useState(null);
  const [assignments, setAssignments] = useState([]);
  const [assignment, setAssignment] = useState(null);
  const [marks, setMarks] = useState({});
  const [score, setScore] = useState(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [comment, setComment] = useState("");

  useEffect(() => {
    api("/health").then(setHealth).catch((e) => setError(e.message));
    api("/assignments")
      .then((list) => {
        setAssignments(list);
        if (list[0]) return api(`/assignments/${list[0].id}`).then(setAssignment);
      })
      .catch((e) => setError(e.message));
  }, []);

  const entered = useMemo(
    () => Object.fromEntries(Object.entries(marks).filter(([, v]) => v !== "")),
    [marks]
  );

  useEffect(() => {
    if (!assignment) return;
    const t = setTimeout(() => {
      api(`/assignments/${assignment.assignment.id}/score`, {
        method: "POST",
        body: JSON.stringify({ marks: entered }),
      })
        .then((s) => { setScore(s); setError(""); })
        .catch((e) => setError(e.message));
    }, 200);
    return () => clearTimeout(t);
  }, [entered, assignment]);

  const dimScore = (id) => score?.dimensions.find((d) => d.id === id);
  const step = assignment ? Number(assignment.scoring.mark_step) : 0.25;

  return (
    <div className="app">
      <header>
        <strong>Poster Grading</strong>
        <select
          value={assignment?.assignment.id || ""}
          onChange={(e) => api(`/assignments/${e.target.value}`).then(setAssignment)}
        >
          {assignments.map((a) => (
            <option key={a.id} value={a.id}>{a.course} · {a.title}</option>
          ))}
        </select>
        <span className="spacer" />
        {health && (
          <span className={`pill ${health.together_configured ? "ok" : "warn"}`}>
            Together API {health.together_configured ? "configured" : "not configured"}
          </span>
        )}
      </header>

      {error && <div className="error">{error}</div>}

      <main>
        <section className="pane poster">
          <div className="placeholder">
            Poster viewer
            <small>Stage 2: student list, PDF viewer, keyboard navigation</small>
          </div>
        </section>

        <section className="pane side">
          <div className="inputs">
            <label>
              Presentation note
              <textarea value={note} onChange={(e) => setNote(e.target.value)}
                placeholder="Imported automatically from the class note in stage 2" />
            </label>
            <label>
              My comments
              <textarea value={comment} onChange={(e) => setComment(e.target.value)}
                placeholder="Short comments on the poster" />
            </label>
          </div>
          <p className="hint">Not saved yet — persistence arrives in stage 2.</p>

          <div className="scoring">
          <h2>Scoring</h2>
          {assignment?.dimensions.map((d) => {
            const s = dimScore(d.id);
            return (
              <div className="dim" key={d.id}>
                <div className="dim-head">
                  <span className="dim-name">{d.name}</span>
                  <span className="dim-letter">{s?.letter || "—"}</span>
                  <input
                    type="number" min="0" max={d.max_marks} step={step}
                    value={marks[d.id] ?? ""}
                    onChange={(e) => setMarks({ ...marks, [d.id]: e.target.value })}
                  />
                  <span className="max">/ {Number(d.max_marks)}</span>
                </div>
                {s?.letter && d.descriptors[band(s.letter)] && (
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
                  <>
                    <span className="big">{score.letter}</span>
                    <span> ({score.rounded_percent}%)</span>
                  </>
                ) : (
                  <span className="muted">Enter all dimensions for a grade</span>
                )}
              </div>
            </div>
          )}
          </div>
        </section>
      </main>
    </div>
  );
}
