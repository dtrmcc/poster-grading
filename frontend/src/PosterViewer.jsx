import { useState } from "react";

const ZOOMS = [50, 75, 100, 125, 150, 200, 300]; // % of pane width

export default function PosterViewer({ student }) {
  const [mode, setMode] = useState("page"); // "page" | "width" | number
  if (!student) return null;

  const pages = Array.from({ length: student.pages }, (_, i) => i + 1);
  const zoom = typeof mode === "number" ? mode : 100;
  const step = (dir) => {
    const i = ZOOMS.indexOf(zoom);
    const next = ZOOMS[Math.min(ZOOMS.length - 1, Math.max(0, (i < 0 ? 2 : i) + dir))];
    setMode(next);
  };
  const imgStyle =
    mode === "page" ? { maxHeight: "100%", maxWidth: "100%" }
    : mode === "width" ? { width: "100%" }
    : { width: `${mode}%`, maxWidth: "none" };

  return (
    <div className="viewer">
      <div className="viewer-bar">
        <button className={mode === "page" ? "on" : ""} onClick={() => setMode("page")}>Fit page</button>
        <button className={mode === "width" ? "on" : ""} onClick={() => setMode("width")}>Fit width</button>
        <button onClick={() => step(-1)} title="Zoom out">−</button>
        <button onClick={() => step(1)} title="Zoom in">+</button>
        {typeof mode === "number" && <span className="muted">{mode}%</span>}
        <span className="spacer" />
        {!student.has_text && <span className="pill warn">No extractable text (image-only PDF)</span>}
        <a href={`/api/students/${student.id}/pdf`} target="_blank" rel="noreferrer">Open PDF ↗</a>
      </div>
      <div className={`viewer-pages ${mode === "page" ? "fit-page" : ""}`}>
        {pages.map((p) => (
          <img
            key={`${student.id}-${p}`}
            src={`/api/students/${student.id}/pages/${p}.jpg`}
            alt={`Poster page ${p}`}
            style={imgStyle}
          />
        ))}
      </div>
    </div>
  );
}
