import { useState } from "react";
import { Page, Suggestion } from "./types";

interface Props {
  page: Page;
  busy: boolean;
  onAccept: (s: Suggestion) => void;
  onIgnore: (id: string) => void;
  onCheck: () => void;
  onShow: (s: Suggestion) => void;
}

// Possible sound-alike mistakes found by the Hebrew language model. Nothing changes unless accepted.
// Suggestions whose word was edited since are hidden (they no longer apply).
export function Suggestions({ page, busy, onAccept, onIgnore, onCheck, onShow }: Props) {
  const [open_, setOpen] = useState(false);
  if (page.status !== "transcribed") return null;
  if (page.suggestions === undefined) {
    return (
      <div className="suggestions">
        <button onClick={onCheck} disabled={busy}>Check for sound-alike mistakes</button>
      </div>
    );
  }
  const live = new Set(
    page.lines.flatMap((L) => (L.words ?? []).filter((w) => w.suggestion).map((w) => w.suggestion!.id)),
  );
  const open = page.suggestions.filter((s) => s.status === "open" && live.has(s.id));
  const auto = page.lines.flatMap((L) => (L.words ?? []).filter((w) => w.auto_from));
  const autoNote = auto.length ? (
    <div className="muted small auto-note" dir="rtl">
      <span dir="ltr">Fixed automatically ({auto.length}), language model and handwriting agree: </span>
      {auto.map((w) => (
        <span key={w.id} className="auto-fix">
          {w.auto_from} ← <strong>{w.text}</strong>
        </span>
      ))}
      <span dir="ltr"> — yellow in Review; U/ו undoes one.</span>
    </div>
  ) : null;
  const again = (
    <button className="link small" onClick={onCheck} disabled={busy} title="Run the sound-alike and handwriting check again">
      Check again
    </button>
  );
  if (!open.length)
    return (
      <div className="suggestions">
        {autoNote ?? <span className="muted small">No sound-alike suggestions on this page. </span>}
        {again}
      </div>
    );
  return (
    <div className="suggestions">
      <div className="sugg-head clickable" onClick={() => setOpen(!open_)}>
        {open_ ? "▾" : "▸"} Possible sound-alike mistakes ({open.length}){" "}
        <span className="muted">— dashed words in Review; press S on one to accept</span>{" "}
        <span onClick={(e) => e.stopPropagation()}>{again}</span>
      </div>
      {autoNote}
      {open_ && (
      <ul>
        {open.map((s) => (
          <li key={s.id}>
            <span className="sugg-text" dir="rtl" lang="he" onClick={() => onShow(s)} title="Show on the page">
              <span className="muted">{s.before} </span>
              <span className="from">{s.from}</span>
              <span className="arrow"> ← </span>
              <span className="to">{s.to}</span>
              <span className="muted"> {s.after}</span>
            </span>
            <span className="sugg-buttons">
              <span className="muted line-no">line {s.line}</span>
              <button onClick={() => onAccept(s)}>Accept</button>
              <button onClick={() => onIgnore(s.id)}>Ignore</button>
            </span>
          </li>
        ))}
      </ul>
      )}
    </div>
  );
}
