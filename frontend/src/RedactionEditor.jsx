import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";

// Shows the poster with every box that will be painted black before sending.
// Automatic boxes (names/matric/e-mail found in the PDF) cannot be removed;
// drag on the image to add a box, click × to remove one you added.
export default function RedactionEditor({ studentId, onSaved }) {
  const [data, setData] = useState(null);
  const [page, setPage] = useState(0);
  const [temp, setTemp] = useState(null);
  const [error, setError] = useState("");
  const start = useRef(null);
  const overlay = useRef(null);

  useEffect(() => {
    api(`/students/${studentId}/redaction`).then(setData).catch((e) => setError(e.message));
  }, [studentId]);

  const pos = (e) => {
    const r = overlay.current.getBoundingClientRect();
    return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
            Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
  };
  const toBox = (a, b) => [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];

  const save = async (manual) => {
    try {
      const d = await api(`/students/${studentId}/redaction`, { method: "PUT", body: JSON.stringify({ manual }) });
      setData(d);
      onSaved();
    } catch (e) { setError(e.message); }
  };

  const onDown = (e) => { e.preventDefault(); start.current = pos(e); setTemp(null); };
  const onMove = (e) => { if (start.current) setTemp(toBox(start.current, pos(e))); };
  const onUp = (e) => {
    if (!start.current) return;
    const b = toBox(start.current, pos(e));
    start.current = null;
    setTemp(null);
    if (b[2] - b[0] < 0.005 || b[3] - b[1] < 0.004) return;
    const manual = data.pages.map((p) => p.manual);
    manual[page] = [...manual[page], b.map((v) => Math.round(v * 1e5) / 1e5)];
    save(manual);
  };
  const remove = (i) => {
    const manual = data.pages.map((p) => p.manual);
    manual[page] = manual[page].filter((_, j) => j !== i);
    save(manual);
  };

  if (error) return <div className="error inline">{error}</div>;
  if (!data) return <p className="muted">Finding names on the poster…</p>;
  const p = data.pages[page];
  const style = (b) => ({ left: `${b[0] * 100}%`, top: `${b[1] * 100}%`,
                          width: `${(b[2] - b[0]) * 100}%`, height: `${(b[3] - b[1]) * 100}%` });
  return (
    <div className="redactor">
      <div className="redactor-bar">
        <span className="muted small">
          {p.auto.length} found automatically · {p.manual.length} added by you · drag on the image to cover more
        </span>
        <span className="spacer" />
        {data.pages.length > 1 && data.pages.map((_, i) => (
          <button key={i} className={i === page ? "on" : ""} onClick={() => setPage(i)}>Page {i + 1}</button>
        ))}
      </div>
      <div className="redactor-canvas">
        <img src={p.original} alt="Poster" draggable={false} />
        <div ref={overlay} className="redactor-overlay"
          onMouseDown={onDown} onMouseMove={onMove} onMouseUp={onUp} onMouseLeave={onUp}>
          {p.auto.map((b, i) => <div key={`a${i}`} className="rbox auto" style={style(b)} title="Found automatically" />)}
          {p.manual.map((b, i) => (
            <div key={`m${i}`} className="rbox manual" style={style(b)}>
              <button className="rbox-x" onMouseDown={(e) => e.stopPropagation()}
                onClick={(e) => { e.stopPropagation(); remove(i); }} title="Remove this box">×</button>
            </div>
          ))}
          {temp && <div className="rbox temp" style={style(temp)} />}
        </div>
      </div>
    </div>
  );
}
