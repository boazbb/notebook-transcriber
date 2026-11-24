import { useEffect, useState } from "react";
import { RULE_LABEL } from "./Glossary";
import { Rule, Settings } from "./types";

const api = window.api;

interface Props {
  projectPath: string;
  onClose: (s: Settings | null) => void;
}

export function SettingsPanel({ projectPath, onClose }: Props) {
  const [s, setS] = useState<Settings | null>(null);
  useEffect(() => {
    api.call("get_settings", { path: projectPath }).then((r: any) => setS(r));
  }, [projectPath]);

  async function save() {
    const r = (await api.call("save_settings", { path: projectPath, settings: s })) as Settings;
    onClose(r);
  }

  return (
    <div className="modal-backdrop">
      <div className="modal">
        <div className="modal-head">
          <h2>Settings</h2>
          <span className="muted">For this notebook.</span>
        </div>
        {s && (
          <div className="form">
            <label>New glossary words get niqqud on</label>
            <select value={s.default_rule} onChange={(e) => setS({ ...s, default_rule: e.target.value as Rule })}>
              {(Object.keys(RULE_LABEL) as Rule[]).map((r) => (
                <option key={r} value={r}>{RULE_LABEL[r]}</option>
              ))}
            </select>
            <span className="muted small">“First appearance” restarts at each chapter mark.</span>
            <label className="check">
              <input
                type="checkbox"
                checked={s.check_soundalikes}
                onChange={(e) => setS({ ...s, check_soundalikes: e.target.checked })}
              />
              After transcribing, look for sound-alike mistakes (about 12 s per page)
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={s.keep_asr_only_punct}
                onChange={(e) => setS({ ...s, keep_asr_only_punct: e.target.checked })}
              />
              Keep punctuation the speech model adds where the page shows no mark (for new transcriptions)
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={s.spoken_commands}
                onChange={(e) => setS({ ...s, spoken_commands: e.target.checked })}
              />
              <span>
                Spoken quotes and brackets: say <strong>מרכאות</strong> where a quote mark goes and{" "}
                <strong>סוגריים</strong> where a bracket goes (the first opens, the next closes). Not read from the
                page: in handwriting they look like ' and ל.
              </span>
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={s.keep_line_breaks}
                onChange={(e) => setS({ ...s, keep_line_breaks: e.target.checked })}
              />
              Export with the notebook's line breaks (otherwise one line per paragraph)
            </label>
          </div>
        )}
        <div className="modal-foot">
          <div className="spacer" />
          <button onClick={() => onClose(null)}>Cancel</button>
          <button className="primary" onClick={save} disabled={!s}>Save</button>
        </div>
      </div>
    </div>
  );
}
