import { LayoutTool } from "./PageView";
import { Page } from "./types";

interface Props {
  page: Page;
  on: boolean;
  setOn: (on: boolean) => void;
  tool: LayoutTool;
  setTool: (t: LayoutTool) => void;
  selectedLine: string | null;
  edit: (op: string, args?: Record<string, unknown>) => void;
  busy: boolean;
}

const HINT: Record<LayoutTool, string> = {
  select: "Click a line to select it; drag its orange end handles to stretch or shrink it.",
  split: "Click the line where it should be cut in two.",
  add_line: "Drag a rectangle around text that has no line.",
  add_drawing: "Drag a rectangle around a drawing: lines inside it are skipped.",
};

// "Edit lines": fix the lines and drawings found on the page.
export function LayoutBar({ page, on, setOn, tool, setTool, selectedLine, edit, busy }: Props) {
  if (!on) {
    return (
      <div className="layout-bar off">
        <button onClick={() => setOn(true)} title="Merge, split, reorder or skip lines; mark drawings">Edit lines…</button>
      </div>
    );
  }
  const L = page.lines.find((x) => x.id === selectedLine);
  const toolBtn = (t: LayoutTool, label: string) => (
    <button className={tool === t ? "on" : ""} onClick={() => setTool(tool === t ? "select" : t)} disabled={busy}>
      {label}
    </button>
  );
  return (
    <div className="layout-bar">
      <strong>Edit lines</strong>
      {toolBtn("split", "✂ Split")}
      {toolBtn("add_line", "+ Line")}
      {toolBtn("add_drawing", "+ Drawing")}
      <button onClick={() => edit("detect_drawings")} disabled={busy} title="Find drawings automatically again">
        Find drawings
      </button>
      <span className="sep" />
      {L ? (
        <>
          <span>Line {L.order}:</span>
          <button onClick={() => edit("merge_next", { line: L.id })} disabled={busy} title="Join with the next line (e.g. two pieces of one line)">
            Merge with next
          </button>
          <button onClick={() => edit("move", { line: L.id, delta: -1 })} disabled={busy} title="Read this line earlier">
            ↑ Earlier
          </button>
          <button onClick={() => edit("move", { line: L.id, delta: 1 })} disabled={busy} title="Read this line later">
            ↓ Later
          </button>
          <button onClick={() => edit("skip", { line: L.id, skip: !L.skip })} disabled={busy} title="Skipped lines are not read">
            {L.skip ? "Include" : "Skip"}
          </button>
        </>
      ) : (
        <span className="muted">{HINT[tool]}</span>
      )}
      <span style={{ flex: 1 }} />
      <button className="primary" onClick={() => setOn(false)}>Done</button>
      {L && <span className="muted small hint-line">{HINT[tool]}</span>}
    </div>
  );
}
