import { useEffect, useState } from "react";
import { GlossaryEntry, Rule } from "./types";

const api = window.api;

export const RULE_LABEL: Record<Rule, string> = {
  first: "First appearance",
  every: "Every appearance",
  never: "Never (spelling only)",
};

function RuleSelect({ value, onChange }: { value: Rule; onChange: (r: Rule) => void }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value as Rule)}>
      {(Object.keys(RULE_LABEL) as Rule[]).map((r) => (
        <option key={r} value={r}>{RULE_LABEL[r]}</option>
      ))}
    </select>
  );
}

// ---------- the glossary editor ----------

interface PanelProps {
  projectPath: string;
  onClose: () => void;
}

export function GlossaryPanel({ projectPath, onClose }: PanelProps) {
  const [entries, setEntries] = useState<GlossaryEntry[] | null>(null);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    api.call("get_glossary", { path: projectPath }).then((r: any) => {
      setEntries(r.entries);
      setCounts(r.counts);
    });
  }, [projectPath]);

  const update = (i: number, patch: Partial<GlossaryEntry>) => {
    setEntries((es) => es!.map((e, k) => (k === i ? { ...e, ...patch } : e)));
    setDirty(true);
  };

  async function save() {
    const r: any = await api.call("save_glossary", { path: projectPath, entries });
    setEntries(r.entries);
    setCounts(r.counts);
    setDirty(false);
  }

  return (
    <div className="modal-backdrop" onClick={(e) => e.target === e.currentTarget && !dirty && onClose()}>
      <div className="modal wide">
        <div className="modal-head">
          <h2>Glossary</h2>
          <span className="muted">
            Names and invented terms. They get niqqud on export, are recognised behind prefixes (ו ה ב כ ל מ ש), and are
            given to the speech model as spelling hints (for new transcriptions).
          </span>
        </div>
        {entries === null ? (
          <p className="muted">Loading…</p>
        ) : (
          <div className="gl-table-wrap">
            <table className="gl-table">
              <thead>
                <tr>
                  <th>Word</th>
                  <th>With niqqud</th>
                  <th>Add niqqud</th>
                  <th>Also heard as (comma-separated)</th>
                  <th>Note</th>
                  <th title="Appearances in the transcribed pages">#</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {entries.map((e, i) => (
                  <tr key={e.id}>
                    <td><input dir="rtl" value={e.plain} onChange={(x) => update(i, { plain: x.target.value })} /></td>
                    <td><input dir="rtl" className="pointed" value={e.pointed} onChange={(x) => update(i, { pointed: x.target.value })} /></td>
                    <td><RuleSelect value={e.rule} onChange={(r) => update(i, { rule: r })} /></td>
                    <td>
                      <input
                        dir="rtl"
                        value={e.variants.join(", ")}
                        onChange={(x) => update(i, { variants: x.target.value.split(",").map((v) => v.trim()) })}
                      />
                    </td>
                    <td><input value={e.note} onChange={(x) => update(i, { note: x.target.value })} /></td>
                    <td className="muted">{counts[e.id] ?? 0}</td>
                    <td>
                      <button
                        className="link"
                        onClick={() => {
                          setEntries((es) => es!.filter((_, k) => k !== i));
                          setDirty(true);
                        }}
                      >Delete</button>
                    </td>
                  </tr>
                ))}
                {!entries.length && (
                  <tr>
                    <td colSpan={7} className="muted">
                      Empty. Select a word in the text and press “Add to glossary” (⌘G), or add a row here.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
        <div className="modal-foot">
          <button
            onClick={() => {
              setEntries((es) => [
                ...(es ?? []),
                { id: "", plain: "", pointed: "", rule: "first", variants: [], note: "" },
              ]);
              setDirty(true);
            }}
          >+ Add a row</button>
          <div className="spacer" />
          <button onClick={onClose}>{dirty ? "Discard changes" : "Close"}</button>
          <button className="primary" onClick={save} disabled={!dirty}>Save</button>
        </div>
      </div>
    </div>
  );
}

// ---------- add one word (from the text) ----------

interface AddProps {
  projectPath: string;
  word: string;
  sentence: string;
  defaultRule: Rule;
  onClose: (added: boolean) => void;
}

export function AddToGlossary({ projectPath, word, sentence, defaultRule, onClose }: AddProps) {
  const [plain, setPlain] = useState("");
  const [pointed, setPointed] = useState("");
  const [rule, setRule] = useState<Rule>(defaultRule);
  const [variants, setVariants] = useState("");
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function propose(p?: string) {
    setLoading(true);
    try {
      const r: any = await api.call("propose_pointing", { path: projectPath, word, sentence, plain: p });
      setPlain(r.plain);
      setPointed(r.pointed);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    propose();
  }, []);

  async function add() {
    const r: any = await api.call("get_glossary", { path: projectPath });
    const entry = {
      id: "",
      plain,
      pointed,
      rule,
      note,
      variants: variants.split(",").map((v) => v.trim()).filter(Boolean),
    };
    await api.call("save_glossary", { path: projectPath, entries: [...r.entries, entry] });
    onClose(true);
  }

  return (
    <div className="modal-backdrop">
      <div className="modal">
        <div className="modal-head">
          <h2>Add to glossary</h2>
          <span className="muted">Selected: <span dir="rtl">{word}</span></span>
        </div>
        <div className="form">
          <label>Word (without prefixes like ו, ל, ה)</label>
          <div className="row">
            <input dir="rtl" value={plain} onChange={(e) => setPlain(e.target.value)} />
            <button onClick={() => propose(plain)} disabled={loading || !plain}>Propose niqqud again</button>
          </div>
          <label>With niqqud {loading && <span className="muted">— proposing…</span>}</label>
          <input dir="rtl" className="pointed big" value={pointed} onChange={(e) => setPointed(e.target.value)} />
          <span className="muted small">Proposed by the Nakdan model; check and fix it if needed.</span>
          <label>Add niqqud</label>
          <RuleSelect value={rule} onChange={setRule} />
          <label>Also heard as <span className="muted">(misspellings to correct, comma-separated)</span></label>
          <input dir="rtl" value={variants} onChange={(e) => setVariants(e.target.value)} />
          <label>Note</label>
          <input value={note} onChange={(e) => setNote(e.target.value)} />
          {error && <p className="error">{error}</p>}
        </div>
        <div className="modal-foot">
          <div className="spacer" />
          <button onClick={() => onClose(false)}>Cancel</button>
          <button className="primary" onClick={add} disabled={!plain.trim() || loading}>Add</button>
        </div>
      </div>
    </div>
  );
}
