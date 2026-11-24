"""Project folder: one folder per notebook, plain files only.

  <Name>.ntproj/
    project.json
    source.pdf
    pages/007/page.png, page.json, audio/page.wav
    export/
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
from pathlib import Path

from . import align as align_mod
import copy

from .hebrew import norm
from . import asr, glossary, handwriting, layout, lines as lines_mod, marks, review, soundalike, spoken

SCHEMA = 1
_write_lock = threading.Lock()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def page_dir(proj: Path, n: int) -> Path:
    return proj / "pages" / f"{n:03d}"


def read_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(p: Path, data: dict) -> None:
    with _write_lock:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)


def load_page(proj: Path, n: int) -> dict:
    return read_json(page_dir(proj, n) / "page.json")


def save_page(proj: Path, n: int, page: dict) -> None:
    write_json(page_dir(proj, n) / "page.json", page)


def project_info(proj: Path) -> dict:
    info = read_json(proj / "project.json")
    pages = []
    for n in range(1, info["pages"] + 1):
        try:
            p = load_page(proj, n)
            pages.append({"page": n, "status": p["status"], "lines": len(p.get("lines", [])),
                          "has_audio": bool(p.get("audio")), "edited": is_edited(p),
                          "words": sum(len(L.get("words", [])) for L in p.get("lines", [])),
                          "reviewed": reviewed_count(p),
                          "finished": bool(p.get("finished")),
                          "image": str(page_dir(proj, n) / "page.png")})
        except FileNotFoundError:
            pages.append({"page": n, "status": "missing", "lines": 0, "has_audio": False, "edited": False})
    return {"path": str(proj), "name": info["name"], "pages": pages}


# ---------- import ----------

def create_project(pdf: Path, dest: Path, progress) -> Path:
    """Copy the PDF into a new project folder and extract page images. Layout runs separately."""
    if dest.exists() and any(dest.iterdir()):
        raise ValueError(f"Folder already exists and is not empty: {dest}")
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pdf, dest / "source.pdf")
    n_pages = layout.page_count(dest / "source.pdf")
    write_json(dest / "project.json", {"schema": SCHEMA, "name": pdf.stem, "source": "source.pdf",
                                       "pages": n_pages, "created": _now(), "settings": {}})
    for n in range(1, n_pages + 1):
        d = page_dir(dest, n)
        (d / "audio").mkdir(parents=True, exist_ok=True)
        w, h = layout.extract_page_image(dest / "source.pdf", n - 1, d / "page.png")
        save_page(dest, n, {"schema": SCHEMA, "page": n, "status": "image", "size": [w, h], "lines": []})
        progress({"stage": "import", "page": n, "pages": n_pages, "message": f"Page images {n}/{n_pages}"})
    (dest / "export").mkdir(exist_ok=True)
    return dest


def analyse(proj: Path, n: int) -> dict:
    """Run line/paragraph/word-box detection on one page (keeps any transcription only if lines are unchanged)."""
    page = load_page(proj, n)
    res = layout.analyse_page(page_dir(proj, n) / "page.png")
    page.update({"status": "layout", "size": res["size"], "line_height": res["line_height"],
                 "lines": res["lines"], "page_number_boxes": res["page_number_boxes"], "drawings": res["drawings"]})
    for key in ("asr", "text", "boxes_split"):
        page.pop(key, None)
    page.pop("audio", None) if not (page_dir(proj, n) / "audio" / "page.wav").exists() else None
    save_page(proj, n, page)
    return page


# ---------- audio + transcription ----------

def archive_page(proj: Path, n: int, reason: str) -> str | None:
    """Before a recording or transcription replaces anything: copy the page's words (page.json) and its
    recordings to pages/NNN/history/<date-time>/, so a second take never destroys the first.
    Returns the folder, or None when there is nothing to keep yet."""
    d = page_dir(proj, n)
    audio = sorted((d / "audio").glob("*.wav")) if (d / "audio").exists() else []
    page = load_page(proj, n)
    has_words = any(L.get("words") for L in page.get("lines", []))
    if not audio and not has_words:
        return None
    dest = d / "history" / time.strftime("%Y-%m-%d_%H-%M-%S")
    k = 1
    while dest.exists():
        k += 1
        dest = dest.with_name(dest.name.split("+")[0] + f"+{k}")
    (dest / "audio").mkdir(parents=True)
    shutil.copy2(d / "page.json", dest / "page.json")
    for a in audio:
        shutil.copy2(a, dest / "audio" / a.name)
    (dest / "reason.txt").write_text(reason + "\n", encoding="utf-8")
    return str(dest)


def set_audio_file(proj: Path, n: int, source: Path) -> None:
    """Use an existing recording (any format) for this page: converted to audio/page.wav.
    The previous recording and words are kept in the page's history first."""
    archive_page(proj, n, f"before using the audio file {source.name}")
    audio = asr.load_audio(source)
    asr.write_wav(page_dir(proj, n) / "audio" / "page.wav", audio)


def transcribe_page(proj: Path, n: int, start_line: int, discarded: list, spaces: list, progress) -> dict:
    page = load_page(proj, n)
    if page["status"] == "image":
        progress({"stage": "layout", "page": n, "message": "Finding lines…"})
        page = analyse(proj, n)
    wav = page_dir(proj, n) / "audio" / "page.wav"
    if not wav.exists():
        raise ValueError("This page has no recording yet.")
    commands_on = get_settings(proj).get("spoken_commands", False)
    hints = " ".join(x for x in [glossary.hotwords(get_glossary(proj)), spoken.HINTS if commands_on else None] if x) or None
    res = asr.transcribe(wav, discarded, hotwords=hints,
                         progress=lambda m: progress({"stage": "asr", "page": n, "message": m}))
    cmds = []
    if commands_on:
        res["words"], cmds = spoken.extract(res["words"])
    progress({"stage": "align", "page": n, "message": "Placing words on lines…"})
    words = [w["w"] for w in res["words"]]
    placed = align_mod.align(words, page["lines"], page["line_height"], start_line)
    by_line: dict[int, list] = {}
    seq: list[dict] = []  # word dicts in reading order (for spoken commands)
    for w, pl in zip(res["words"], placed):
        if pl is None:
            seq.append({"text": w["w"]})  # unplaced: keeps the command positions right
            continue
        d = {"text": w["w"], "boxes": pl["boxes"], "t0": w["t0"], "t1": w["t1"], "p": w["p"],
             "fit": pl.get("fit"), "kind": pl.get("kind")}
        by_line.setdefault(pl["line"], []).append(d)
        seq.append(d)
    for L in page["lines"]:
        L["words"] = by_line.get(L["order"], [])
        for k, w in enumerate(L["words"]):
            w["id"] = f"w{L['order']}_{k}"
        L.pop("para_text", None)
        L["read"] = bool(L["words"])
    split_shared_boxes(proj, n, page)
    progress({"stage": "marks", "page": n, "message": "Reading punctuation on the page…"})
    apply_page_marks(proj, n, page)
    spoken.apply(seq, cmds)  # quotes / brackets said aloud
    apply_glossary_spelling(proj, n, page)
    for L in page["lines"]:
        L["asr_words"] = copy.deepcopy(L["words"])  # kept for "Revert to transcription"
    page["audio"] = {"file": "audio/page.wav", "discarded": discarded, "spaces": spaces,
                     "start_line": start_line, "duration": res["duration"]}
    page["asr"] = {"model": res["model"], "device": res["device"], "at": _now(),
                   "unplaced": [w["w"] for w, pl in zip(res["words"], placed) if pl is None]}
    page["status"] = "transcribed"
    page.pop("text", None)  # a new transcription replaces earlier hand edits
    if get_settings(proj).get("check_soundalikes", True):
        page["suggestions"] = find_suggestions(page, lambda m: progress({"stage": "lm", "page": n, "message": m}))
        save_page(proj, n, page)
        check_handwriting(proj, n, page, lambda m: progress({"stage": "hw", "page": n, "message": m}))
    else:
        page.pop("suggestions", None)
    save_page(proj, n, page)
    return page


def split_shared_boxes(proj: Path, n: int, page: dict) -> int:
    """Give each word its own handwriting box: a box that two or more spoken words were matched to
    (written close together) is cut at the ink gaps between them. Returns the number of boxes split."""
    import cv2
    ink = handwriting.page_ink(page_dir(proj, n) / "page.png", page["line_height"])
    lh = page["line_height"]
    n_split = 0
    for L in page["lines"]:
        ws = L.get("words", [])
        if not ws:
            continue
        groups: dict[int, list[dict]] = {}
        for w in ws:
            if len(w["boxes"]) == 1:
                groups.setdefault(w["boxes"][0], []).append(w)
        new_boxes, new_index = [], {}
        for bi, box in enumerate(L["boxes"]):
            g = groups.get(bi, [])
            if len(g) >= 2:
                pieces = layout.split_box(ink, box, [max(1, len(norm(w["text"]))) for w in g], lh)
                for w, piece in zip(g, pieces):
                    w["boxes"] = [len(new_boxes)]
                    w["split_from"] = bi
                    new_boxes.append(piece)
                n_split += 1
            else:
                new_index[bi] = len(new_boxes)
                new_boxes.append(box)
        for w in ws:
            if "split_from" in w:
                w.pop("split_from")
            else:
                w["boxes"] = [new_index[b] for b in w["boxes"] if b in new_index]
        L["boxes"] = new_boxes
        # keep the transcription copy (for Revert) pointing at the same boxes
        by_id = {w["id"]: w["boxes"] for w in ws}
        for w in L.get("asr_words", []):
            if w.get("id") in by_id:
                w["boxes"] = list(by_id[w["id"]])
            else:
                w["boxes"] = [new_index[b] for b in w.get("boxes", []) if b in new_index]
    page["boxes_split"] = True
    return n_split


def transcribe_line(proj: Path, n: int, line: int, progress) -> dict:
    """Re-recorded line (audio/line-NN.wav): transcribe it alone, with the text of the lines before it as
    context and the glossary as hints, match the words to this line's boxes and replace the line's words."""
    page = load_page(proj, n)
    review.ensure_ids(page)
    L = next(x for x in page["lines"] if x["order"] == line)
    rel = f"audio/line-{line:02d}.wav"
    wav = page_dir(proj, n) / rel
    before = [w["text"] for x in page["lines"] if x["order"] < line for w in x.get("words", [])]
    commands_on = get_settings(proj).get("spoken_commands", False)
    hints = " ".join(x for x in [glossary.hotwords(get_glossary(proj)), spoken.HINTS if commands_on else None] if x) or None
    res = asr.transcribe(wav, hotwords=hints, context=" ".join(before[-40:]) or None,
                         progress=lambda m: progress({"stage": "asr", "page": n, "message": m}))
    cmds = []
    if commands_on:
        res["words"], cmds = spoken.extract(res["words"])
    words = [w["w"] for w in res["words"]]
    if not words:
        raise ValueError("No speech was recognised in the new recording of this line.")
    placed = align_mod.align(words, [L], page["line_height"], start_line=line, free_ends=False)
    stamp = time.strftime("%H%M%S")
    new = []
    for k, (w, pl) in enumerate(zip(res["words"], placed)):
        new.append({"id": f"w{line}_r{stamp}_{k}", "text": w["w"], "boxes": (pl or {}).get("boxes", []),
                    "t0": w["t0"], "t1": w["t1"], "p": w["p"], "fit": (pl or {}).get("fit"),
                    "kind": (pl or {}).get("kind"), "audio": rel})
    old_ids = {w["id"] for w in L.get("words", [])}
    L["words"] = new
    L["read"] = True
    L["audio_override"] = rel
    split_shared_boxes(proj, n, page)
    ink = handwriting.page_ink(page_dir(proj, n) / "page.png")
    marks.merge_line(L, marks.find_marks(ink, L, page["line_height"]), get_settings(proj).get("keep_asr_only_punct", True))
    spoken.apply(L["words"], cmds)
    apply_glossary_spelling(proj, n, page)
    L["asr_words"] = copy.deepcopy(L["words"])
    # sound-alike suggestions for this line only (the page is context)
    if get_settings(proj).get("check_soundalikes", True):
        sugg = [s_ for s_ in page.get("suggestions", []) if s_.get("word_id") not in old_ids]
        all_words = [(x["order"], w) for x in page["lines"] for w in x.get("words", [])]
        idx = {i for i, (o, _) in enumerate(all_words) if o == line}
        texts = [w["text"] for _, w in all_words]
        for i, r in enumerate(soundalike.correct(texts, lambda m: progress({"stage": "lm", "page": n, "message": m}), idx)):
            if r["from"]:
                w = all_words[i][1]
                sugg.append({"id": f"s{w['id']}", "line": line, "word": -1, "word_id": w["id"], "from": r["from"],
                             "to": r["text"], "gain": r["gain"], "before": " ".join(texts[max(0, i - 4):i]),
                             "after": " ".join(texts[i + 1:i + 5]), "status": "open"})
        page["suggestions"] = sugg
        check_handwriting(proj, n, page, lambda m: progress({"stage": "hw", "page": n, "message": m}))
    save_page(proj, n, page)
    return page


MARKS_VERSION = 2  # 2: one mark per gap, no shva-in-ך commas


def apply_page_marks(proj: Path, n: int, page: dict) -> None:
    """Punctuation written on the page, merged with Whisper's (stage 6)."""
    ink = handwriting.page_ink(page_dir(proj, n) / "page.png")
    keep = get_settings(proj).get("keep_asr_only_punct", True)
    for L in page["lines"]:
        if L.get("words"):
            marks.merge_line(L, marks.find_marks(ink, L, page["line_height"]), keep)
    page["marks_done"] = True
    page["marks_version"] = MARKS_VERSION


# ---------- layout editing (M5) ----------

def replace_words(proj: Path, n: int, page: dict, seq: list[dict]) -> None:
    """Put the page's words (in reading order) back on the lines after a layout edit: the same matching as
    after transcription, without speech recognition. Words keep their text, edits, audio and punctuation."""
    for L in page["lines"]:
        L["words"] = []
        L.pop("asr_words", None)
        L.pop("para_text", None)
    if not seq:
        return
    placed = align_mod.align([w["text"] for w in seq], page["lines"], page["line_height"], start_line=1)
    by_order = {L["order"]: L for L in page["lines"]}
    last = None
    for w, pl in zip(seq, placed):
        if pl is None:
            if last is None:
                continue
            w["boxes"] = []
            last["words"].append(w)
            continue
        w["boxes"], w["fit"], w["kind"] = pl["boxes"], pl.get("fit"), pl.get("kind")
        last = by_order[pl["line"]]
        last["words"].append(w)
    for L in page["lines"]:
        L["read"] = bool(L["words"])
    page["boxes_split"] = False
    split_shared_boxes(proj, n, page)
    for L in page["lines"]:
        L["asr_words"] = copy.deepcopy(L["words"])  # Revert now returns to this placement


def edit_layout(proj: Path, n: int, op: str, args: dict) -> dict:
    """Apply one layout edit (see lines.py) and keep the page consistent."""
    page = load_page(proj, n)
    review.ensure_ids(page)
    seq = [w for L in page["lines"] for w in L.get("words", [])]  # reading order before the edit
    ink = handwriting.page_ink(page_dir(proj, n) / "page.png")
    shape = ink.shape
    if op == "merge_next":
        lines_mod.merge_next(page, args["line"])
    elif op == "split":
        lines_mod.split(page, args["line"], int(args["x"]), shape)
    elif op == "move":
        lines_mod.move(page, args["line"], int(args["delta"]))
    elif op == "skip":
        lines_mod.set_skip(page, args["line"], args["skip"])
    elif op == "add_line":
        lines_mod.add_line(page, args["rect"])
    elif op == "set_ends":
        lines_mod.set_ends(page, args["line"], int(args["x0"]), int(args["x1"]), shape)
    elif op == "add_drawing":
        lines_mod.add_drawing(page, args["rect"])
    elif op == "remove_drawing":
        lines_mod.remove_drawing(page, args["id"])
    elif op == "detect_drawings":
        lines_mod.detect_drawings(page, ink)
    else:
        raise ValueError(f"Unknown layout edit: {op}")
    lines_mod.refresh(page, ink)
    if page.get("status") == "transcribed":
        replace_words(proj, n, page, seq)
    save_page(proj, n, page)
    return page


def recompute_boxes(proj: Path, n: int) -> dict:
    """Word boxes found again for the whole page (e.g. after a fix to the box finding); on a transcribed
    page the words are re-placed on them, keeping their text, edits, audio and punctuation."""
    page = load_page(proj, n)
    review.ensure_ids(page)
    seq = [w for L in page["lines"] for w in L.get("words", [])]
    lines_mod.refresh(page, handwriting.page_ink(page_dir(proj, n) / "page.png", page["line_height"]))
    if page.get("status") == "transcribed":
        replace_words(proj, n, page, seq)
    save_page(proj, n, page)
    return page


def handwriting_samples(proj: Path, skip_ids: set[str]) -> list:
    """Trusted words of every transcribed page (user-corrected, accepted, or confidently read with no
    open suggestion) as (letters, letter crops), for the handwriting model."""
    info = read_json(proj / "project.json")
    out = []
    for m in range(1, info["pages"] + 1):
        try:
            pg = load_page(proj, m)
        except FileNotFoundError:
            continue
        if pg.get("status") != "transcribed":
            continue
        review.ensure_ids(pg)
        flagged = {s_["word_id"] for s_ in pg.get("suggestions", []) if s_["status"] == "open"}
        ink = None
        for L in pg["lines"]:
            for w in L.get("words", []):
                if w["id"] in skip_ids or w["id"] in flagged or len(w["boxes"]) != 1:
                    continue
                trusted = w.get("edited") or w.get("accepted") or review.band(w, False) == "confident"
                word = handwriting.letters_of(w["text"])
                if not trusted or not 2 <= len(word) <= 10:
                    continue
                if ink is None:
                    ink = handwriting.page_ink(page_dir(proj, m) / "page.png")
                crops = handwriting.letter_crops(ink, L["boxes"][w["boxes"][0]], word)
                if crops:
                    out.append((word, crops))
    return out


def check_handwriting(proj: Path, n: int, page: dict, progress=None) -> dict:
    """Judge open letter-swap suggestions against the handwriting: apply the ones it confirms (marked,
    undoable), drop the ones it contradicts, keep the rest as suggestions with the score."""
    open_s = [s_ for s_ in page.get("suggestions", []) if s_["status"] == "open"]
    stats = {"applied": 0, "dropped": 0, "unsure": 0, "model": False}
    if not open_s:
        return stats
    if progress:
        progress("Checking suggestions against the handwriting…")
    model = handwriting.train(handwriting_samples(proj, {s_["word_id"] for s_ in open_s}))
    if model is None:
        return stats  # not enough of the notebook read yet
    stats["model"] = True
    ink = handwriting.page_ink(page_dir(proj, n) / "page.png")
    for s_ in open_s:
        try:
            L, k = review.find_word(page, s_["word_id"])
        except KeyError:
            continue
        w = L["words"][k]
        a, b = handwriting.letters_of(s_["to"]), handwriting.letters_of(w["text"])
        if len(w["boxes"]) != 1 or len(a) != len(b) or a == b:
            continue
        crops = handwriting.letter_crops(ink, L["boxes"][w["boxes"][0]], b)
        if not crops:
            continue
        sc = round(model.score(crops, a, b), 2)
        s_["hw"] = sc
        if sc >= handwriting.DECIDE:
            w["auto_from"] = w["text"]
            w["text"] = s_["to"]
            s_["status"] = "auto"
            stats["applied"] += 1
        elif sc <= -handwriting.DECIDE:
            s_["status"] = "rejected_hw"
            stats["dropped"] += 1
        else:
            stats["unsure"] += 1
    page["handwriting_check"] = stats
    return stats


def find_suggestions(page: dict, progress=None) -> list[dict]:
    """Possible sound-alike mistakes, checked with the Hebrew language model. Never applied automatically."""
    review.ensure_ids(page)
    refs = [(L["order"], int(w["id"].split("_")[1]) if w["id"].split("_")[1].isdigit() else -1)
            for L in page["lines"] for w in L.get("words", [])]
    words = [w["text"] for L in page["lines"] for w in L.get("words", [])]
    if not words:
        return []
    out = []
    for (line, k), r, i in zip(refs, soundalike.correct(words, progress), range(len(words))):
        if r["from"]:
            out.append({"id": f"s{line}_{k}", "line": line, "word": k, "word_id": f"w{line}_{k}",
                        "from": r["from"], "to": r["text"],
                        "gain": r["gain"], "before": " ".join(words[max(0, i - 4):i]),
                        "after": " ".join(words[i + 1:i + 5]), "status": "open"})
    return out


def check_suggestions(proj: Path, n: int, progress) -> dict:
    page = load_page(proj, n)
    page["suggestions"] = find_suggestions(page, progress)
    check_handwriting(proj, n, page, progress)
    save_page(proj, n, page)
    return page


def set_suggestion(proj: Path, n: int, sid: str, status: str) -> dict:
    page = load_page(proj, n)
    for s in page.get("suggestions", []):
        if s["id"] == sid:
            s["status"] = status
    save_page(proj, n, page)
    return page


# ---------- text ----------

def generated_text(page: dict) -> str:
    """Read lines with the notebook's line breaks; a blank line between paragraphs."""
    out, para = [], None
    for L in page.get("lines", []):
        if not L.get("read"):
            continue
        p = L.get("para_text", L.get("paragraph"))
        if para is not None and p != para:
            out.append("")
        para = p
        out.append(" ".join(w["text"] for w in L["words"]))
    return "\n".join(out)


def page_text(page: dict) -> str:
    return page["text"] if page.get("text") is not None else generated_text(page)


def rematch_lines(proj: Path, n: int, page: dict, orders: set[int]) -> None:
    """After words were added or removed on these lines: match each line's words to its boxes again, so
    the words after the edit point at the right handwriting and new words get a box."""
    lh = page["line_height"]
    for L in page["lines"]:
        if L["order"] not in orders or not L.get("words") or L.get("skip") or L.get("in_drawing"):
            continue
        placed = align_mod.align([w["text"] for w in L["words"]], [L], lh, start_line=L["order"], free_ends=False)
        for w, pl in zip(L["words"], placed):
            if pl and pl["line"] == L["order"]:
                w["boxes"], w["fit"], w["kind"] = pl["boxes"], pl.get("fit"), pl.get("kind")
            else:
                w["boxes"] = []
    split_shared_boxes(proj, n, page)


def _word_counts(page: dict) -> dict[int, int]:
    return {L["order"]: len(L.get("words", [])) for L in page.get("lines", [])}


# ---------- undo ----------

UNDO_DEPTH = 50


def push_undo(proj: Path, n: int, label: str) -> None:
    """Keep the page as it is now, before an edit (pages/NNN/undo/, the last 50 steps)."""
    d = page_dir(proj, n) / "undo"
    d.mkdir(exist_ok=True)
    stamp = f"{time.time_ns():020d}"  # sorts in the order the edits happened
    shutil.copy2(page_dir(proj, n) / "page.json", d / f"{stamp}.json")
    (d / f"{stamp}.txt").write_text(label, encoding="utf-8")
    steps = sorted(d.glob("*.json"))
    for old in steps[:-UNDO_DEPTH]:
        old.unlink()
        old.with_suffix(".txt").unlink(missing_ok=True)


def undo(proj: Path, n: int) -> str | None:
    """Restore the page as it was before the last edit. Returns what was undone, or None."""
    d = page_dir(proj, n) / "undo"
    steps = sorted(d.glob("*.json")) if d.exists() else []
    if not steps:
        return None
    last = steps[-1]
    label = last.with_suffix(".txt").read_text(encoding="utf-8") if last.with_suffix(".txt").exists() else "edit"
    shutil.copy2(last, page_dir(proj, n) / "page.json")
    last.unlink()
    last.with_suffix(".txt").unlink(missing_ok=True)
    return label


def save_text(proj: Path, n: int, text: str | None) -> dict:
    """Free-text edit of a page. Matched back to the words when the lines still correspond
    (keeps every word linked to its handwriting); otherwise stored as a plain-text override.
    text=None reverts to the transcription."""
    page = load_page(proj, n)
    before = _word_counts(page)
    if text is None:
        review.revert(page)
    elif text != generated_text(page) and not review.apply_free_text(page, text):
        page["text"] = text
    elif text is not None:
        after = _word_counts(page)
        rematch_lines(proj, n, page, {o for o, c in after.items() if before.get(o) != c})
    save_page(proj, n, page)
    return page


def set_word(proj: Path, n: int, word_id: str, text: str | None = None, accept: bool = False,
             suggestion: str | None = None) -> dict:
    page = load_page(proj, n)
    before = _word_counts(page)
    review.set_word(page, word_id, text, accept)
    after = _word_counts(page)
    changed = {o for o, c in after.items() if before.get(o) != c}
    if changed:
        rematch_lines(proj, n, page, changed)  # a word added / removed: re-point the line's words
    if suggestion:
        for s in page.get("suggestions", []):
            if s["id"] == suggestion:
                s["status"] = "accepted"
    save_page(proj, n, page)
    return page


def move_to_line(proj: Path, n: int, word_id: str, direction: int) -> dict:
    page = load_page(proj, n)
    a, b = review.move_to_line(page, word_id, direction)
    rematch_lines(proj, n, page, {a, b})
    save_page(proj, n, page)
    return page


def reviewed_count(page: dict) -> int:
    """Words the user corrected, accepted or chose punctuation for."""
    return sum(1 for L in page.get("lines", []) for w in L.get("words", [])
               if w.get("edited") or w.get("accepted") or (w.get("punct") or {}).get("status") == "user")


def is_edited(page: dict) -> bool:
    return page.get("text") is not None or reviewed_count(page) > 0


SENTENCE_END = tuple(".?!:\"'״׳)…")


# ---------- settings ----------

DEFAULT_SETTINGS = {"default_rule": "first", "check_soundalikes": True, "keep_line_breaks": False,
                    "keep_asr_only_punct": True, "spoken_commands": False}


def get_settings(proj: Path) -> dict:
    return {**DEFAULT_SETTINGS, **read_json(proj / "project.json").get("settings", {})}


def save_settings(proj: Path, settings: dict) -> dict:
    info = read_json(proj / "project.json")
    info["settings"] = {k: settings.get(k, v) for k, v in DEFAULT_SETTINGS.items()}
    write_json(proj / "project.json", info)
    return info["settings"]


# ---------- glossary + chapters ----------

def get_glossary(proj: Path) -> list[dict]:
    return read_json(proj / "project.json").get("glossary", [])


def apply_glossary_spelling(proj: Path, n: int, page: dict) -> int:
    """Glossary "also heard as" variants in the words → the glossary spelling (words the user edited are
    left alone; the original stays in w["glossary_from"]). Returns the number of words changed."""
    idx = glossary._index(get_glossary(proj))
    changed = 0
    for L in page.get("lines", []):
        for w in L.get("words", []):
            if w.get("edited"):
                continue
            new = glossary.correct_spelling(w["text"], idx)
            if new and new != w["text"]:
                w.setdefault("glossary_from", w["text"])
                w["text"] = new
                changed += 1
    return changed


def save_glossary(proj: Path, entries: list[dict]) -> list[dict]:
    info = read_json(proj / "project.json")
    clean = []
    for e in entries:
        if not e.get("plain", "").strip():
            continue
        base = glossary.new_entry(e["plain"], e.get("pointed", ""), e.get("rule", "first"),
                                  e.get("note", ""), [v for v in e.get("variants", []) if v.strip()])
        base["id"] = e.get("id") or base["id"]
        clean.append(base)
    info["glossary"] = clean
    write_json(proj / "project.json", info)
    for m in range(1, info["pages"] + 1):  # new spellings apply to the pages already transcribed
        try:
            pg = load_page(proj, m)
        except FileNotFoundError:
            continue
        if pg.get("status") == "transcribed" and apply_glossary_spelling(proj, m, pg):
            save_page(proj, m, pg)
    return clean


def set_chapter_start(proj: Path, n: int, line: int, on: bool) -> dict:
    page = load_page(proj, n)
    starts = set(page.get("chapter_starts", []))
    starts.add(line) if on else starts.discard(line)
    page["chapter_starts"] = sorted(starts)
    save_page(proj, n, page)
    return page


def page_blocks(page: dict) -> list[list[tuple[str, bool]]]:
    """The page text as paragraphs of (line text, starts a chapter). Text lines map to read lines in order."""
    read = [L["order"] for L in page.get("lines", []) if L.get("read")]
    starts = set(page.get("chapter_starts", []))
    blocks, k = [], 0
    for block in re.split(r"\n\s*\n", page_text(page).strip()):
        lines = []
        for l in block.splitlines():
            if not l.strip():
                continue
            order = read[k] if k < len(read) else None
            lines.append((l.strip(), order in starts))
            k += 1
        if lines:
            blocks.append(lines)
    return blocks


def export_text(proj: Path, pages: list[int], keep_line_breaks: bool = False,
                apply_glossary: bool = True) -> tuple[str, int, int]:
    """The export as a string: (text, pages with text, glossary matches).
    Default: one line per paragraph, a blank line between paragraphs. A paragraph that runs over a page
    break (previous page doesn't end a sentence) is joined. Glossary words get their niqqud according to
    their rule; "first appearance" restarts at each chapter.

    "First appearance" counts from the start of the notebook (or the last chapter mark), not from the start
    of what is exported: pages before the first exported one are read first, so copying or exporting a
    single page doesn't repeat the niqqud on every page."""
    ap = glossary.Applier(get_glossary(proj) if apply_glossary else [])
    if apply_glossary and pages:
        for m in range(1, min(pages)):   # earlier pages: only to know what has appeared already
            try:
                for block in page_blocks(load_page(proj, m)):
                    for text, chapter in block:
                        if chapter:
                            ap.new_chapter()
                        ap.line(text)
            except FileNotFoundError:
                continue
        ap.counts.clear()
    paras: list[list[str]] = []          # each paragraph: its lines
    pages_with_text = 0
    for n in pages:
        blocks = page_blocks(load_page(proj, n))
        if not blocks:
            continue
        pages_with_text += 1
        for bi, block in enumerate(blocks):
            lines = []
            for text, chapter in block:
                if chapter:
                    ap.new_chapter()
                lines.append(ap.line(text))
            continues = (bi == 0 and paras and not block[0][1]
                         and not paras[-1][-1].endswith(SENTENCE_END))
            if continues and not keep_line_breaks:
                paras[-1] += lines
            else:
                paras.append(lines)
    sep = "\n" if keep_line_breaks else " "
    return "\n\n".join(sep.join(p) for p in paras), pages_with_text, sum(ap.counts.values())


def export(proj: Path, pages: list[int], dest: Path, keep_line_breaks: bool = False,
           apply_glossary: bool = True) -> dict:
    body, n_pages, matches = export_text(proj, pages, keep_line_breaks, apply_glossary)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(body + "\n", encoding="utf-8")
    return {"path": str(dest), "pages_with_text": n_pages, "glossary_matches": matches}


def glossary_counts(proj: Path) -> dict[str, int]:
    """How often each glossary entry appears in the transcribed text (all pages)."""
    info = read_json(proj / "project.json")
    ap = glossary.Applier(info.get("glossary", []))
    for n in range(1, info["pages"] + 1):
        try:
            for block in page_blocks(load_page(proj, n)):
                for text, _ in block:
                    ap.line(text)
        except FileNotFoundError:
            pass
    return ap.counts
