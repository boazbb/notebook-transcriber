import { useEffect, useMemo, useRef, useState } from "react";
import { fileUrl, Line, Page, Word } from "./types";

// a word's punctuation needs a look: the page and the speech model disagree, or only the page has it
export const punctDoubt = (w: Word) =>
  !!w.punct_check && (w.punct?.status === "page_only" || w.punct?.status === "differ");

const doubtful = (w: Word) => w.band !== "confident" || punctDoubt(w);

// a piece of a word's letters, with the glossary word (if any) in blue: `from` is the piece's offset in w.text
function Letters({ w, text, from }: { w: Word; text: string; from: number }) {
  const g = w.glossary_span;
  if (!g || g[1] <= from || g[0] >= from + text.length) return <>{text}</>;
  const a = Math.max(0, g[0] - from);
  const b = Math.min(text.length, g[1] - from);
  return (
    <>
      {text.slice(0, a)}
      <span className="gl-part">{text.slice(a, b)}</span>
      {text.slice(b)}
    </>
  );
}

function WordText({ w }: { w: Word }) {
  const m = w.text.match(/^([(\[]?)(.*?)([.,:;?!"'\-)\]]*)$/);
  if (!m || !w.punct) return <Letters w={w} text={w.text} from={0} />;
  const [, lead, core, tail] = m;
  const st = w.punct_check ? w.punct.status : "ok";
  return (
    <>
      {lead && <span className={`punct ${st}`}>{lead}</span>}
      <Letters w={w} text={core} from={lead.length} />
      {tail ? (
        <span className={`punct ${st}`}>{tail}</span>
      ) : st === "asr_only" || st === "differ" ? (
        <span className={`punct ${st} gap`}>{"\u2009"}</span>
      ) : null}
    </>
  );
}

interface Props {
  page: Page;
  version: number;
  selected: string | null;
  onSelect: (id: string | null) => void;
  onHover: (id: string | null) => void;
  onEdit: (id: string, text: string) => void;
  onAccept: (id: string) => void;
  onAcceptSuggestion: (w: Word) => void;
  onPunct: (id: string, mark: string) => void;
  onMoveLine: (id: string, direction: -1 | 1) => void;
  playWord: (w: Word) => void;
  playLine: (L: Line) => void;
  reRecordLine: (L: Line) => void;
  recording: boolean;
}

const BAND_TITLE: Record<string, string> = {
  confident: "",
  check: "Check this word",
  unsure: "Unsure: very low confidence, or no handwriting box matched",
};

// The transcribed text as clickable words, each linked to its handwriting.
// Keys: Tab / Shift+Tab next/previous doubtful word · ← → words · ↑ ↓ lines · A accept · S accept the
// suggestion · Enter edit · Delete remove · P play word · L play line · Esc deselect.
// Letter keys are matched by position, so they work in Hebrew too: A = ש, S = ד, P = פ, L = ך.
export function ReviewView(p: Props) {
  const { page, selected } = p;
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const boxRef = useRef<HTMLDivElement | null>(null);

  const readLines = page.lines.filter((L) => L.read);
  const order = useMemo(() => readLines.flatMap((L) => (L.words ?? []).map((w) => ({ w, L }))), [page]);
  const idx = order.findIndex((o) => o.w.id === selected);
  const cur = idx >= 0 ? order[idx] : null;
  const nDoubtful = order.filter((o) => doubtful(o.w)).length;
  // a line holding clearly more spoken words than written ones: words probably belong to a neighbouring line
  const crowded = (L: Line) => (L.words?.length ?? 0) > Math.max(L.boxes.length * 1.3, L.boxes.length + 3);

  useEffect(() => {
    if (selected) document.getElementById(`word-${selected}`)?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  function move(delta: number, onlyDoubtful = false) {
    if (!order.length) return;
    let i = idx >= 0 ? idx : delta > 0 ? -1 : order.length;
    for (let step = 0; step < order.length; step++) {
      i = (i + delta + order.length) % order.length;
      if (!onlyDoubtful || doubtful(order[i].w)) {
        p.onSelect(order[i].w.id);
        return;
      }
    }
  }

  function moveLine(delta: number) {
    if (!cur) return move(1);
    const li = readLines.findIndex((L) => L.id === cur.L.id) + delta;
    const L = readLines[Math.max(0, Math.min(readLines.length - 1, li))];
    if (L.words?.length) p.onSelect(L.words[0].id);
  }

  function startEdit(w: Word) {
    setEditing(w.id);
    setDraft(w.text);
  }

  function onKey(e: React.KeyboardEvent) {
    if (editing) return;
    // physical keys (e.code), so the shortcuts work with the Hebrew keyboard layout too (A = ש, S = ד, P = פ, L = ך)
    const k = e.key;
    const c = e.code;
    if (e.altKey && (k === "ArrowUp" || k === "ArrowDown")) {
      // the selected word and the words before it → previous line; it and the words after it → next line
      if (cur) p.onMoveLine(cur.w.id, k === "ArrowUp" ? -1 : 1);
    } else if (k === "Tab") move(e.shiftKey ? -1 : 1, true);
    else if (k === "ArrowLeft") move(1); // right-to-left text: left = next word
    else if (k === "ArrowRight") move(-1);
    else if (k === "ArrowDown") moveLine(1);
    else if (k === "ArrowUp") moveLine(-1);
    else if (k === "Escape") p.onSelect(null);
    else if (!cur) return;
    else if (k === "Enter") startEdit(cur.w);
    else if (c === "KeyA") {
      p.onAccept(cur.w.id);
      move(1, true);
    } else if (c === "KeyS" && cur.w.suggestion) p.onAcceptSuggestion(cur.w);
    else if (c === "KeyU" && cur.w.auto_from) p.onEdit(cur.w.id, cur.w.auto_from);
    else if (c === "KeyP") p.playWord(cur.w);
    else if (c === "KeyL") p.playLine(cur.L);
    else if (k === "Delete" || k === "Backspace") {
      p.onEdit(cur.w.id, "");
      move(1);
    } else return;
    e.preventDefault();
  }

  // paragraphs: consecutive read lines with the same paragraph number
  const paras: Line[][] = [];
  for (const L of readLines) {
    const last = paras[paras.length - 1];
    const pn = L.para_text ?? L.paragraph;
    if (last && (last[0].para_text ?? last[0].paragraph) === pn) last.push(L);
    else paras.push([L]);
  }

  return (
    <div className="review" ref={boxRef}>
      <div className="review-text" dir="rtl" lang="he" tabIndex={0} onKeyDown={onKey}>
        {paras.map((para, pi) => (
          <div className="para" key={pi}>
            {para.map((L) => (
              <div key={L.id} className={(cur?.L.id === L.id ? "rline current" : "rline") + (crowded(L) ? " crowded" : "")}>
                <span className="rline-no" dir="ltr">
                  {crowded(L) ? (
                    <span className="warn" title={`${(L.words ?? []).length} words for ${L.boxes.length} handwritten words: some probably belong on the line before or after (select one, ⌥↑ / ⌥↓)`}>⚠</span>
                  ) : null}
                  {L.order}
                </span>
                {(L.words ?? []).map((w) =>
                  editing === w.id ? (
                    <input
                      key={w.id}
                      className="word-edit"
                      dir="rtl"
                      autoFocus
                      value={draft}
                      size={Math.max(3, draft.length + 1)}
                      onChange={(e) => setDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          p.onEdit(w.id, draft);
                          setEditing(null);
                          boxRef.current?.querySelector<HTMLElement>(".review-text")?.focus();
                        } else if (e.key === "Escape") {
                          setEditing(null);
                          boxRef.current?.querySelector<HTMLElement>(".review-text")?.focus();
                        }
                        e.stopPropagation();
                      }}
                      onBlur={() => setEditing(null)}
                    />
                  ) : (
                    <span
                      key={w.id}
                      id={`word-${w.id}`}
                      className={[
                        "word",
                        w.band,
                        w.glossary ? "glossary" : "",
                        w.suggestion ? "has-sugg" : "",
                        w.id === selected ? "selected" : "",
                      ].join(" ")}
                      title={BAND_TITLE[w.band ?? "confident"]}
                      onMouseEnter={() => p.onHover(w.id)}
                      onMouseLeave={() => p.onHover(null)}
                      onClick={() => p.onSelect(w.id)}
                      onDoubleClick={() => startEdit(w)}
                    >
                      <WordText w={w} />
                    </span>
                  ),
                )}
              </div>
            ))}
          </div>
        ))}
      </div>
      <Inspector {...p} cur={cur} doubtful={nDoubtful} onEditStart={startEdit} />
    </div>
  );
}

// The selected word: its handwriting, its audio, its confidence and any suggestion.
function Inspector({
  page,
  version,
  cur,
  doubtful,
  playWord,
  playLine,
  onAccept,
  onAcceptSuggestion,
  onEditStart,
  reRecordLine,
  recording,
  onEdit: p_onEdit,
  onPunct: p_onPunct,
  onMoveLine: p_onMoveLine,
}: Props & { cur: { w: Word; L: Line } | null; doubtful: number; onEditStart: (w: Word) => void }) {
  if (!cur) {
    return (
      <div className="inspector muted">
        {doubtful} word{doubtful === 1 ? "" : "s"} to check (yellow / red). Click a word, or press <kbd>Tab</kbd> to
        jump to the next one. <kbd>A/ש</kbd> accept · <kbd>Enter</kbd> edit · <kbd>P/פ</kbd> play word ·{" "}
        <kbd>L/ך</kbd> play line · <kbd>⌥↑</kbd>/<kbd>⌥↓</kbd> move the word (and the ones before / after it) to the previous / next line ·{" "}
        <kbd>⌘⇧F</kbd> page finished · <kbd>U/ו</kbd> undo an automatic fix · <kbd>⌘R</kbd> re-record the line · <kbd>S/ד</kbd> accept suggestion · <kbd>Delete</kbd> remove. On the page: drag the selected box's edges to
        fix it; <kbd>⌥</kbd>-click handwriting to link the selected word to it.
      </div>
    );
  }
  const { w, L } = cur;
  // a strip of the line around the word: its neighbours, the full line height and room below for commas,
  // with the word's box (blue) and the neighbouring boxes (dashed) drawn on it
  const boxes = w.boxes.map((i) => L.boxes[i]).filter(Boolean);
  let crop = null;
  if (boxes.length) {
    const lh = page.line_height ?? 40;
    const wx0 = Math.min(...boxes.map((b) => b[0]));
    const wx1 = Math.max(...boxes.map((b) => b[2]));
    const x0 = Math.max(0, wx0 - 2.2 * lh);
    const x1 = Math.min(page.size[0], wx1 + 2.2 * lh);
    const y0 = Math.max(0, L.bbox[1] - 0.15 * lh);
    const y1 = Math.min(page.size[1], L.bbox[3] + 0.35 * lh);
    const H = 120;
    const scale = H / (y1 - y0);
    const W = (x1 - x0) * scale;
    const mine = new Set(w.boxes);
    crop = (
      <div className="crop" style={{
        width: W, height: H,
        backgroundImage: `url("${fileUrl(page.image, version)}")`,
        backgroundSize: `${page.size[0] * scale}px ${page.size[1] * scale}px`,
        backgroundPosition: `${-x0 * scale}px ${-y0 * scale}px`,
      }}>
        <svg width={W} height={H} viewBox={`${x0} ${y0} ${x1 - x0} ${y1 - y0}`}>
          {L.boxes.map((b, i) =>
            b[2] < x0 || b[0] > x1 ? null : (
              <rect key={i} className={mine.has(i) ? "cbox mine" : "cbox"} x={b[0]} y={b[1]} width={b[2] - b[0]} height={b[3] - b[1]} />
            ),
          )}
        </svg>
      </div>
    );
  }
  const reasons = [];
  if (!w.boxes.length) reasons.push("no handwriting box matched");
  if (w.p !== undefined && w.p < 0.9 && !w.edited) reasons.push(`speech confidence ${Math.round(w.p * 100)}%`);
  if (w.kind === "2:1") reasons.push("written joined with a neighbouring word");
  if (w.kind === "1:2") reasons.push("written in two pieces");
  if (w.suggestion) reasons.push("may be a sound-alike mistake");
  if (w.auto_from) reasons.push("changed automatically: the language model and your handwriting both point to it");
  return (
    <div className="inspector wide">
      <div className="insp-crop">{crop ?? <span className="muted">no handwriting box</span>}</div>
      <div className="insp-body">
        <div className="insp-word" dir="rtl">
          <span className={`word ${w.band}`}>{w.text}</span>
          {w.glossary && <span className="badge blue">glossary</span>}
          {w.edited && <span className="badge">edited</span>}
          <span className="muted small"> line {L.order}</span>
        </div>
        {reasons.length > 0 && <div className="muted small">{reasons.join(" · ")}</div>}
        {w.suggestion && (
          <div className="insp-sugg" dir="rtl">
            Maybe: <strong>{w.suggestion.to}</strong>{" "}
            <button onClick={() => onAcceptSuggestion(w)}>Use it (S/ד)</button>{" "}
            <span className="muted small" dir="ltr">
              {w.suggestion.hw == null
                ? ""
                : w.suggestion.hw > 0.5
                  ? "handwriting leans towards it"
                  : w.suggestion.hw < -0.5
                    ? "handwriting leans against it"
                    : "handwriting can't tell"}
            </span>
          </div>
        )}
        {w.punct && w.punct_check && (
          <div className="insp-punct">
            <span className="muted small">Punctuation: </span>
            <span className="small">
              on the page <strong>{w.punct.page || "none"}</strong> · speech model <strong>{w.punct.asr || "none"}</strong>
            </span>{" "}
            {Array.from(new Set([w.punct.page, w.punct.asr, ""])).map((m) => (
              <button key={m || "none"} onClick={() => p_onPunct(w.id, m.replace("(", ""))}>
                {m ? `Use “${m}”` : "No mark"}
              </button>
            ))}
          </div>
        )}
        {w.auto_from && (
          <div className="insp-sugg" dir="rtl">
            Was: <strong>{w.auto_from}</strong>{" "}
            <button onClick={() => p_onEdit(w.id, w.auto_from!)}>Undo (U/ו)</button>
          </div>
        )}
        <div className="row">
          <button onClick={() => playWord(w)} disabled={!page.audio_path}>▶ Word (P/פ)</button>
          <button onClick={() => playLine(L)} disabled={!page.audio_path}>▶ Line (L/ך)</button>
          <button onClick={() => onEditStart(w)}>Edit (Enter)</button>
          <button onClick={() => p_onMoveLine(w.id, -1)} title="This word and the ones before it on its line go to the end of the previous line">
            ↑ To previous line (⌥↑)
          </button>
          <button onClick={() => p_onMoveLine(w.id, 1)} title="This word and the ones after it on its line go to the start of the next line">
            ↓ To next line (⌥↓)
          </button>
          <button onClick={() => reRecordLine(L)} disabled={recording} title="Read this line again (⌘R)">
            ● Re-record line (⌘R)
          </button>
          {w.band !== "confident" && <button onClick={() => onAccept(w.id)}>Accept (A/ש)</button>}
        </div>
      </div>
    </div>
  );
}
