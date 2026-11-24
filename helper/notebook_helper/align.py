"""Stage 5: match spoken words to word boxes across the page (this also decides each word's line).

Dynamic programming over boxes in reading order. Expected width of a word = letters x letter width.
Moves: 1 word-1 box, 2 words-1 box, 1 word-2 boxes (same line), skip word, skip box.
Boxes before the first and after the last spoken word are free, so reading can start and stop anywhere.
"""
from __future__ import annotations

import math

from .hebrew import norm

SKIP_WORD, SKIP_BOX, MERGE = 1.6, 1.2, 0.35
LETTER_WIDTH = 0.36  # x line height; measured on the test notebook


def align(words: list[str], lines: list[dict], line_height: float, start_line: int = 1,
          free_ends: bool = True) -> list[dict | None]:
    """For each spoken word: {'line': order, 'boxes': [box indices in that line]} or None if unplaced.
    free_ends: boxes before the first and after the last word cost nothing (a page read from mid-page);
    False when re-matching a single line, whose words should cover all its boxes."""
    boxes = [(L["order"], k, b[2] - b[0]) for L in lines
             if L["order"] >= start_line and not L.get("skip") and not L.get("in_drawing")
             for k, b in enumerate(L.get("boxes", []))]
    letters = [max(1, len(norm(w))) for w in words]
    lw = LETTER_WIDTH * line_height
    n, m = len(words), len(boxes)
    INF = float("inf")
    D = [[INF] * (m + 1) for _ in range(n + 1)]
    B: list[list[tuple[int, int] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    D[0][0] = 0.0
    for j in range(1, m + 1):  # free start (else: skipping boxes costs as usual)
        D[0][j], B[0][j] = (0.0 if free_ends else j * SKIP_BOX), (0, 1)

    def fit(nletters, width):
        return abs(math.log(max(width, 1) / max(nletters * lw, 1)))

    for i in range(n + 1):
        Di = D[i]
        for j in range(m + 1):
            c = Di[j]
            if c == INF:
                continue
            moves = []
            if i < n and j < m:
                moves.append((1, 1, fit(letters[i], boxes[j][2])))
            if i + 1 < n and j < m:
                moves.append((2, 1, fit(letters[i] + letters[i + 1], boxes[j][2]) + MERGE))
            if i < n and j + 1 < m and boxes[j][0] == boxes[j + 1][0]:
                moves.append((1, 2, fit(letters[i], boxes[j][2] + boxes[j + 1][2]) + MERGE))
            if i < n:
                moves.append((1, 0, SKIP_WORD))
            if j < m:
                moves.append((0, 1, SKIP_BOX))
            for di, dj, cost in moves:
                if c + cost < D[i + di][j + dj]:
                    D[i + di][j + dj] = c + cost
                    B[i + di][j + dj] = (di, dj)

    result: list[dict | None] = [None] * n
    i, j = n, (min(range(m + 1), key=lambda k: D[n][k]) if free_ends else m)  # free end
    while i or j:
        di, dj = B[i][j]
        if di and dj:
            line = boxes[j - dj][0]
            idx = [boxes[k][1] for k in range(j - dj, j)]
            kind = "1:1" if di == dj == 1 else ("2:1" if di == 2 else "1:2")
            fit_cost = D[i][j] - D[i - di][j - dj] - (0 if kind == "1:1" else MERGE)
            for k in range(i - di, i):
                result[k] = {"line": line, "boxes": idx, "fit": round(fit_cost, 3), "kind": kind}
        i, j = i - di, j - dj
    # words that matched no box (skipped) take the line of their neighbours
    for k in range(n):
        if result[k] is None:
            prev = next((result[x] for x in range(k - 1, -1, -1) if result[x]), None)
            nxt = next((result[x] for x in range(k + 1, n) if result[x]), None)
            ref = prev or nxt
            if ref:
                result[k] = {"line": ref["line"], "boxes": [], "unmatched": True, "fit": None, "kind": "none"}
    return result
