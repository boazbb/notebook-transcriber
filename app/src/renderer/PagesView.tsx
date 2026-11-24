import { useEffect, useRef } from "react";
import { fileUrl, ProjectInfo, QueueState } from "./types";

interface Props {
  project: ProjectInfo;
  selected: number;
  onSelect: (n: number) => void;
  onOpen: (n: number) => void;
  queue: QueueState;
  recording: boolean;
  recPage: number | null;
  recInfo: { seconds: number; level: number; spaces: number; discards: number };
  onRecord: () => void;
  onStop: () => void;
  canRecord: boolean;
}

function fmt(s: number) {
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}

// M6: every page at a glance, recording page after page while earlier ones are transcribed in the background.
// R = record the selected page · Enter = stop: the page is queued and the next one selected.
export function PagesView(p: Props) {
  const { project, selected, queue } = p;
  const cardRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    cardRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selected]);

  const isCurrent = (n: number) => queue.current?.path === project.path && queue.current.page === n;
  const queuePos = (n: number) => queue.pending.findIndex((j) => j.path === project.path && j.page === n);
  const status = (n: number) => {
    const s = project.pages[n - 1];
    if (p.recording && p.recPage === n) return { cls: "rec", label: "● Recording" };
    if (isCurrent(n)) return { cls: "busy", label: `Transcribing — ${queue.current!.message || "…"}` };
    const q = queuePos(n);
    if (q >= 0) return { cls: "queued", label: q === 0 && !queue.current ? "Starting…" : `Waiting (${q + 1} in queue)` };
    if (s.status === "transcribed" && s.finished) return { cls: "finished", label: "✓ Finished" };
    if (s.status === "transcribed")
      return s.reviewed
        ? { cls: "reviewed", label: `Transcribed · ${s.reviewed}/${s.words} words reviewed` }
        : { cls: "done", label: `Transcribed · ${s.words} words` };
    if (s.status === "image") return { cls: "", label: "Finding lines…" };
    return { cls: "", label: "Not read yet" };
  };
  const selBusy = isCurrent(selected) || queuePos(selected) >= 0;

  return (
    <div className="pages-view">
      <div className={p.recording ? "pages-rec on" : "pages-rec"}>
        {p.recording ? (
          <>
            <span className="dot" />
            <strong>Recording page {p.recPage}</strong>
            <span className="rec-time">{fmt(p.recInfo.seconds)}</span>
            <div className="meter"><div style={{ width: `${Math.min(100, p.recInfo.level * 140)}%` }} /></div>
            <button className="primary" onClick={p.onStop}>■ Stop and queue (Enter)</button>
            <span className="muted small">
              Space: line end ({p.recInfo.spaces}) · Backspace: discard since the last mark ({p.recInfo.discards})
            </span>
          </>
        ) : (
          <>
            <strong>Page {selected}</strong>
            <span className="muted">{status(selected).label}</span>
            <button className="record" onClick={p.onRecord} disabled={!p.canRecord || selBusy}>
              ● Record page {selected} (R)
            </button>
            <span className="muted small">
              Read the page aloud from the notebook, then press <kbd>Enter</kbd>: it's transcribed in the background and
              the next page is selected — press <kbd>R</kbd> again. Double-click a page to open it.
            </span>
          </>
        )}
        {queue.current || queue.pending.length ? (
          <span className="queue-sum">
            Queue: {queue.current ? `page ${queue.current.page} now` : ""}
            {queue.pending.length ? `, then ${queue.pending.map((j) => j.page).join(", ")}` : ""}
          </span>
        ) : null}
      </div>
      <div className="pages-grid">
        {project.pages.map((s) => {
          const st = status(s.page);
          return (
            <div
              key={s.page}
              ref={s.page === selected ? cardRef : undefined}
              className={`page-card ${st.cls} ${s.page === selected ? "selected" : ""}`}
              onClick={() => !p.recording && p.onSelect(s.page)}
              onDoubleClick={() => !p.recording && p.onOpen(s.page)}
              title="Click to select · double-click to open"
            >
              <img src={fileUrl(s.image)} alt="" loading="lazy" draggable={false} />
              <div className="page-card-info">
                <strong>Page {s.page}</strong>
                <span className="small">{st.label}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
