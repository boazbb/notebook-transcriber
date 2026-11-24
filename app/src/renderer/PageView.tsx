import { useEffect, useRef, useState } from "react";
import { fileUrl, Page } from "./types";

interface Props {
  page: Page;
  chapterStarts: number[];
  startLine: number;
  onPickLine: (order: number) => void;
  version: number;
  selectedWord?: string | null;
  hoverWord?: string | null;
  onPickWord?: (id: string) => void;
  onAssignBox?: (line: number, index: number, add: boolean) => void;
  onResizeBox?: (line: number, index: number, rect: [number, number, number, number]) => void;
  layout?: LayoutProps; // "Edit lines" mode
}

export type LayoutTool = "select" | "split" | "add_line" | "add_drawing";

export interface LayoutProps {
  selectedLine: string | null;
  tool: LayoutTool;
  onSelectLine: (id: string | null) => void;
  onSplit: (id: string, x: number) => void;
  onRect: (tool: "add_line" | "add_drawing", rect: [number, number, number, number]) => void;
  onEnds: (id: string, x0: number, x1: number) => void;
  onRemoveDrawing: (id: string) => void;
}

// The scan with the detected lines drawn over it. Click a line to start reading there.
// In review, the selected word's handwriting is outlined (and the other words of its line faintly);
// clicking a word box selects that word.
export function PageView({
  page,
  chapterStarts,
  startLine,
  onPickLine,
  version,
  selectedWord,
  hoverWord,
  onPickWord,
  onAssignBox,
  onResizeBox,
  layout,
}: Props) {
  const [zoom, setZoom] = useState(1);
  const [w, h] = page.size;
  const lh = page.line_height ?? 40;
  const selRef = useRef<SVGRectElement | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  // layout mode: a rectangle being drawn, or a line end being dragged
  const [rect, setRect] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const [ends, setEnds] = useState<{ id: string; side: 0 | 2; x0: number; x1: number } | null>(null);
  // the latest values, for the mouse-up handler (its closure would otherwise see the drag's start)
  const rectRef = useRef(rect);
  const endsRef = useRef(ends);
  rectRef.current = rect;
  endsRef.current = ends;
  const toSvg = (cx: number, cy: number) => {
    const svg = svgRef.current!;
    const pt = svg.createSVGPoint();
    pt.x = cx;
    pt.y = cy;
    const q = pt.matrixTransform(svg.getScreenCTM()!.inverse());
    return [Math.round(q.x), Math.round(q.y)];
  };
  useEffect(() => {
    if (!rect && !ends) return;
    const move = (e: MouseEvent) => {
      const [x, y] = toSvg(e.clientX, e.clientY);
      if (rect) setRect((r) => (r ? { ...r, x1: x, y1: y } : r));
      if (ends) setEnds((d) => (d ? (d.side === 0 ? { ...d, x0: Math.min(x, d.x1 - 10) } : { ...d, x1: Math.max(x, d.x0 + 10) }) : d));
    };
    const up = () => {
      const r = rectRef.current;
      const d = endsRef.current;
      if (r && layout && (layout.tool === "add_line" || layout.tool === "add_drawing")) {
        if (Math.abs(r.x1 - r.x0) > 10 && Math.abs(r.y1 - r.y0) > 10)
          layout.onRect(layout.tool, [r.x0, r.y0, r.x1, r.y1]);
        setRect(null);
      }
      if (d && layout) {
        layout.onEnds(d.id, d.x0, d.x1);
        setEnds(null);
      }
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    return () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
  }, [rect !== null, ends !== null]);

  // a box edge being dragged: which box, which edge, and the box as it is now
  const [drag, setDrag] = useState<{ line: number; index: number; edge: 0 | 2; rect: [number, number, number, number] } | null>(null);

  useEffect(() => {
    if (!drag) return;
    const toX = (clientX: number) => {
      const svg = svgRef.current;
      if (!svg) return 0;
      const pt = svg.createSVGPoint();
      pt.x = clientX;
      pt.y = 0;
      return pt.matrixTransform(svg.getScreenCTM()!.inverse()).x;
    };
    const move = (e: MouseEvent) => {
      const x = Math.round(toX(e.clientX));
      setDrag((d) => {
        if (!d) return d;
        const r = [...d.rect] as [number, number, number, number];
        r[d.edge] = d.edge === 0 ? Math.min(x, r[2] - 6) : Math.max(x, r[0] + 6);
        return { ...d, rect: r };
      });
    };
    const up = () => {
      setDrag((d) => {
        if (d) onResizeBox?.(d.line, d.index, d.rect);
        return null;
      });
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    return () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
  }, [drag !== null]);

  // which word owns each box: "line:boxIndex" -> word id
  const owner = new Map<string, string>();
  let selLine: string | null = null;
  for (const L of page.lines) {
    for (const wd of L.words ?? []) {
      for (const b of wd.boxes) if (!owner.has(`${L.order}:${b}`)) owner.set(`${L.order}:${b}`, wd.id);
      if (wd.id === selectedWord) selLine = L.id;
    }
  }

  useEffect(() => {
    selRef.current?.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
  }, [selectedWord]);

  let firstSel = true;
  return (
    <div className="page-view">
      <div className="zoom">
        <button onClick={() => setZoom((z) => Math.max(0.5, z - 0.25))} title="Zoom out">−</button>
        <span>{Math.round(zoom * 100)}%</span>
        <button onClick={() => setZoom((z) => Math.min(3, z + 0.25))} title="Zoom in">+</button>
      </div>
      <div className="page-scroll">
        <div className="page-canvas" style={{ width: `${zoom * 100}%` }}>
          <img src={fileUrl(page.image, version)} alt={`Page ${page.page}`} draggable={false} />
          <svg
            ref={svgRef}
            viewBox={`0 0 ${w} ${h}`}
            preserveAspectRatio="none"
            className={layout ? `layout tool-${layout.tool}` : undefined}
            onMouseDown={(e) => {
              if (!layout || (layout.tool !== "add_line" && layout.tool !== "add_drawing")) return;
              const [x, y] = toSvg(e.clientX, e.clientY);
              setRect({ x0: x, y0: y, x1: x, y1: y });
              e.preventDefault();
            }}
          >
            {page.lines.map((L) => {
              const cls = [
                "line",
                L.skip ? "skipped" : "",
                L.in_drawing ? "in-drawing" : "",
                layout && L.id === layout.selectedLine ? "lsel" : "",
                L.read ? "read" : "",
                L.order === startLine ? "start" : "",
                L.order < startLine ? "before" : "",
                L.id === selLine ? "current" : "",
              ].join(" ");
              return (
                <g
                  key={L.id}
                  className={cls}
                  onClick={(e) => {
                    if (!layout) return onPickLine(L.order);
                    if (layout.tool === "split") {
                      const [x] = toSvg(e.clientX, e.clientY);
                      layout.onSplit(L.id, x);
                    } else if (layout.tool === "select") layout.onSelectLine(L.id);
                  }}
                >
                  <title>
                    {layout
                      ? layout.tool === "split"
                        ? `Click where line ${L.order} should be cut`
                        : `Line ${L.order}${L.skip ? " (skipped)" : ""}${L.in_drawing ? " (in a drawing: skipped)" : ""} — click to select`
                      : `Line ${L.order} (paragraph ${L.paragraph}) — click to start reading here`}
                  </title>
                  {L.polygons.map((poly, i) => (
                    <polygon key={i} points={poly.map((p) => p.join(",")).join(" ")} />
                  ))}
                  <text x={Math.min(L.bbox[2] + lh * 0.25, w - lh * 0.9)} y={L.baseline[0][1]} fontSize={lh * 0.45}>
                    {chapterStarts.includes(L.order) ? `§ ${L.order}` : L.order}
                  </text>
                </g>
              );
            })}
            {(page.drawings ?? []).map((d) => (
              <g key={d.id} className="drawing">
                <rect x={d.bbox[0]} y={d.bbox[1]} width={d.bbox[2] - d.bbox[0]} height={d.bbox[3] - d.bbox[1]}>
                  <title>{d.source === "user" ? "Drawing (marked by you)" : "Drawing (found automatically)"} — lines inside are skipped</title>
                </rect>
                {layout && (
                  <text
                    className="drawing-x"
                    x={d.bbox[2] - lh * 0.5}
                    y={d.bbox[1] + lh * 0.55}
                    fontSize={lh * 0.6}
                    onClick={(e) => {
                      e.stopPropagation();
                      layout.onRemoveDrawing(d.id);
                    }}
                  >
                    ×<title>Not a drawing: remove this box</title>
                  </text>
                )}
              </g>
            ))}
            {rect && (
              <rect
                className={rect && layout?.tool === "add_drawing" ? "drawing-new" : "line-new"}
                x={Math.min(rect.x0, rect.x1)}
                y={Math.min(rect.y0, rect.y1)}
                width={Math.abs(rect.x1 - rect.x0)}
                height={Math.abs(rect.y1 - rect.y0)}
              />
            )}
            {layout &&
              layout.tool === "select" &&
              page.lines
                .filter((L) => L.id === layout.selectedLine)
                .map((L) => {
                  const x0 = ends?.id === L.id ? ends.x0 : L.bbox[0];
                  const x1 = ends?.id === L.id ? ends.x1 : L.bbox[2];
                  return ([0, 2] as const).map((side) => (
                    <rect
                      key={side}
                      className="handle"
                      x={(side === 0 ? x0 : x1) - lh * 0.08}
                      y={L.bbox[1]}
                      width={lh * 0.16}
                      height={L.bbox[3] - L.bbox[1]}
                      onMouseDown={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        setEnds({ id: L.id, side, x0: L.bbox[0], x1: L.bbox[2] });
                      }}
                    >
                      <title>Drag to stretch or shrink the line</title>
                    </rect>
                  ));
                })}
            {ends && (
              <rect className="line-new" x={ends.x0} y={page.lines.find((L) => L.id === ends.id)!.bbox[1]}
                width={ends.x1 - ends.x0} height={page.lines.find((L) => L.id === ends.id)!.bbox[3] - page.lines.find((L) => L.id === ends.id)!.bbox[1]} />
            )}
            {onPickWord && !layout &&
              page.lines.map((L) =>
                L.boxes.map((b0, i) => {
                  const id = owner.get(`${L.order}:${i}`);
                  const sel = !!id && id === selectedWord;
                  const hov = !!id && id === hoverWord;
                  const inLine = L.id === selLine;
                  const b = drag && drag.line === L.order && drag.index === i ? drag.rect : b0;
                  const ref = sel && firstSel ? selRef : undefined;
                  if (sel) firstSel = false;
                  const cls = sel ? "wbox sel" : hov ? "wbox hov" : inLine ? (id ? "wbox" : "wbox orphan") : "wbox far";
                  return (
                    <g key={`${L.id}-${i}`}>
                      <rect
                        ref={ref}
                        className={cls}
                        x={b[0]}
                        y={b[1]}
                        width={b[2] - b[0]}
                        height={b[3] - b[1]}
                        rx={lh * 0.08}
                        onClick={(e) => {
                          e.stopPropagation();
                          if (e.altKey && selectedWord && onAssignBox) onAssignBox(L.order, i, e.shiftKey);
                          else if (id) onPickWord(id);
                        }}
                      >
                        <title>
                          {id
                            ? "Click: select this word · ⌥-click: link the selected word here · ⌥⇧-click: add this box to it"
                            : "Not linked to a spoken word · ⌥-click: link the selected word here"}
                        </title>
                      </rect>
                      {sel && onResizeBox &&
                        ([0, 2] as const).map((edge) => (
                          <rect
                            key={edge}
                            className="handle"
                            x={b[edge] - lh * 0.06}
                            y={b[1]}
                            width={lh * 0.12}
                            height={b[3] - b[1]}
                            onMouseDown={(e) => {
                              e.preventDefault();
                              e.stopPropagation();
                              setDrag({ line: L.order, index: i, edge, rect: [...b0] as [number, number, number, number] });
                            }}
                          >
                            <title>Drag to move this edge of the box</title>
                          </rect>
                        ))}
                    </g>
                  );
                }),
              )}
          </svg>
        </div>
      </div>
    </div>
  );
}
