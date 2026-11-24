"""JSON-RPC over stdio.

Requests (one JSON object per line on stdin):  {"id": 1, "method": "open_project", "params": {...}}
Responses (one per line on stdout):            {"id": 1, "result": ...} or {"id": 1, "error": "..."}
Events (no id):                                {"event": "progress" | "page_updated" | "log", "params": {...}}
Each request runs in its own thread; page layout runs in one background worker.
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
import traceback
from pathlib import Path

from . import project as P

_out_lock = threading.Lock()


def send(obj: dict) -> None:
    with _out_lock:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def event(name: str, params: dict) -> None:
    send({"event": name, "params": params})


# ---------- background layout worker ----------

_layout_q: "queue.PriorityQueue[tuple[int, int, str, int]]" = queue.PriorityQueue()
_seq = 0
_seq_lock = threading.Lock()


def queue_layout(proj: Path, n: int, urgent: bool = False) -> None:
    global _seq
    with _seq_lock:
        _seq += 1
        _layout_q.put((0 if urgent else 1, _seq, str(proj), n))


def _layout_worker() -> None:
    while True:
        _, _, proj_s, n = _layout_q.get()
        proj = Path(proj_s)
        try:
            if P.load_page(proj, n)["status"] != "image":
                continue  # already done (e.g. queued twice)
            event("progress", {"stage": "layout", "page": n, "message": f"Finding lines on page {n}…"})
            P.analyse(proj, n)
            event("page_updated", {"project": proj_s, "page": n})
        except Exception as e:  # keep the worker alive
            event("log", {"level": "error", "message": f"Layout failed on page {n}: {e}"})
        finally:
            if _layout_q.empty():
                event("progress", {"stage": "idle", "message": ""})


threading.Thread(target=_layout_worker, daemon=True).start()


# ---------- background transcription queue (M6) ----------
# Recording a page puts it in this queue and returns at once, so the next page can be recorded while
# earlier ones are transcribed — one at a time, in order. Events: "queue" (the whole state) after every
# change and with progress, "page_updated" when a page is done, "transcribe_failed" on an error.

_tq_lock = threading.Lock()
_tq_cond = threading.Condition(_tq_lock)
_tq_pending: list[dict] = []          # jobs waiting, in order
_tq_current: dict | None = None       # the job being transcribed
_tq_message = ""


def _queue_state() -> dict:
    return {"current": ({"path": _tq_current["path"], "page": _tq_current["page"], "message": _tq_message}
                        if _tq_current else None),
            "pending": [{"path": j["path"], "page": j["page"]} for j in _tq_pending]}


def _emit_queue() -> None:
    event("queue", _queue_state())


def _transcribe_worker() -> None:
    global _tq_current, _tq_message
    while True:
        with _tq_cond:
            while not _tq_pending:
                _tq_cond.wait()
            _tq_current = _tq_pending.pop(0)
            _tq_message = "Starting…"
        job = _tq_current
        _emit_queue()

        def progress(p: dict) -> None:
            global _tq_message
            _tq_message = p.get("message", "")
            _emit_queue()

        try:
            if job.get("archive"):
                P.archive_page(Path(job["path"]), job["page"], "before transcribing again")
            P.transcribe_page(Path(job["path"]), job["page"], job.get("start_line", 1), job.get("discarded") or [],
                              job.get("spaces") or [], progress)
            event("page_updated", {"project": job["path"], "page": job["page"], "transcribed": True})
        except Exception as e:
            event("log", {"level": "error", "message": traceback.format_exc()})
            event("transcribe_failed", {"project": job["path"], "page": job["page"], "error": str(e) or e.__class__.__name__})
        finally:
            with _tq_cond:
                _tq_current = None
                _tq_message = ""
            _emit_queue()


threading.Thread(target=_transcribe_worker, daemon=True).start()


def m_enqueue_transcription(path: str, page: int, start_line: int = 1, discarded=None, spaces=None,
                            archive: bool = False) -> dict:
    """Queue a page for transcription (its recording must be saved already). A page already waiting is
    replaced by the newer request. Returns the queue state."""
    job = {"path": path, "page": page, "start_line": start_line, "discarded": discarded or [],
           "spaces": spaces or [], "archive": archive}
    with _tq_cond:
        _tq_pending[:] = [j for j in _tq_pending if not (j["path"] == path and j["page"] == page)]
        _tq_pending.append(job)
        _tq_cond.notify()
    _emit_queue()
    return _queue_state()


def m_queue_state() -> dict:
    return _queue_state()


def m_cancel_transcription(path: str, page: int) -> dict:
    """Remove a waiting page from the queue (the one being transcribed finishes)."""
    with _tq_cond:
        _tq_pending[:] = [j for j in _tq_pending if not (j["path"] == path and j["page"] == page)]
    _emit_queue()
    return _queue_state()


# ---------- methods ----------

def m_create_project(pdf: str, dest: str) -> dict:
    proj = P.create_project(Path(pdf), Path(dest), lambda p: event("progress", p))
    info = P.project_info(proj)
    for pg in info["pages"]:
        queue_layout(proj, pg["page"])
    return info


def m_open_project(path: str) -> dict:
    proj = Path(path)
    info = P.project_info(proj)
    for pg in info["pages"]:
        if pg["status"] == "image":
            queue_layout(proj, pg["page"])
    return info


def m_project_info(path: str) -> dict:
    return P.project_info(Path(path))


def m_get_page(path: str, page: int) -> dict:
    from . import glossary as G, review as R
    proj = Path(path)
    raw = P.load_page(proj, page)
    changed = False
    if raw.get("status") in ("layout", "transcribed") and "drawings" not in raw:
        from . import handwriting as H, lines as LN, layout as LY
        ink = H.page_ink(P.page_dir(proj, page) / "page.png")
        LN.detect_drawings(raw, ink)
        inside = LY.lines_in_drawings(raw["lines"], [d["bbox"] for d in raw["drawings"]])
        for L in raw["lines"]:
            L["in_drawing"] = L["id"] in inside
        changed = True
    lines_ = [L for L in raw.get("lines", []) if not L.get("in_drawing") and not L.get("skip")]
    if raw.get("status") in ("layout", "transcribed") and len(lines_) >= 5:
        import statistics
        if statistics.median(len(L.get("boxes", [])) for L in lines_) <= 3:
            # word boxes from a bad gap threshold (fixed since): recompute them and re-place the words
            P.push_undo(proj, page, "word boxes recomputed")
            raw = P.recompute_boxes(proj, page)
    if raw.get("status") == "transcribed" and not raw.get("boxes_split"):
        R.ensure_ids(raw)
        P.split_shared_boxes(proj, page, raw)  # pages transcribed before boxes were split per word
        changed = True
    if raw.get("status") == "transcribed" and raw.get("marks_done") and raw.get("marks_version", 1) < P.MARKS_VERSION:
        R.ensure_ids(raw)
        P.push_undo(proj, page, "punctuation re-read (newer rules)")
        P.apply_page_marks(proj, page, raw)   # newer punctuation rules; the user's words and choices are kept
        changed = True
    if raw.get("status") == "transcribed" and not raw.get("marks_done"):
        R.ensure_ids(raw)
        P.apply_page_marks(proj, page, raw)  # pages transcribed before punctuation was read from the page
        for L in raw["lines"]:
            if L.get("words"):
                by_id = {w["id"]: w for w in L["words"]}
                L["asr_words"] = [by_id.get(w["id"], w) if not by_id.get(w["id"], {}).get("edited") else w
                                  for w in L.get("asr_words", [])]
        changed = True
    if raw.get("text") is not None and R.apply_free_text(raw, raw["text"]):
        changed = True  # older plain-text edits: moved onto the words when lines still match
    if changed:
        P.save_page(proj, page, raw)
    data = R.annotate(raw, G._index(P.get_glossary(proj)))
    data["edited"] = P.is_edited(data)
    data["reviewed_words"] = P.reviewed_count(data)
    data["review_available"] = data.get("text") is None
    if data["status"] == "image":
        queue_layout(proj, page, urgent=True)
    data["image"] = str(P.page_dir(proj, page) / "page.png")
    data["audio_path"] = str(P.page_dir(proj, page) / "audio" / "page.wav") \
        if (P.page_dir(proj, page) / "audio" / "page.wav").exists() else None
    data["display_text"] = P.page_text(data)
    data["generated_text"] = P.generated_text(data)
    return data


def m_audio_path(path: str, page: int) -> str:
    return str(P.page_dir(Path(path), page) / "audio" / "page.wav")


def m_use_audio_file(path: str, page: int, source: str) -> dict:
    P.set_audio_file(Path(path), page, Path(source))
    return {"ok": True}


def m_transcribe_page(path: str, page: int, start_line: int = 1, discarded=None, spaces=None,
                      archive: bool = False) -> dict:
    if archive:  # "Transcribe again" on the same recording: keep the current words first
        P.archive_page(Path(path), page, "before transcribing again")
    P.transcribe_page(Path(path), page, start_line, discarded or [], spaces or [],
                      lambda p: event("progress", p))
    event("progress", {"stage": "idle", "message": ""})
    return m_get_page(path, page)


def m_check_suggestions(path: str, page: int) -> dict:
    P.check_suggestions(Path(path), page, lambda m: event("progress", {"stage": "lm", "page": page, "message": m}))
    event("progress", {"stage": "idle", "message": ""})
    return m_get_page(path, page)


def m_set_suggestion(path: str, page: int, id: str, status: str) -> dict:
    P.push_undo(Path(path), page, "suggestion")
    P.set_suggestion(Path(path), page, id, status)
    return {"ok": True}


def m_set_word(path: str, page: int, word_id: str, text: str | None = None, accept: bool = False,
               suggestion: str | None = None) -> dict:
    P.push_undo(Path(path), page, "word edit")
    P.set_word(Path(path), page, word_id, text, accept, suggestion)
    return m_get_page(path, page)


def m_set_box(path: str, page: int, line: int, index: int, rect: list[int]) -> dict:
    P.push_undo(Path(path), page, "box")
    from . import review as R
    pg = P.load_page(Path(path), page)
    R.set_box(pg, line, index, rect)
    P.save_page(Path(path), page, pg)
    return m_get_page(path, page)


def m_assign_box(path: str, page: int, word_id: str, line: int, index: int, add: bool = False) -> dict:
    P.push_undo(Path(path), page, "word box")
    from . import review as R
    pg = P.load_page(Path(path), page)
    R.assign_box(pg, word_id, line, index, add)
    P.save_page(Path(path), page, pg)
    return m_get_page(path, page)


def m_line_audio_path(path: str, page: int, line: int) -> str:
    return str(P.page_dir(Path(path), page) / "audio" / f"line-{line:02d}.wav")


def m_transcribe_line(path: str, page: int, line: int) -> dict:
    P.transcribe_line(Path(path), page, line, lambda p: event("progress", p))
    event("progress", {"stage": "idle", "message": ""})
    return m_get_page(path, page)


def m_set_punct(path: str, page: int, word_id: str, mark: str) -> dict:
    P.push_undo(Path(path), page, "punctuation")
    from . import marks as M, review as R
    pg = P.load_page(Path(path), page)
    L, k = R.find_word(pg, word_id)
    M.set_punct(L["words"][k], mark)
    P.save_page(Path(path), page, pg)
    return m_get_page(path, page)


def m_edit_layout(path: str, page: int, op: str, args: dict | None = None) -> dict:
    P.push_undo(Path(path), page, "line edit")
    P.edit_layout(Path(path), page, op, args or {})
    return m_get_page(path, page)


def m_archive_page(path: str, page: int, reason: str = "") -> str | None:
    return P.archive_page(Path(path), page, reason or "before recording again")


def m_undo(path: str, page: int) -> dict:
    label = P.undo(Path(path), page)
    data = m_get_page(path, page)
    data["undone"] = label
    return data


def m_move_to_line(path: str, page: int, word_id: str, direction: int) -> dict:
    P.push_undo(Path(path), page, "words moved to another line")
    P.move_to_line(Path(path), page, word_id, direction)
    return m_get_page(path, page)


def m_set_finished(path: str, page: int, finished: bool) -> dict:
    """Mark a page as finished (fully checked; also trusted training material for M8)."""
    P.push_undo(Path(path), page, "finished mark")
    pg = P.load_page(Path(path), page)
    pg["finished"] = bool(finished)
    pg["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S") if finished else None
    P.save_page(Path(path), page, pg)
    return m_get_page(path, page)


def m_save_text(path: str, page: int, text: str | None) -> dict:
    P.push_undo(Path(path), page, "text edit")
    P.save_text(Path(path), page, text)
    return {"ok": True}


def m_export(path: str, pages: list[int], dest: str, keep_line_breaks: bool = False) -> dict:
    return P.export(Path(path), pages, Path(dest), keep_line_breaks)


def m_preview_export(path: str, pages: list[int]) -> dict:
    """The text of these pages exactly as an export would write it (settings + glossary)."""
    keep = P.get_settings(Path(path)).get("keep_line_breaks", False)
    text, _, matches = P.export_text(Path(path), pages, keep)
    return {"text": text, "glossary_matches": matches, "keep_line_breaks": keep}


def m_get_glossary(path: str) -> dict:
    return {"entries": P.get_glossary(Path(path)), "counts": P.glossary_counts(Path(path))}


def m_save_glossary(path: str, entries: list[dict]) -> dict:
    P.save_glossary(Path(path), entries)
    return m_get_glossary(path)


def m_propose_pointing(path: str, word: str, sentence: str = "", plain: str | None = None) -> dict:
    from . import glossary as G
    plain = plain or G.guess_plain(word, P.get_glossary(Path(path)))
    return G.propose_pointing(word, plain, sentence)


def m_get_settings(path: str) -> dict:
    return P.get_settings(Path(path))


def m_save_settings(path: str, settings: dict) -> dict:
    return P.save_settings(Path(path), settings)


def m_set_chapter_start(path: str, page: int, line: int, on: bool) -> dict:
    P.push_undo(Path(path), page, "chapter mark")
    P.set_chapter_start(Path(path), page, line, on)
    return {"ok": True}


def m_ping() -> str:
    return "pong"


METHODS = {name[2:]: fn for name, fn in globals().items() if name.startswith("m_")}


def handle(req: dict) -> None:
    rid = req.get("id")
    try:
        fn = METHODS[req["method"]]
        send({"id": rid, "result": fn(**(req.get("params") or {}))})
    except Exception as e:
        event("log", {"level": "error", "message": traceback.format_exc()})
        send({"id": rid, "error": str(e) or e.__class__.__name__})


def main() -> None:
    event("ready", {})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            event("log", {"level": "error", "message": f"bad request: {line[:200]}"})
            continue
        threading.Thread(target=handle, args=(req,), daemon=True).start()


if __name__ == "__main__":
    main()
