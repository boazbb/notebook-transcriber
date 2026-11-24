import { useCallback, useEffect, useRef, useState } from "react";
import { PageView } from "./PageView";
import { Suggestions } from "./Suggestions";
import { ReviewView } from "./ReviewView";
import { PagesView } from "./PagesView";
import { LayoutBar } from "./LayoutBar";
import type { LayoutTool } from "./PageView";
import { StretchPlayer } from "./player";
import { AddToGlossary, GlossaryPanel } from "./Glossary";
import { SettingsPanel } from "./Settings";
import { Recorder } from "./recorder";
import { fileUrl, Line, Page, ProjectInfo, QueueState, Settings, Suggestion, Word } from "./types";
import iconUrl from "./icon.png";

const api = window.api;

const STATUS_LABEL: Record<string, string> = {
  image: "finding lines…",
  layout: "ready to read",
  transcribed: "transcribed",
  missing: "missing",
};

export default function App() {
  const [project, setProject] = useState<ProjectInfo | null>(null);
  const [pageNo, setPageNo] = useState(1);
  const [page, setPage] = useState<Page | null>(null);
  const [version, setVersion] = useState(0);
  const [startLine, setStartLine] = useState(1);
  const [text, setText] = useState("");
  const [savedText, setSavedText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [recLine, setRecLine] = useState<number | null>(null); // re-recording one line
  const [settings, setSettings] = useState<Settings | null>(null);
  const [showGlossary, setShowGlossary] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [addWord, setAddWord] = useState<{ word: string; sentence: string } | null>(null);
  const textRef = useRef<HTMLTextAreaElement | null>(null);
  const [view, setView] = useState<"review" | "text" | "export">("review");
  const [selectedWord, setSelectedWord] = useState<string | null>(null);
  const [layoutOn, setLayoutOn] = useState(false);
  const [mode, setMode] = useState<"page" | "pages">("page"); // one page, or all pages (M6)
  const [queue, setQueue] = useState<QueueState>({ current: null, pending: [] });
  const [recPage, setRecPage] = useState<number | null>(null);
  const modeRef = useRef(mode);
  const inQueueRef = useRef<(n: number) => boolean>(() => false);
  modeRef.current = mode;
  const copyPageRef = useRef<(() => void) | null>(null);
  const undoRef = useRef<(() => void) | null>(null);
  const startRecordingRef = useRef<(() => void) | null>(null);
  const finishedRef = useRef<(() => void) | null>(null);
  const [layoutTool, setLayoutTool] = useState<LayoutTool>("select");
  const [selectedLine, setSelectedLine] = useState<string | null>(null);
  const [hoverWord, setHoverWord] = useState<string | null>(null);
  const player = useRef(new StretchPlayer());
  const [preview, setPreview] = useState<{ text: string; glossary_matches: number; keep_line_breaks: boolean } | null>(null);
  const [recInfo, setRecInfo] = useState({ seconds: 0, level: 0, spaces: 0, discards: 0 });
  const recorder = useRef<Recorder | null>(null);
  const projectRef = useRef<ProjectInfo | null>(null);
  const pageNoRef = useRef(1);
  projectRef.current = project;
  pageNoRef.current = pageNo;

  // ---------- loading ----------

  const refreshProject = useCallback(async () => {
    const p = projectRef.current;
    if (p) setProject((await api.call("project_info", { path: p.path })) as ProjectInfo);
  }, []);

  const loadPage = useCallback(async (n: number) => {
    const p = projectRef.current;
    if (!p) return;
    const pg = (await api.call("get_page", { path: p.path, page: n })) as Page;
    if (pageNoRef.current !== n) return;
    setPage(pg);
    setVersion((v) => v + 1);
    setText(pg.display_text);
    setSavedText(pg.display_text);
    const firstRead = pg.lines.find((L) => L.read)?.order;
    setStartLine(firstRead ?? pg.audio?.start_line ?? 1);
  }, []);

  const openProject = useCallback(async (path: string) => {
    setError(null);
    try {
      const info = (await api.call("open_project", { path })) as ProjectInfo;
      setProject(info);
      setPageNo(1);
    } catch (e: any) {
      setError(`Could not open the project: ${e.message}`);
    }
  }, []);

  useEffect(() => {
    api.lastProject().then((p) => {
      if (p) openProject(p);
    });
    return api.onEvent((name, params) => {
      if (name === "progress") setProgress(params.message ?? "");
      if (name === "page_updated") {
        refreshProject();
        if (params.page === pageNoRef.current) loadPage(params.page);
      }
      if (name === "queue") setQueue(params as QueueState);
      if (name === "transcribe_failed") setError(`Page ${params.page} could not be transcribed: ${params.error}`);
      if (name === "log" && params.level === "error") console.error(params.message);
    });
  }, [openProject, refreshProject, loadPage]);

  useEffect(() => {
    setPage(null);
    if (project) loadPage(pageNo);
  }, [project?.path, pageNo, loadPage]);

  useEffect(() => {
    if (project) api.call("get_settings", { path: project.path }).then((r: any) => setSettings(r));
    if (project) api.call("queue_state").then((r: any) => setQueue(r));
  }, [project?.path]);

  // "As exported" preview: refreshed when shown, after edits are saved, and when the glossary/settings change
  const refreshPreview = useCallback(async () => {
    const p = projectRef.current;
    if (!p) return;
    const n = pageNoRef.current;
    const r: any = await api.call("preview_export", { path: p.path, pages: [n] });
    if (pageNoRef.current === n) setPreview(r);
  }, []);

  // the recording, decoded once per page for word / line playback
  useEffect(() => {
    if (page?.audio_path) player.current.load(fileUrl(page.audio_path, version)).catch(() => undefined);
  }, [page?.audio_path, version]);

  useEffect(() => {
    setSelectedWord(null);
    setHoverWord(null);
  }, [pageNo]);

  useEffect(() => {
    if (view === "export" && page?.status === "transcribed" && text === savedText) refreshPreview();
  }, [view, page?.page, page?.status, savedText, text, settings, showGlossary, addWord, refreshPreview]);

  // ---------- text editing (autosave) ----------

  useEffect(() => {
    if (!project || !page || text === savedText) return;
    const t = setTimeout(async () => {
      await api.call("save_text", { path: project.path, page: page.page, text });
      setSavedText(text);
      refreshProject();
      const pg = (await api.call("get_page", { path: project.path, page: page.page })) as Page;
      if (pageNoRef.current === pg.page) setPage(pg);
    }, 700);
    return () => clearTimeout(t);
  }, [text, savedText, project, page, refreshProject]);

  // ---------- actions ----------

  async function run(label: string, fn: () => Promise<void>) {
    setBusy(label);
    setError(null);
    try {
      await fn();
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(null);
      setProgress("");
    }
  }

  async function importPdf() {
    const choice = await api.importPdfDialog();
    if (!choice) return;
    await run("Importing…", async () => {
      const info = (await api.call("create_project", choice)) as ProjectInfo;
      setProject(info);
      setPageNo(1);
    });
  }

  async function openDialog() {
    const p = await api.openProjectDialog();
    if (p) await openProject(p);
  }

  // before a recording or transcription replaces the page's words: ask if any were reviewed
  function confirmReplaceEdits() {
    if (!page) return true;
    const n = page.reviewed_words ?? 0;
    const textEdits = page.text !== undefined && page.text !== null;
    if (!n && !textEdits) return true;
    return window.confirm(
      `This page has ${n ? `${n} reviewed word${n === 1 ? "" : "s"} (corrected or accepted)` : "hand edits"}. ` +
        "Recording or transcribing again replaces the page's words.\n\n" +
        "The current words and recording are kept in the page's history folder. Continue?",
    );
  }

  async function transcribe(discarded: [number, number][] = [], spaces: number[] = [], archive = false) {
    if (!project || !page) return;
    // into the background queue: the app stays free (record the next page, review another one)
    await api.call("enqueue_transcription", {
      path: project.path,
      page: page.page,
      start_line: modeRef.current === "pages" ? 1 : startLine,
      discarded,
      spaces,
      archive,
    });
  }

  async function useAudioFile() {
    if (!project || !page || !confirmReplaceEdits()) return;
    const f = await api.audioFileDialog();
    if (!f) return;
    await run("Loading recording…", async () => {
      await api.call("use_audio_file", { path: project.path, page: page.page, source: f });
    });
    await transcribe();
  }

  async function startRecording() {
    if (!page || !confirmReplaceEdits()) return;
    setError(null);
    if (!(await api.micAccess())) {
      setError("Microphone access is blocked. Allow it in System Settings → Privacy & Security → Microphone.");
      return;
    }
    const r = new Recorder();
    try {
      await r.start();
    } catch (e: any) {
      setError(`Could not start recording: ${e.message}`);
      return;
    }
    recorder.current = r;
    setRecPage(page.page);
    setRecording(true);
  }

  async function stopRecording() {
    const r = recorder.current;
    if (!r || !page) return;
    recorder.current = null;
    setRecording(false);
    const line = recLine;
    setRecLine(null);
    const wav = await r.stop();
    if (r.seconds < (line ? 0.5 : 1)) {
      setError("The recording was too short.");
      return;
    }
    if (line && project) {
      await api.saveRecording(page.page, wav, line);
      await run(`Transcribing line ${line}…`, async () => {
        applyPage((await api.call("transcribe_line", { path: project.path, page: page.page, line })) as Page);
      });
      return;
    }
    await api.saveRecording(page.page, wav);
    await transcribe(r.discarded, r.spaces);
    setRecPage(null);
    if (modeRef.current === "pages" && project && page.page < project.pages.length) setPageNo(page.page + 1);
  }

  // [ / ] = previous / next page. Physical keys (e.code), so they work with the Hebrew layout too.
  // Ignored while typing (text fields, a word being edited), recording, or with a dialog open.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey && e.shiftKey && e.code === "KeyC" && !recording && !busy) {
        e.preventDefault();
        copyPageRef.current?.();
        return;
      }
      if (e.metaKey && e.shiftKey && e.code === "KeyF" && !recording && !busy) {
        e.preventDefault();
        finishedRef.current?.();
        return;
      }
      // ⌘Z: undo the last change to the page (typing in a text field keeps its own undo)
      if (e.metaKey && !e.shiftKey && e.code === "KeyZ" && !recording && !busy) {
        const tg = e.target as HTMLElement | null;
        if (tg && (tg.tagName === "TEXTAREA" || tg.tagName === "INPUT")) return;
        if (showGlossary || showSettings || addWord) return;
        e.preventDefault();
        undoRef.current?.();
        return;
      }
      if (e.code === "KeyR" && !e.metaKey && !e.ctrlKey && !e.altKey && modeRef.current === "pages" && !recording && !busy) {
        const tg = e.target as HTMLElement | null;
        if (tg && (tg.tagName === "TEXTAREA" || tg.tagName === "INPUT")) return;
        e.preventDefault();
        startRecordingRef.current?.();
        return;
      }
      if (e.code !== "BracketLeft" && e.code !== "BracketRight") return;
      if (e.metaKey || e.ctrlKey || e.altKey || recording || busy) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT" || t.tagName === "SELECT" || t.isContentEditable)) return;
      if (showGlossary || showSettings || addWord) return;
      const total = projectRef.current?.pages.length ?? 1;
      e.preventDefault();
      setPageNo((n) => (e.code === "BracketLeft" ? Math.max(1, n - 1) : Math.min(total, n + 1)));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [recording, busy, showGlossary, showSettings, addWord]);

  // recording meter + keys (Space = line end, Backspace = discard, Enter/Esc = stop)
  useEffect(() => {
    if (!recording) return;
    const timer = setInterval(() => {
      const r = recorder.current;
      if (r) setRecInfo({ seconds: r.seconds, level: r.level, spaces: r.spaces.length, discards: r.discarded.length });
    }, 100);
    const onKey = (e: KeyboardEvent) => {
      const r = recorder.current;
      if (!r) return;
      if (e.code === "Space") r.markLineEnd();
      else if (e.code === "Backspace") r.discardSinceMark();
      else if (e.code === "Enter" || e.code === "Escape") stopRecording();
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      clearInterval(timer);
      window.removeEventListener("keydown", onKey);
    };
  });

  async function exportText(all: boolean) {
    if (!project) return;
    const pages = all
      ? project.pages.filter((p) => p.status === "transcribed").map((p) => p.page)
      : [pageNo];
    if (!pages.length) {
      setError("No transcribed pages to export yet.");
      return;
    }
    const dest = await api.exportDialog(all ? `${project.name}.txt` : `${project.name}-page-${pageNo}.txt`);
    if (!dest) return;
    await run("Exporting…", async () => {
      const r = (await api.call("export", {
        path: project.path,
        pages,
        dest,
        keep_line_breaks: settings?.keep_line_breaks ?? false,
      })) as { path: string };
      setProgress(`Exported to ${r.path}`);
    });
  }

  async function checkSuggestions() {
    if (!project || !page) return;
    const n = page.page;
    await run("Checking…", async () => {
      // the check can change words (automatic fixes), so take the whole page
      applyPage((await api.call("check_suggestions", { path: project.path, page: n })) as Page);
    });
  }

  function applyPage(pg: Page) {
    if (pageNoRef.current !== pg.page) return;
    setPage(pg);
    setText(pg.display_text);
    setSavedText(pg.display_text);
    refreshProject();
  }

  async function editWord(id: string, newText: string) {
    if (!project || !page) return;
    applyPage((await api.call("set_word", { path: project.path, page: page.page, word_id: id, text: newText })) as Page);
  }

  async function editLayout(op: string, args: Record<string, unknown> = {}) {
    if (!project || !page) return;
    const n = page.page;
    await run("Updating the lines…", async () => {
      const pg = (await api.call("edit_layout", { path: project.path, page: n, op, args })) as Page;
      applyPage(pg);
      setVersion((v) => v + 1);
    });
    if (op === "split" || op === "add_line" || op === "add_drawing") setLayoutTool("select");
  }

  async function moveToLine(id: string, direction: -1 | 1) {
    if (!project || !page) return;
    try {
      applyPage((await api.call("move_to_line", { path: project.path, page: page.page, word_id: id, direction })) as Page);
    } catch (e: any) {
      setError(e.message);
    }
  }

  async function toggleFinished() {
    if (!project || !page || page.status !== "transcribed") return;
    const pg = (await api.call("set_finished", { path: project.path, page: page.page, finished: !page.finished })) as Page;
    applyPage(pg);
    setProgress(pg.finished ? `Page ${pg.page} marked finished.` : `Page ${pg.page} no longer marked finished.`);
    setTimeout(() => setProgress(""), 2500);
  }

  async function setPunct(id: string, mark: string) {
    if (!project || !page) return;
    applyPage((await api.call("set_punct", { path: project.path, page: page.page, word_id: id, mark })) as Page);
  }

  async function acceptWord(id: string) {
    if (!project || !page) return;
    applyPage((await api.call("set_word", { path: project.path, page: page.page, word_id: id, accept: true })) as Page);
  }

  async function acceptSuggestion(s: Suggestion) {
    if (!project || !page) return;
    applyPage(
      (await api.call("set_word", {
        path: project.path,
        page: page.page,
        word_id: s.word_id,
        text: s.to,
        suggestion: s.id,
      })) as Page,
    );
  }

  function acceptWordSuggestion(w: Word) {
    const s = page?.suggestions?.find((x) => x.id === w.suggestion?.id);
    if (s) acceptSuggestion(s);
  }

  async function assignBox(line: number, index: number, add: boolean) {
    if (!project || !page || !selectedWord) return;
    applyPage(
      (await api.call("assign_box", { path: project.path, page: page.page, word_id: selectedWord, line, index, add })) as Page,
    );
  }

  async function resizeBox(line: number, index: number, rect: [number, number, number, number]) {
    if (!project || !page) return;
    applyPage((await api.call("set_box", { path: project.path, page: page.page, line, index, rect })) as Page);
  }

  // a word's recording: the page take, or the clip of a re-recorded line
  function audioUrl(w: Word): string | undefined {
    if (!page) return undefined;
    if (!w.audio) return page.audio_path ? fileUrl(page.audio_path, version) : undefined;
    const dir = page.image.slice(0, page.image.lastIndexOf("/"));
    return fileUrl(`${dir}/${w.audio}`, w.id); // ids are new for every re-recording: no stale cached clip
  }

  function playWord(w: Word) {
    player.current.play(w.t0, w.t1, 0.3, audioUrl(w));
  }

  function playLine(L: Line) {
    const ws = L.words ?? [];
    if (ws.length) player.current.play(ws[0].t0, ws[ws.length - 1].t1, 0.15, audioUrl(ws[0]));
  }

  async function reRecordLine(line: number) {
    if (!page || recording) return;
    setError(null);
    if (!(await api.micAccess())) {
      setError("Microphone access is blocked. Allow it in System Settings → Privacy & Security → Microphone.");
      return;
    }
    const r = new Recorder();
    try {
      await r.start();
    } catch (e: any) {
      setError(`Could not start recording: ${e.message}`);
      return;
    }
    recorder.current = r;
    setRecLine(line);
    setRecording(true);
  }

  async function setSuggestionStatus(id: string, status: "accepted" | "ignored") {
    if (!project || !page) return;
    setPage((old) =>
      old ? { ...old, suggestions: old.suggestions?.map((s) => (s.id === id ? { ...s, status } : s)) } : old,
    );
    await api.call("set_suggestion", { path: project.path, page: page.page, id, status });
  }

  // the selected word (or the word at the cursor) and its line, for "Add to glossary"
  function openAddWord() {
    if (view === "review" && page) {
      for (const L of page.lines) {
        const w = L.words?.find((x) => x.id === selectedWord);
        if (w) {
          setAddWord({ word: w.text, sentence: (L.words ?? []).map((x) => x.text).join(" ") });
          return;
        }
      }
      setError("Select a word first (click it, or Tab to it).");
      return;
    }
    const ta = textRef.current;
    if (!ta || !project) return;
    let a = ta.selectionStart;
    let b = ta.selectionEnd;
    const v = ta.value;
    if (a === b) {
      while (a > 0 && !/\s/.test(v[a - 1])) a--;
      while (b < v.length && !/\s/.test(v[b])) b++;
    }
    const word = v.slice(a, b).trim();
    if (!word) {
      setError("Select a word in the text first.");
      return;
    }
    const ls = v.lastIndexOf("\n", a - 1) + 1;
    const le = v.indexOf("\n", b);
    setAddWord({ word, sentence: v.slice(ls, le < 0 ? v.length : le) });
  }

  async function toggleChapter(line: number, on: boolean) {
    if (!project || !page) return;
    await api.call("set_chapter_start", { path: project.path, page: page.page, line, on });
    setPage((old) =>
      old
        ? {
            ...old,
            chapter_starts: on
              ? [...(old.chapter_starts ?? []), line].sort((x, y) => x - y)
              : (old.chapter_starts ?? []).filter((x) => x !== line),
          }
        : old,
    );
  }

  copyPageRef.current = () => copyPage();
  undoRef.current = () => undoEdit();
  finishedRef.current = () => toggleFinished();
  startRecordingRef.current = () => {
    if (page && page.page === pageNoRef.current && !inQueueRef.current(page.page)) startRecording();
  };

  async function undoEdit() {
    if (!project || !page) return;
    const pg = (await api.call("undo", { path: project.path, page: page.page })) as Page & { undone: string | null };
    applyPage(pg);
    setVersion((v) => v + 1);
    setProgress(pg.undone ? `Undone: ${pg.undone}` : "Nothing to undo on this page.");
    setTimeout(() => setProgress(""), 2500);
  }

  // the page exactly as "Export page…" would write it, to the clipboard: plain text + right-to-left HTML
  async function copyPage() {
    if (!project || !page) return;
    const r: any = await api.call("preview_export", { path: project.path, pages: [page.page] });
    const text: string = r.text ?? "";
    if (!text.trim()) {
      setError("This page has no text to copy yet.");
      return;
    }
    const esc = (t: string) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    const paras = text.split(/\n\s*\n/).map((p) => esc(p.trim()).replace(/\n/g, "<br>"));
    const html =
      '<div dir="rtl" style="direction:rtl;text-align:right">' +
      paras.map((p) => `<p dir="rtl" style="direction:rtl;text-align:right">${p}</p>`).join("") +
      "</div>";
    await api.copy(text, html);
    setProgress(`Page ${page.page} copied to the clipboard (${r.glossary_matches ? "with glossary niqqud, " : ""}${
      r.keep_line_breaks ? "notebook line breaks" : "one line per paragraph"}).`);
    setTimeout(() => setProgress(""), 4000);
  }

  async function revertText() {
    if (!project || !page) return;
    await api.call("save_text", { path: project.path, page: page.page, text: null });
    applyPage((await api.call("get_page", { path: project.path, page: page.page })) as Page);
  }

  // ---------- render ----------

  if (!project) {
    return (
      <div className="welcome">
        <img src={iconUrl} alt="" className="welcome-icon" />
        <h1>Notebook Transcriber</h1>
        <p>Read your handwritten pages aloud; get clean Hebrew text with the notebook's lines and paragraphs.</p>
        <div className="welcome-buttons">
          <button className="primary" onClick={importPdf} disabled={!!busy}>Import a scanned PDF…</button>
          <button onClick={openDialog} disabled={!!busy}>Open a project…</button>
        </div>
        {(busy || progress) && <p className="progress">{progress || busy}</p>}
        {error && <p className="error">{error}</p>}
      </div>
    );
  }

  const summary = project.pages.find((p) => p.page === pageNo);
  const layoutReady = page && page.status !== "image";
  const edited = !!page?.edited || text !== savedText;
  const reviewOk = page?.status === "transcribed" && page.review_available;
  const inQueue = (n: number) =>
    (queue.current?.path === project.path && queue.current.page === n) ||
    queue.pending.some((j) => j.path === project.path && j.page === n);
  const pageQueued = inQueue(pageNo);
  inQueueRef.current = inQueue;
  const shownView = pageQueued
    ? "queued"
    : view === "review" && !reviewOk ? (page?.status === "transcribed" ? "blocked" : "text") : view;
  const nReadLines = page?.lines.filter((L) => L.read).length ?? 0;

  return (
    <div className="app">
      <header>
        <button
          className={mode === "pages" ? "mode-btn on" : "mode-btn"}
          onClick={() => !recording && setMode(mode === "pages" ? "page" : "pages")}
          title="All pages: record page after page while earlier ones are transcribed"
          disabled={recording}
        >{mode === "pages" ? "◧ Page" : "▦ Pages"}</button>
        <img src={iconUrl} alt="" className="header-icon" />
        <strong className="title">{project.name}</strong>
        <div className="nav">
          <button onClick={() => setPageNo((n) => Math.max(1, n - 1))} disabled={pageNo <= 1 || recording} title="Previous page ( [ )">‹</button>
          <select value={pageNo} onChange={(e) => setPageNo(Number(e.target.value))} disabled={recording}>
            {project.pages.map((p) => (
              <option key={p.page} value={p.page}>
                Page {p.page} — {inQueue(p.page) ? "transcribing…" : p.finished ? "✓ finished" : STATUS_LABEL[p.status] ?? p.status}
                {p.edited ? " (edited)" : ""}
              </option>
            ))}
          </select>
          <button
            onClick={() => setPageNo((n) => Math.min(project.pages.length, n + 1))}
            title="Next page ( ] )"
            disabled={pageNo >= project.pages.length || recording}
          >›</button>
          <span className="muted" title="PDF page numbers">of {project.pages.length}</span>
        </div>
        <div className="spacer" />
        <button onClick={() => setShowGlossary(true)} disabled={recording}>Glossary…</button>
        <button onClick={() => setShowSettings(true)} disabled={recording}>Settings…</button>
        <button
          onClick={copyPage}
          disabled={!!busy || recording || page?.status !== "transcribed"}
          title="Copy this page as it would be exported (⌘⇧C) — paste into Google Docs"
        >Copy page</button>
        <button onClick={() => exportText(false)} disabled={!!busy || recording}>Export page…</button>
        <button onClick={() => exportText(true)} disabled={!!busy || recording}>Export all…</button>
        <button onClick={openDialog} disabled={!!busy || recording}>Open…</button>
        <button onClick={importPdf} disabled={!!busy || recording}>Import PDF…</button>
      </header>

      {mode === "pages" ? (
        <main className="pages-main">
          <PagesView
            project={project}
            selected={pageNo}
            onSelect={setPageNo}
            onOpen={(n) => {
              setPageNo(n);
              setMode("page");
            }}
            queue={queue}
            recording={recording}
            recPage={recPage}
            recInfo={recInfo}
            onRecord={startRecording}
            onStop={stopRecording}
            canRecord={!!page && page.page === pageNo && !busy}
          />
          {error && <p className="error pages-error">{error}</p>}
        </main>
      ) : (
      <main>
        <section className="left">
          {page ? (
            <>
            <LayoutBar
              page={page}
              on={layoutOn}
              setOn={(on) => {
                setLayoutOn(on);
                setLayoutTool("select");
                setSelectedLine(null);
              }}
              tool={layoutTool}
              setTool={setLayoutTool}
              selectedLine={selectedLine}
              edit={editLayout}
              busy={!!busy || recording}
            />
            <PageView
              layout={
                layoutOn
                  ? {
                      selectedLine,
                      tool: layoutTool,
                      onSelectLine: setSelectedLine,
                      onSplit: (id, x) => editLayout("split", { line: id, x }),
                      onRect: (tool, r) => editLayout(tool, { rect: r }),
                      onEnds: (id, x0, x1) => editLayout("set_ends", { line: id, x0, x1 }),
                      onRemoveDrawing: (id) => editLayout("remove_drawing", { id }),
                    }
                  : undefined
              }
              page={page}
              chapterStarts={page.chapter_starts ?? []}
              startLine={startLine}
              onPickLine={(n) => !recording && setStartLine(n)}
              version={version}
              selectedWord={shownView === "review" ? selectedWord : null}
              hoverWord={shownView === "review" ? hoverWord : null}
              onPickWord={shownView === "review" ? setSelectedWord : undefined}
              onAssignBox={shownView === "review" ? assignBox : undefined}
              onResizeBox={shownView === "review" ? resizeBox : undefined}
            />
            </>
          ) : (
            <div className="placeholder">Loading page…</div>
          )}
        </section>

        <section className="right">
          <div className="controls">
            {!layoutReady && <p className="progress">Finding lines on this page… (about 10 seconds)</p>}
            {layoutReady && (
              <>
                <p className="hint">
                  Start line: <strong>{startLine}</strong> <span className="muted">(click a line on the page to change it)</span>
                  <label className="check inline" title="“First appearance” niqqud restarts here">
                    <input
                      type="checkbox"
                      checked={(page?.chapter_starts ?? []).includes(startLine)}
                      onChange={(e) => toggleChapter(startLine, e.target.checked)}
                      disabled={recording}
                    />
                    A chapter starts at line {startLine}
                  </label>
                </p>
                {!recording ? (
                  <div className="row">
                    <button className="record" onClick={startRecording} disabled={!!busy || pageQueued}>● Record</button>
                    <button onClick={useAudioFile} disabled={!!busy}>Use an audio file…</button>
                    {page?.audio_path && (
                      <button onClick={() => confirmReplaceEdits() && transcribe(page.audio?.discarded, page.audio?.spaces, true)} disabled={!!busy}>
                        Transcribe again
                      </button>
                    )}
                  </div>
                ) : (
                  <div className="recording">
                    <div className="row">
                      <span className="dot" /> Recording {fmt(recInfo.seconds)}
                      <div className="meter"><div style={{ width: `${Math.min(100, recInfo.level * 140)}%` }} /></div>
                      <button className="primary" onClick={stopRecording}>■ Stop and transcribe</button>
                    </div>
                    <p className="muted">
                      {recLine ? <>Re-recording <strong>line {recLine}</strong>: read just this line, then{" "}<kbd>Enter</kbd>. </> : null}
                      Read naturally from line {startLine}. Optional keys: <kbd>Space</kbd> line end ({recInfo.spaces}),{" "}
                      <kbd>Backspace</kbd> discard since the last mark ({recInfo.discards}), <kbd>Enter</kbd> stop.
                    </p>
                  </div>
                )}
                {page?.audio_path && !recording && (
                  <audio controls src={fileUrl(page.audio_path, version)} className="player" />
                )}
              </>
            )}
            {(busy || progress) && <p className="progress">{progress || busy}</p>}
            {error && <p className="error">{error}</p>}
          </div>

          {page && (
            <Suggestions
              page={page}
              busy={!!busy || recording}
              onAccept={acceptSuggestion}
              onIgnore={(id) => setSuggestionStatus(id, "ignored")}
              onCheck={checkSuggestions}
              onShow={(s) => {
                setView("review");
                setSelectedWord(s.word_id);
              }}
            />
          )}

          <div className="text-head">
            <span className="row">
              <span className="tabs">
                <button
                  className={view === "review" ? "tab on" : "tab"}
                  onClick={() => setView("review")}
                  disabled={page?.status !== "transcribed"}
                  title="Each word linked to its handwriting and audio; doubtful words colored"
                >Review</button>
                <button className={view === "text" ? "tab on" : "tab"} onClick={() => setView("text")}>Text</button>
                <button
                  className={view === "export" ? "tab on" : "tab"}
                  onClick={() => setView("export")}
                  disabled={page?.status !== "transcribed"}
                  title="This page exactly as Export page… writes it: niqqud from the glossary, paragraphs per Settings"
                >As exported</button>
              </span>
              {page?.status === "transcribed" && <span className="muted">{nReadLines} lines read</span>}
              {(page?.edited || summary?.edited) && <span className="badge">edited</span>}
            </span>
            <span className="row">
              {page?.status === "transcribed" && (
                <button
                  className={page.finished ? "finished-btn on" : "finished-btn"}
                  onClick={toggleFinished}
                  title="Mark this page as fully checked (⌘⇧F)"
                >{page.finished ? "✓ Finished" : "Mark finished"}</button>
              )}
              {page?.status === "transcribed" && (
                <button className="link" onClick={openAddWord} title="Select a word, then ⌘G">
                  Add to glossary (⌘G / ⌘ע)
                </button>
              )}
              {edited && page?.status === "transcribed" && (
                <button className="link" onClick={revertText}>Revert to transcription</button>
              )}
            </span>
          </div>
          {shownView === "queued" ? (
            <div className="preview queued-note">
              <p>
                <strong>
                  {queue.current?.page === pageNo ? "Being transcribed" : "Waiting to be transcribed"}
                </strong>{" "}
                <span className="muted">
                  {queue.current?.page === pageNo ? `— ${queue.current.message}` : "(other pages first)"}
                </span>
              </p>
              <p className="muted">The text appears here when it's done. You can record or review other pages meanwhile.</p>
            </div>
          ) : shownView === "blocked" ? (
            <div className="preview">
              <p>
                The review view isn't available because this page's text is empty. Type the text in{" "}
                <button className="link" onClick={() => setView("text")}>Text</button>, or{" "}
                <button className="link" onClick={revertText}>revert to the transcription</button>.
              </p>
            </div>
          ) : shownView === "review" && page ? (
            <div
              className="review-wrap"
              onKeyDown={(e) => {
                if (e.metaKey && e.code === "KeyG") {
                  e.preventDefault();
                  openAddWord();
                } else if (e.metaKey && e.code === "KeyR") {
                  e.preventDefault();
                  const L = page.lines.find((x) => x.words?.some((w) => w.id === selectedWord));
                  if (L) reRecordLine(L.order);
                  else setError("Select a word in the line to re-record first.");
                }
              }}
            >
              <ReviewView
                page={page}
                version={version}
                selected={selectedWord}
                onSelect={setSelectedWord}
                onHover={setHoverWord}
                onEdit={editWord}
                onAccept={acceptWord}
                onAcceptSuggestion={acceptWordSuggestion}
                onPunct={setPunct}
                onMoveLine={moveToLine}
                playWord={playWord}
                playLine={playLine}
                reRecordLine={(L) => reRecordLine(L.order)}
                recording={recording}
              />
            </div>
          ) : view === "export" && page?.status === "transcribed" ? (
            <div className="preview" dir="rtl" lang="he">
              <div className="preview-note" dir="ltr">
                Read-only preview of <strong>Export page…</strong>:{" "}
                {preview?.keep_line_breaks ? "the notebook's line breaks" : "one line per paragraph"}; glossary words
                with niqqud are highlighted ({preview?.glossary_matches ?? 0} glossary word
                {preview?.glossary_matches === 1 ? "" : "s"} on this page). Exporting several pages counts “first
                appearance” across all of them.
              </div>
              {(preview?.text ?? "").split("\n").map((line, i) =>
                line.trim() ? (
                  <p key={i}>
                    {line.split(" ").map((w, k) => {
                      // highlight only the pointed (glossary) word: from its first pointed letter to the end of
                      // its letters — not a prefix like ל, not punctuation
                      const start = w.search(/[\u05d0-\u05ea][\u0591-\u05C7]/);
                      if (start < 0) return <span key={k}>{w} </span>;
                      const run = w.slice(start).match(/^[\u05d0-\u05ea\u0591-\u05C7]+/)![0];
                      return (
                        <span key={k}>
                          {w.slice(0, start)}
                          <span className="gl-word">{run}</span>
                          {w.slice(start + run.length)}{" "}
                        </span>
                      );
                    })}
                  </p>
                ) : (
                  <p key={i} className="gap" />
                ),
              )}
            </div>
          ) : (
          <textarea
            ref={textRef}
            onKeyDown={(e) => {
              if (e.metaKey && e.code === "KeyG") {
                e.preventDefault();
                openAddWord();
              }
            }}
            dir="rtl"
            lang="he"
            spellCheck={false}
            value={text}
            disabled={recording || !page || page.status !== "transcribed"}
            placeholder={page?.status === "transcribed" ? "" : "Record or load an audio file to get the text of this page."}
            onChange={(e) => setText(e.target.value)}
          />
          )}
        </section>
      </main>
      )}

      {showGlossary && <GlossaryPanel projectPath={project.path} onClose={() => setShowGlossary(false)} />}
      {showSettings && (
        <SettingsPanel
          projectPath={project.path}
          onClose={(r) => {
            if (r) setSettings(r);
            setShowSettings(false);
          }}
        />
      )}
      {addWord && (
        <AddToGlossary
          projectPath={project.path}
          word={addWord.word}
          sentence={addWord.sentence}
          defaultRule={settings?.default_rule ?? "first"}
          onClose={(added) => {
            setAddWord(null);
            if (added) setProgress("Added to the glossary. It applies on export; transcribe again to use it as a spelling hint.");
          }}
        />
      )}
    </div>
  );
}

function fmt(s: number) {
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}
