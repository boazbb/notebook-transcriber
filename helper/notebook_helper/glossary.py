"""Stage 7: glossary of names and invented terms, with niqqud added on export.

An entry: {"id", "plain": "דנה", "pointed": "דָנָה", "rule": "first" | "every" | "never",
           "variants": ["דאנה"], "note": "..."}
- A word matches when, after removing up to 3 prefix letters (ו ה ב כ ל מ ש, stacked like ולדנה),
  it equals the plain form or a variant (niqqud and final letters ignored).
- Variants are misspellings to correct: they are replaced by the glossary spelling.
- rule "first": niqqud only on the first appearance in the export, or in each chapter; "every": always;
  "never": only the spelling is corrected. The prefix letters stay unpointed.
"""
from __future__ import annotations

import re
import threading
import uuid
from pathlib import Path

from .hebrew import norm

PREFIX_LETTERS = set("והבכלמש")
RULES = ("first", "every", "never")
NIQQUD_MODEL = Path.home() / ".cache" / "notebook-transcriber" / "dicta-1.0.int8.onnx"


def new_entry(plain: str, pointed: str = "", rule: str = "first", note: str = "", variants=None) -> dict:
    return {"id": uuid.uuid4().hex[:8], "plain": plain.strip(), "pointed": pointed.strip() or plain.strip(),
            "rule": rule if rule in RULES else "first", "note": note, "variants": list(variants or [])}


def _index(entries: list[dict]) -> dict[str, dict]:
    idx = {}
    for e in entries:
        for form in [e["plain"], *e.get("variants", [])]:
            if norm(form):
                idx[norm(form)] = e
    return idx


def _split_punct(token: str) -> tuple[str, str, str]:
    i, j = 0, len(token)
    while i < j and not ("א" <= token[i] <= "ת"):
        i += 1
    while j > i and not ("א" <= token[j - 1] <= "ת" or "֑" <= token[j - 1] <= "ׇ"):
        j -= 1
    return token[:i], token[i:j], token[j:]


def match(word: str, idx: dict[str, dict]) -> tuple[dict, str] | None:
    """(entry, prefix) if the word is a glossary word, possibly behind prefix letters."""
    core = re.sub(r"[֑-ׇ]", "", word)
    for k in range(0, min(3, len(core) - 2) + 1):
        prefix, rest = core[:k], core[k:]
        if all(c in PREFIX_LETTERS for c in prefix) and norm(rest) in idx:
            return idx[norm(rest)], prefix
    return None


class Applier:
    """Applies the glossary to a stream of lines; call new_chapter() at chapter starts."""

    def __init__(self, entries: list[dict]):
        self.idx = _index(entries)
        self.seen: set[str] = set()
        self.counts: dict[str, int] = {}

    def new_chapter(self) -> None:
        self.seen.clear()

    def line(self, text: str) -> str:
        if not self.idx:
            return text
        out = []
        for token in text.split(" "):
            pre, core, post = _split_punct(token)
            m = match(core, self.idx) if core else None
            if not m:
                out.append(token)
                continue
            e, prefix = m
            self.counts[e["id"]] = self.counts.get(e["id"], 0) + 1
            pointed = e["rule"] == "every" or (e["rule"] == "first" and e["id"] not in self.seen)
            self.seen.add(e["id"])
            out.append(pre + prefix + (e["pointed"] if pointed else e["plain"]) + post)
        return " ".join(out)


def hotwords(entries: list[dict]) -> str | None:
    """Glossary spellings given to the speech model as hints in every 30 s window."""
    words = []
    for e in entries:
        if e["plain"] not in words:
            words.append(e["plain"])
    return " ".join(words) or None


# ---------- niqqud proposals (dicta-onnx, local Nakdan model) ----------

_dicta = None
_dicta_lock = threading.Lock()


def guess_plain(word: str, entries: list[dict]) -> str:
    """The word without punctuation and, if it matches a glossary word behind prefixes, without them."""
    core = _split_punct(word)[1]
    m = match(core, _index(entries))
    return m[0]["plain"] if m else core


def propose_pointing(word: str, plain: str, sentence: str = "") -> dict:
    """Propose niqqud for `plain`, as it appears in `word` (which may carry prefix letters, e.g.
    ולדנה / דנה), using the sentence as context. Returns {"plain", "pointed"}."""
    global _dicta
    core = _split_punct(word)[1] or plain
    plain = plain.strip() or core
    n_prefix = len(core) - len(plain) if core.endswith(plain) else 0
    with _dicta_lock:
        if _dicta is None:
            from dicta_onnx import Dicta
            _dicta = Dicta(str(NIQQUD_MODEL))
        if n_prefix:  # behind a prefix the first letter's pointing changes (דָנָה vs דָּנָה): point the bare word
            core, n_prefix = plain, 0
        text = sentence if core in sentence else core
        pointed_text = _dicta.add_diacritics(text)
        target = None
        for tok in pointed_text.split():
            t = _split_punct(tok)[1]
            if re.sub(r"[֑-ׇ]", "", t) == core:
                target = t
                break
        if target is None:
            target = _split_punct(_dicta.add_diacritics(core))[1]
            n_prefix = len(core) - len(plain) if core.endswith(plain) else 0
    if n_prefix:
        target = strip_prefix_pointing(target, n_prefix)[1]
    if re.sub(r"[֑-ׇ]", "", target) != plain:
        target = _split_punct(_dicta.add_diacritics(plain))[1]  # letters drifted: point the bare word
    return {"plain": plain, "pointed": target}


def strip_prefix_pointing(pointed: str, n_prefix: int) -> tuple[str, str]:
    """Split a pointed word after n_prefix letters: (prefix letters without niqqud, pointed rest)."""
    letters = 0
    for i, ch in enumerate(pointed):
        if "א" <= ch <= "ת":
            if letters == n_prefix:
                return re.sub(r"[֑-ׇ]", "", pointed[:i]), pointed[i:]
            letters += 1
    return pointed, ""


def correct_spelling(text: str, idx: dict[str, dict]) -> str | None:
    """If the word is a glossary entry's "also heard as" variant (behind any prefix), the word with the
    glossary spelling (same prefix and punctuation); otherwise None. Plain forms are left as they are."""
    pre, core, post = _split_punct(text)
    m = match(core, idx) if core else None
    if not m:
        return None
    e, prefix = m
    rest = core[len(prefix):]
    if norm(rest) == norm(e["plain"]):
        return None
    return pre + prefix + e["plain"] + post
