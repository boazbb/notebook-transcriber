"""M5: editing the page layout — lines and drawings.

Line edits: merge with the next line, split at a point, move in reading order, skip / include, add a line
from a rectangle, stretch or shrink a line's ends. Drawings: add a box, remove one. Lines lying in a drawing
are skipped. After every edit the word boxes and paragraphs are recomputed; on a transcribed page the words
are re-placed on the new lines by the same matching as after transcription (no new speech recognition).
"""
from __future__ import annotations

import uuid

import cv2
import numpy as np

from . import layout


def _mask_polys(shape, polys) -> np.ndarray:
    m = np.zeros(shape, np.uint8)
    for p in polys:
        cv2.fillPoly(m, [np.array(p, np.int32)], 1)
    return m


def _polys_from_mask(m: np.ndarray, lh: float) -> list[list[list[int]]]:
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        if cv2.contourArea(c) < 0.05 * lh * lh:
            continue
        c = cv2.approxPolyDP(c, max(1.0, lh * 0.03), True)
        out.append([[int(x), int(y)] for x, y in c.reshape(-1, 2)])
    return out


def _bbox(polys) -> list[int]:
    xs = [p[0] for poly in polys for p in poly]
    ys = [p[1] for poly in polys for p in poly]
    return [min(xs), min(ys), max(xs), max(ys)]


def _baseline_at(baseline, x) -> float:
    bl = sorted(baseline, key=lambda p: p[0])
    return float(np.interp(x, [p[0] for p in bl], [p[1] for p in bl]))


def renumber(page: dict) -> None:
    for i, L in enumerate(page["lines"]):
        L["order"] = i + 1


def _new_id(page: dict) -> str:
    ids = {L["id"] for L in page["lines"]}
    while True:
        i = "l" + uuid.uuid4().hex[:6]
        if i not in ids:
            return i


def find(page: dict, line_id: str) -> int:
    for i, L in enumerate(page["lines"]):
        if L["id"] == line_id:
            return i
    raise KeyError(f"No line {line_id}")


# ---------- edits ----------

def merge_next(page: dict, line_id: str) -> None:
    i = find(page, line_id)
    if i + 1 >= len(page["lines"]):
        raise ValueError("This is the last line.")
    a, b = page["lines"][i], page["lines"].pop(i + 1)
    a["polygons"] = a["polygons"] + b["polygons"]
    a["baseline"] = sorted(a["baseline"] + b["baseline"], key=lambda p: -p[0])
    a["bbox"] = _bbox(a["polygons"])
    a["words"] = a.get("words", []) + b.get("words", [])
    renumber(page)


def split(page: dict, line_id: str, x: int, shape) -> None:
    """Cut a line at x into a right part (kept) and a left part (the next line in reading order)."""
    i = find(page, line_id)
    L = page["lines"][i]
    lh = page["line_height"]
    m = _mask_polys(shape, L["polygons"])
    right, left = m.copy(), m.copy()
    right[:, :x] = 0
    left[:, x:] = 0
    rp, lp = _polys_from_mask(right, lh), _polys_from_mask(left, lh)
    if not rp or not lp:
        raise ValueError("Split point is outside the line.")
    new = {"id": _new_id(page), "kind": "text", "paragraph": L.get("paragraph", 1), "polygons": lp,
           "baseline": [p for p in L["baseline"] if p[0] < x] or [[x - 1, int(_baseline_at(L["baseline"], x))]],
           "boxes": [], "words": []}
    L["polygons"] = rp
    L["baseline"] = [p for p in L["baseline"] if p[0] >= x] or [[x, int(_baseline_at(L["baseline"], x))]]
    L["bbox"], new["bbox"] = _bbox(rp), _bbox(lp)
    page["lines"].insert(i + 1, new)
    renumber(page)


def move(page: dict, line_id: str, delta: int) -> None:
    i = find(page, line_id)
    j = max(0, min(len(page["lines"]) - 1, i + delta))
    page["lines"].insert(j, page["lines"].pop(i))
    renumber(page)


def set_skip(page: dict, line_id: str, skip: bool) -> None:
    page["lines"][find(page, line_id)]["skip"] = bool(skip)


def add_line(page: dict, rect: list[int]) -> str:
    """A new line from a rectangle, placed in reading order by its height on the page."""
    x0, y0, x1, y1 = (int(v) for v in rect)
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    base = int(y1 - 0.25 * (y1 - y0))
    L = {"id": _new_id(page), "kind": "text", "paragraph": 1, "polygons": [[[x1, y0], [x0, y0], [x0, y1], [x1, y1]]],
         "baseline": [[x1, base], [x0, base]], "bbox": [x0, y0, x1, y1], "boxes": [], "words": []}
    pos = sum(1 for o in page["lines"] if _baseline_at(o["baseline"], (x0 + x1) / 2) < base)
    page["lines"].insert(pos, L)
    renumber(page)
    return L["id"]


def set_ends(page: dict, line_id: str, x0: int, x1: int, shape) -> None:
    """Stretch or shrink a line to run from x0 to x1 (e.g. to take in a first letter written apart)."""
    L = page["lines"][find(page, line_id)]
    lh = page["line_height"]
    m = _mask_polys(shape, L["polygons"])
    bx0, by0, bx1, by1 = L["bbox"]
    x0, x1 = sorted((int(x0), int(x1)))
    for edge_x, new_x in ((bx0, x0), (bx1, x1)):
        if (new_x < bx0 and edge_x == bx0) or (new_x > bx1 and edge_x == bx1):
            base = _baseline_at(L["baseline"], edge_x)
            top, bot = int(base - 1.0 * lh), int(base + 0.35 * lh)
            a, b = sorted((edge_x, new_x))
            m[max(0, top):bot, max(0, a):b + 1] = 1
    m[:, :max(0, x0)] = 0
    m[:, x1 + 1:] = 0
    polys = _polys_from_mask(m, lh)
    if not polys:
        raise ValueError("The line would be empty.")
    L["polygons"] = polys
    L["bbox"] = _bbox(polys)
    bl = [p for p in L["baseline"] if x0 <= p[0] <= x1]
    for x in (x1, x0):
        if not any(abs(p[0] - x) < 2 for p in bl):
            bl.append([x, int(_baseline_at(L["baseline"], x))])
    L["baseline"] = sorted(bl, key=lambda p: -p[0])


def add_drawing(page: dict, rect: list[int]) -> None:
    x0, y0, x1, y1 = (int(v) for v in rect)
    page.setdefault("drawings", []).append({"id": "d" + uuid.uuid4().hex[:6], "bbox": [min(x0, x1), min(y0, y1),
                                            max(x0, x1), max(y0, y1)], "source": "user"})


def remove_drawing(page: dict, drawing_id: str) -> None:
    page["drawings"] = [d for d in page.get("drawings", []) if d["id"] != drawing_id]


# ---------- keeping the page consistent ----------

def refresh(page: dict, ink: np.ndarray) -> None:
    """After an edit: lines in drawings skipped, paragraphs renumbered, word boxes recomputed."""
    lh = page["line_height"]
    in_drawing = layout.lines_in_drawings(page["lines"], [d["bbox"] for d in page.get("drawings", [])])
    for L in page["lines"]:
        L["in_drawing"] = L["id"] in in_drawing
        L["bbox"] = _bbox(L["polygons"])
    active = [L for L in page["lines"] if not (L.get("skip") or L.get("in_drawing"))]
    layout.mark_paragraphs(active, lh)
    layout.word_boxes(ink, page["lines"], lh)


def detect_drawings(page: dict, ink: np.ndarray) -> None:
    """Automatic drawing boxes (replaces earlier automatic ones; the user's own are kept)."""
    h, w = ink.shape
    user = [d for d in page.get("drawings", []) if d.get("source") == "user"]
    auto = [{"id": f"d{k}", "bbox": b, "source": "auto"}
            for k, b in enumerate(layout.find_drawings(ink, page["lines"], page["line_height"], w, h))]
    page["drawings"] = auto + user
