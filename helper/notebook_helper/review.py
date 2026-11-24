"""Stage 8 + review: confidence bands per word, and word-level edits that keep each word linked to its box.

Words are the source of truth for a transcribed page. Each word: {"id", "text", "boxes": [box indices in
its line], "t0", "t1", "p", "fit", "kind", and after review "edited" / "accepted" / "inserted"}.
Free-text edits (the plain Text view) are matched back to the words line by line, so a corrected word
keeps its box and audio. Only when the user changes the number of lines does the page fall back to a
plain-text override (page["text"]) and the review view is unavailable.
"""
from __future__ import annotations

import copy
import re

from .hebrew import norm

# band thresholds (spec stage 8; tunable)
P_UNSURE, P_CHECK, FIT_CHECK = 0.6, 0.9, 0.6


def ensure_ids(page: dict) -> None:
    """Give every word a stable id (older pages have none); keep a copy of the transcription for Revert."""
    for L in page.get("lines", []):
        for k, w in enumerate(L.get("words", [])):
            w.setdefault("id", f"w{L['order']}_{k}")
        if L.get("words") and "asr_words" not in L:
            L["asr_words"] = copy.deepcopy(L["words"])
    for s in page.get("suggestions", []):
        s.setdefault("word_id", f"w{s['line']}_{s['word']}")


def band(w: dict, open_suggestion: bool) -> str:
    if w.get("accepted") or w.get("edited"):
        return "confident"
    if w.get("auto_from"):
        return "check"  # changed automatically (language model + handwriting): shown until accepted
    if not w.get("boxes"):
        return "unsure"
    if w.get("p", 1) < P_UNSURE:
        return "unsure"
    if open_suggestion or w.get("p", 1) < P_CHECK or (w.get("fit") or 0) > FIT_CHECK \
            or w.get("kind") in ("2:1", "1:2"):
        return "check"
    return "confident"


def annotate(page: dict, glossary_idx: dict | None = None) -> dict:
    """Add display-only fields (band, glossary flag, suggestion) to a page copy for the app."""
    from .glossary import match
    page = copy.deepcopy(page)
    ensure_ids(page)
    open_sugg = {s["word_id"]: s for s in page.get("suggestions", []) if s["status"] == "open"}
    for L in page.get("lines", []):
        for w in L.get("words", []):
            s = open_sugg.get(w["id"])
            if s and norm(s["from"]) != norm(w["text"]):
                s = None  # the word was edited since: the suggestion no longer applies
            w["band"] = band(w, bool(s))
            ps = (w.get("punct") or {}).get("status")
            w["punct_check"] = ps in ("page_only", "asr_only", "differ") and not w.get("edited") and not w.get("accepted")
            w["suggestion"] = {"id": s["id"], "to": s["to"], "hw": s.get("hw")} if s else None
            w["glossary"] = False
            w.pop("glossary_span", None)
            if glossary_idx:
                from .glossary import _split_punct
                pre, core, _post = _split_punct(w["text"])
                m = match(core, glossary_idx) if core else None
                if m:
                    # only the glossary word itself (not a prefix like ל, not punctuation)
                    w["glossary"] = True
                    w["glossary_span"] = [len(pre) + len(m[1]), len(pre) + len(core)]
        L.pop("asr_words", None)
    return page


def _bare(token: str) -> str:
    return re.sub(r"^[^א-ת]+|[^א-ת֑-ׇ]+$", "", token)


def find_word(page: dict, word_id: str) -> tuple[dict, int]:
    for L in page["lines"]:
        for k, w in enumerate(L.get("words", [])):
            if w["id"] == word_id:
                return L, k
    raise KeyError(f"No word {word_id} on this page")


def _new_id(page: dict, line: int) -> str:
    existing = {w["id"] for L in page["lines"] for w in L.get("words", [])}
    k = 0
    while f"w{line}_n{k}" in existing:
        k += 1
    return f"w{line}_n{k}"


def set_word(page: dict, word_id: str, text: str | None = None, accept: bool = False) -> None:
    """Edit (text), delete (text == ""), or accept a word as it is."""
    ensure_ids(page)
    L, k = find_word(page, word_id)
    w = L["words"][k]
    if text is not None:
        tokens = text.split()
        if not tokens:
            del L["words"][k]
        else:
            w["text"] = tokens[0]
            w["edited"] = True
            for extra in reversed(tokens[1:]):  # typing two words splits the word (same box, no audio of its own)
                L["words"].insert(k + 1, {"id": _new_id(page, L["order"]), "text": extra, "boxes": list(w["boxes"]),
                                          "t0": w["t0"], "t1": w["t1"], "p": 1.0, "edited": True, "inserted": True})
    if accept:
        w["accepted"] = True
    page.pop("text", None)


def _align_tokens(old: list[str], new: list[str]) -> list[tuple[int | None, int | None]]:
    a, b = [norm(x) for x in old], [norm(x) for x in new]
    n, m = len(a), len(b)
    D = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        D[i][0] = i
    for j in range(m + 1):
        D[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            D[i][j] = min(D[i - 1][j] + 1, D[i][j - 1] + 1, D[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
    i, j, path = n, m, []
    while i or j:
        if i and j and D[i][j] == D[i - 1][j - 1] + (a[i - 1] != b[j - 1]):
            path.append((i - 1, j - 1)); i -= 1; j -= 1
        elif i and D[i][j] == D[i - 1][j] + 1:
            path.append((i - 1, None)); i -= 1
        else:
            path.append((None, j - 1)); j -= 1
    return path[::-1]


def apply_free_text(page: dict, text: str) -> bool:
    """Match an edited plain text back to the words, over the whole page at once, so the text's line
    breaks don't matter: every word keeps its notebook line (joining or splitting lines in the Text view
    changes nothing). A changed word keeps its box and audio; an added word joins the line of the word
    before it; a removed word is dropped. Blank lines still set the paragraphs (per notebook line).
    Returns False only when the text has no words at all (then the caller keeps it as plain text)."""
    ensure_ids(page)
    read = [L for L in page["lines"] if L.get("read")]
    tokens: list[tuple[str, int]] = []  # (token, paragraph block index)
    for bi, block in enumerate(re.split(r"\n\s*\n", text.strip())):
        tokens += [(t, bi) for t in block.split()]
    if not tokens or not read:
        return False
    old = [(L, w) for L in read for w in L["words"]]
    new_words: dict[int, list] = {id(L): [] for L in read}
    first_block: dict[int, int] = {}
    last_line = read[0]
    for i, j in _align_tokens([w["text"] for _, w in old], [t for t, _ in tokens]):
        if j is None:
            continue  # removed
        tok, bi = tokens[j]
        if i is not None:
            L, w = old[i]
            if w["text"] != tok:
                w = {**w, "text": tok, "edited": True}
        else:
            L = last_line
            prev = new_words[id(L)][-1] if new_words[id(L)] else {"t1": L["words"][0]["t0"] if L["words"] else 0}
            w = {"id": _new_id(page, L["order"]), "text": tok, "boxes": [], "t0": prev["t1"], "t1": prev["t1"],
                 "p": 1.0, "edited": True, "inserted": True}
        new_words[id(L)].append(w)
        first_block.setdefault(id(L), bi)
        last_line = L
    for L in read:
        L["words"] = new_words[id(L)]
        if id(L) in first_block:
            L["para_text"] = first_block[id(L)] + 1
    page.pop("text", None)
    return True


def revert(page: dict) -> None:
    ensure_ids(page)
    for L in page["lines"]:
        if "asr_words" in L:
            L["words"] = copy.deepcopy(L["asr_words"])
        L.pop("para_text", None)
    page.pop("text", None)
    for s in page.get("suggestions", []):
        s["status"] = "open" if s["status"] in ("accepted", "auto") else s["status"]


def set_box(page: dict, line: int, index: int, rect: list[int]) -> None:
    """Move / resize one word box (page pixel coordinates)."""
    L = next(L for L in page["lines"] if L["order"] == line)
    x0, y0, x1, y1 = (int(v) for v in rect)
    if x1 - x0 < 4 or y1 - y0 < 4:
        raise ValueError("Box too small")
    L["boxes"][index] = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]


def assign_box(page: dict, word_id: str, line: int, index: int, add: bool = False) -> None:
    """Link a word to a box (add=True: to this box as well). If the box is on another line, the word
    moves to that line, placed in reading order by the box position (right to left)."""
    ensure_ids(page)
    L_from, k = find_word(page, word_id)
    L_to = next(L for L in page["lines"] if L["order"] == line)
    w = L_from["words"][k]
    if L_to is L_from:
        w["boxes"] = sorted(set(w["boxes"]) | {index}) if add else [index]
    else:
        del L_from["words"][k]
        w["boxes"] = [index]
        x = L_to["boxes"][index][2]  # right edge: reading order is right to left
        pos = 0
        for j, other in enumerate(L_to.get("words", [])):
            if other["boxes"] and L_to["boxes"][other["boxes"][0]][2] > x:
                pos = j + 1
        L_to.setdefault("words", []).insert(pos, w)
        L_to["read"] = True
        L_from["read"] = bool(L_from["words"])
    w["accepted"] = True
    page.pop("text", None)


def move_to_line(page: dict, word_id: str, direction: int) -> tuple[int, int]:
    """Fix a word placed on the wrong line. direction -1: this word and the words before it on its line go
    to the end of the previous line; +1: this word and the words after it go to the start of the next line.
    Lines that are skipped or in a drawing are passed over. Returns (from line, to line) orders."""
    ensure_ids(page)
    L, k = find_word(page, word_id)
    usable = [x for x in page["lines"] if not x.get("skip") and not x.get("in_drawing")]
    i = next(j for j, x in enumerate(usable) if x is L)
    j = i + direction
    if j < 0 or j >= len(usable):
        raise ValueError("There is no line on that side.")
    T = usable[j]
    T.setdefault("words", [])
    if direction < 0:
        moving, L["words"] = L["words"][:k + 1], L["words"][k + 1:]
        T["words"] = T["words"] + moving
    else:
        moving, L["words"] = L["words"][k:], L["words"][:k]
        T["words"] = moving + T["words"]
    for w in moving:
        w["accepted"] = True       # the user placed it: confident
    L["read"], T["read"] = bool(L["words"]), True
    page.pop("text", None)
    return L["order"], T["order"]
