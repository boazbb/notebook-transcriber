"""Stage 6: punctuation marks written on the page.

For each line, the ink components that aren't letters are classified by geometry (relative to the
line's letter height and its baseline) and attached to the word they follow:

- period: a small roundish dot on the baseline
- comma: a small stroke, taller than wide, reaching down to or below the baseline
- colon: two dots stacked
- dash: a short flat stroke at mid height
- quote: a short stroke (or two) high up, in the gap between words — closing the word on its right or
  opening the word on its left, whichever it's closer to (a yod is inside a word)
- parentheses / brackets: not detected (see DETECT_BRACKETS); the speech model's are kept.

A mark after word w lies left of w's box. Marks are found in the gap after each word, also just past
the line's last word.
"""
from __future__ import annotations

import cv2
import numpy as np


# Parentheses / brackets from the page: off. On the test notebook, handwritten square brackets measure
# like a ל (a tall stroke rising above the letters with a short hook), often at a word's start, so they
# can't be told apart by size and position. The speech model does write them from intonation (it wrote
# "(כמו בשנה שעברה)," for "[כמו בשנה שעברה],"), so its brackets are kept.
DETECT_BRACKETS = False


# Quote marks from the page: off. In the test notebook a quote mark (״) is a short high stroke, like a
# yod; measured gaps to the neighbouring letters overlap (a real closing quote: 0.27 / 0.22 letter heights;
# yods: 0.38 / 0.62 and many with a large gap on one side, at a word's start or end). Quotes come from
# spoken commands instead (spoken.py).
DETECT_QUOTES = False


def _baseline_y(baseline, x):
    bl = sorted(baseline, key=lambda p: p[0])
    return float(np.interp(x, [p[0] for p in bl], [p[1] for p in bl]))


def line_components(ink: np.ndarray, line: dict, lh: float | None = None) -> list[dict]:
    mask = np.zeros_like(ink)
    for p in line["polygons"]:
        cv2.fillPoly(mask, [np.array(p, np.int32)], 1)
    if lh:
        # plus a band around the baseline: periods and commas often sit just outside the line outline
        bl = sorted(line["baseline"], key=lambda q: q[0])
        x0, x1 = line["bbox"][0] - int(0.5 * lh), line["bbox"][2] + int(0.2 * lh)
        xs = np.arange(max(0, x0), min(ink.shape[1], x1))
        ys = np.interp(xs, [q[0] for q in bl], [q[1] for q in bl])
        for x, y in zip(xs, ys):
            mask[max(0, int(y - 0.35 * lh)):int(y + 0.3 * lh), x] = 1
    n, lab, stats, cent = cv2.connectedComponentsWithStats(ink & mask, connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, h, a = (int(v) for v in stats[i])
        c = {"x0": x, "y0": y, "x1": x + w, "y1": y + h, "w": w, "h": h, "area": a,
             "cx": float(cent[i][0]), "cy": float(cent[i][1]),
             "base": _baseline_y(line["baseline"], cent[i][0])}
        out.append(c)
    return out


def letter_height(comps: list[dict], lh: float) -> float:
    """Typical letter body height in this line (median of the bigger components)."""
    hs = [c["h"] for c in comps if c["area"] > 0.03 * lh * lh]
    return float(np.median(hs)) if hs else 0.45 * lh


def local_letters(c: dict, big: list[dict], xh: float) -> tuple[float, float] | None:
    """Top and bottom of the letters around a component (median of nearby letter-sized components).
    More reliable than the Kraken baseline, which sits a little below the letters."""
    nb = [b for b in big if abs((b["x0"] + b["x1"]) / 2 - c["cx"]) < 2.5 * xh]
    if not nb:
        return None
    return float(np.median([b["y0"] for b in nb])), float(np.median([b["y1"] for b in nb]))


def classify(c: dict, xh: float, lh: float, top: float, bot: float) -> str | None:
    """Mark type of one component, or None if it looks like (part of) a letter or noise.
    Measured in letter heights against the neighbouring letters' top and bottom (thresholds from the
    user's own marks on the test page: commas start low and reach below the letters; periods are small
    and sit on the bottom line; a vav or yod starts at the top)."""
    w, h = c["w"] / xh, c["h"] / xh
    t = (c["y0"] - top) / xh                  # where it starts: 0 = letter top, 1 = letter bottom
    btm = (c["y1"] - bot) / xh                # where it ends: > 0 = below the letters
    if c["area"] < 0.0012 * lh * lh or min(w, h) < 0.1:
        return None                           # speck
    # parenthesis: tall and narrow, above the letters and below them
    if DETECT_BRACKETS and h > 1.5 and w < 0.4 * h and c["y0"] < top - 0.2 * xh and btm > 0.2:
        return "paren"                        # ץ ק ן ך only go below, ל only above
    if max(w, h) > 0.75:
        return None                           # letter-sized
    if w > 2.2 * h and h < 0.3 and 0.2 < t < 0.75 and btm < -0.15:
        return "dash"
    if t < 0.25 and btm < -0.25 and 0.25 <= h <= 0.85 and w <= 0.6:
        return "high"                         # quote stroke (or a yod; decided by position later)
    if max(w, h) <= 0.32 and t >= 0.45 and -0.35 <= btm <= 0.35:
        return "dot"
    if h <= 0.5 and t >= 0.8 and -0.35 <= btm <= 0.1:
        return "dot"                          # a period written as a small tick: starts low, stays on the line
    if h >= 0.3 and t >= 0.25 and btm >= 0.03:
        return "comma"
    return None


def find_marks(ink: np.ndarray, line: dict, lh: float) -> dict[int, list[str]]:
    """{word index: marks after it (in order), plus "(" before it as "open"} for one line's words."""
    words = line.get("words", [])
    if not words:
        return {}
    comps = line_components(ink, line, lh)
    xh = letter_height(comps, lh)
    boxes = line["boxes"]
    # each word's horizontal extent (its boxes), right to left
    ext = []
    for w in words:
        bs = [boxes[b] for b in w["boxes"] if b < len(boxes)]
        ext.append((min(b[0] for b in bs), max(b[2] for b in bs)) if bs else None)
    found: dict[int, list] = {}

    def owner_after(cx):
        """The word a mark at x follows: the nearest word box to its right."""
        best, bd = None, None
        for i, e in enumerate(ext):
            if e is None:
                continue
            d = e[0] - cx  # word's left edge minus mark x: >= -small when the mark is left of the word
            if d >= -0.15 * xh and (bd is None or d < bd):
                best, bd = i, d
        return best

    big = [c for c in comps if c["area"] > 0.03 * lh * lh]
    for c in comps:
        tb = local_letters(c, big, xh)
        if tb is None:
            continue
        kind = classify(c, xh, lh, *tb)
        if kind is None:
            continue
        if kind == "paren":
            # right edge of a word box = opening (before the word); left edge = closing (after it)
            for i, e in enumerate(ext):
                if e and e[0] - 0.4 * xh <= c["cx"] <= e[1] + 0.4 * xh:
                    if c["cx"] > (e[0] + e[1]) / 2:
                        found.setdefault(i, []).append(("open", c["cx"]))
                    else:
                        found.setdefault(i, []).append((")", c["cx"]))
                    break
            continue
        if kind == "high" and not DETECT_QUOTES:
            continue
        if kind == "high":
            # a quote mark stands in a gap between words (a yod is inside a word). Closer to the word on
            # its right: it closes that word ("..."); closer to the word on its left: it opens that word.
            if any(e and e[0] - 0.05 * xh < c["cx"] < e[1] + 0.05 * xh for e in ext):
                continue
            right = [(e[0] - c["x1"], j) for j, e in enumerate(ext) if e and e[0] >= c["x1"] - 0.1 * xh]
            left = [(c["x0"] - e[1], j) for j, e in enumerate(ext) if e and e[1] <= c["x0"] + 0.1 * xh]
            r = min(right) if right else None
            l_ = min(left) if left else None
            if r and (not l_ or r[0] <= l_[0]):
                found.setdefault(r[1], []).append(("q_close", c["cx"]))
            elif l_:
                found.setdefault(l_[1], []).append(("q_open", c["cx"]))
            continue
        i = owner_after(c["cx"])
        if i is None:
            continue
        gap = (ext[i][0] - c["x1"]) / xh   # clear space between the mark and the word it follows
        if (kind == "comma" and gap < 0.05) or (kind in ("dot", "dash") and gap < -0.25):
            continue
        if kind == "dot" and gap < 0.1 and (c["y0"] - tb[0]) / xh < 0.7:
            continue  # touching the word and not sitting low: a piece of a letter, not a period
        # must be in the gap after the word (not inside the next word's letters)
        nxt = ext[i + 1] if i + 1 < len(ext) else None
        inside_next = nxt and nxt[0] + 0.1 * xh < c["cx"] < nxt[1] - 0.1 * xh
        if inside_next:
            continue
        found.setdefault(i, []).append((kind, c["cx"], c["cy"]))
    # combine pieces per gap: two stacked dots = colon; any number of quote strokes = one quote mark
    out: dict[int, list[str]] = {}
    for i, items in found.items():
        marks = []
        if any(m[0] == "open" for m in items):
            marks.append("(")
        if any(m[0] == "q_open" for m in items):
            marks.append("open_quote")
        rest = [m for m in items if m[0] not in ("open", "q_open", "q_close")]
        dots = [m for m in rest if m[0] == "dot"]
        if len(dots) >= 2 and abs(dots[0][1] - dots[1][1]) < 0.35 * xh and abs(dots[0][2] - dots[1][2]) > 0.25 * xh:
            rest = [m for m in rest if m not in dots[:2]] + [(":", dots[0][1])]
        for m in sorted(rest, key=lambda m: -m[1]):  # right to left = reading order
            marks.append({"dot": ".", "comma": ",", "dash": "-", ")": ")", ":": ":"}[m[0]])
        if any(m[0] == "q_close" for m in items):
            marks.append('"')
        if marks:
            out[i] = marks
    return out


# ---------- merging with the speech model's punctuation (spec stage 6) ----------

import re as _re

TAIL = _re.compile(r'^(.*?)([.,:;?!"\'\-)\]]*)$')


def split_word(text: str) -> tuple[str, str, str]:
    """(leading marks — "(" and/or an opening quote —, the word, trailing punctuation)."""
    m = _re.match(r'^([(\["\u05f4\u201c\u201e]*)', text)
    lead = m.group(1)
    core, tail = TAIL.match(text[len(lead):]).groups()
    return lead, core, tail


SENTENCE_MARKS = ".,:;?!"


def one_mark(marks: list[str], asr: str) -> str:
    """At most one sentence mark per gap from the page: several pieces in one gap (",,", ".,") are usually
    one mark in pieces, or a letter's dot. Prefer the one the speech model also has; else the nearest to
    the word (the first, in reading order). A closing quote / bracket after it is kept."""
    sentence = [m for m in marks if m in SENTENCE_MARKS]
    other = "".join(m for m in marks if m not in SENTENCE_MARKS)
    if not sentence:
        return other
    pick = asr[:1] if asr[:1] in sentence else sentence[0]
    return pick + other


def merge_line(line: dict, found: dict[int, list[str]], keep_asr_only: bool = True) -> None:
    """Combine the page's marks with Whisper's for each word of a line (words the user edited are left alone).
    Each word gets w["punct"] = {"page", "asr", "status"}: agree | page_only | asr_only | differ | none."""
    for k, w in enumerate(line.get("words", [])):
        if w.get("edited") or w.get("accepted") or (w.get("punct") or {}).get("status") == "user":
            continue                          # the user's words and choices are left alone
        lead, core, tail = split_word(w["text"])
        if "punct" in w:                      # re-run: start from Whisper's original marks
            asr, asr_lead = w["punct"]["asr"], w["punct"].get("asr_lead", "")
        else:
            asr, asr_lead = tail, lead
        marks = found.get(k, [])
        page_lead = ("(" if "(" in marks else "") + ('"' if "open_quote" in marks else "")
        page = one_mark([m for m in marks if m not in ("(", "open_quote")], asr)
        if core.endswith("ך") and page != asr:
            # the writer marks the shva inside a final kaf (ךְ), which reads as a comma: trust the speech model here
            page = asr
        if page == asr:
            final, status = asr, ("agree" if asr else "none")
        elif page and asr:
            final, status = page, "differ"
        elif page:
            final, status = page, "page_only"
        else:
            final, status = (asr if keep_asr_only else ""), "asr_only"
        new_lead = page_lead or asr_lead
        if page_lead != asr_lead and page_lead and status in ("agree", "none"):
            status = "page_only"
        w["text"] = new_lead + core + final
        w["punct"] = {"page": page_lead + page, "asr": asr, "asr_lead": asr_lead, "status": status}


def set_punct(w: dict, mark: str) -> None:
    """The user's choice of punctuation after a word ("" for none)."""
    lead, core, _ = split_word(w["text"])
    w["text"] = lead + core + mark
    w.setdefault("punct", {"page": "", "asr": "", "asr_lead": ""})["status"] = "user"
