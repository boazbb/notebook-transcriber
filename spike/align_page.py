"""Spike: align recognised words to word boxes across several lines at once (no Space presses).

Dynamic programming over the boxes in reading order (lines concatenated). Each spoken word's
expected width = letters x letter width (self-calibrated on the page). Moves: 1 word-1 box,
2 words-1 box, 1 word-2 boxes (same line only), skip word, skip box.
Usage: uv run python align_page.py   (page 115, paragraph 1 = lines 1-7)
"""
import json
import math

import cv2

import words as W
from score import align, norm, tokens

PAGE, LINES = 2, (1, 7)
SKIP_WORD, SKIP_BOX, MERGE = 1.6, 1.2, 0.35


def boxes_for(lines_json, ink, lo, hi):
    d = lines_json
    lh = d["line_height_px"]
    text = [L for L in d["lines"] if L["kind"] == "text"]
    data = [W.split_words(W.line_components(ink, L["polygons"]), lh, L["baseline"]) for L in text]
    thr = W.threshold_from_gaps([g for _, gaps, _ in data for g in gaps], lh)
    out = []
    for li in range(lo - 1, hi):
        runs, gaps, _ = data[li]
        words = []
        for i, r in enumerate(runs):
            if i == 0 or gaps[i - 1] >= thr:
                words.append(list(r))
            else:
                w = words[-1]
                w[0], w[2], w[3] = min(w[0], r[0]), min(w[2], r[2]), max(w[3], r[3])
        out += [{"line": li + 1, "x0": w[0], "x1": w[1], "w": w[1] - w[0]} for w in words]
    return out


def run_dp(words, boxes, lw, free_ends=False):
    n, m = len(words), len(boxes)
    INF = float("inf")
    D = [[INF] * (m + 1) for _ in range(n + 1)]
    B = [[None] * (m + 1) for _ in range(n + 1)]
    D[0][0] = 0.0
    if free_ends:  # reading may start anywhere on the page: leading boxes cost nothing
        for j in range(1, m + 1):
            D[0][j], B[0][j] = 0.0, (0, 1)

    def fit(letters, width):
        return abs(math.log(max(width, 1) / max(letters * lw, 1)))

    for i in range(n + 1):
        for j in range(m + 1):
            c = D[i][j]
            if c == INF:
                continue
            moves = []
            if i < n and j < m:
                moves.append((1, 1, fit(len(words[i]), boxes[j]["w"])))
            if i + 1 < n and j < m:  # two words written as one box
                moves.append((2, 1, fit(len(words[i]) + len(words[i + 1]), boxes[j]["w"]) + MERGE))
            if i < n and j + 1 < m and boxes[j]["line"] == boxes[j + 1]["line"]:  # word split in two boxes
                moves.append((1, 2, fit(len(words[i]), boxes[j]["w"] + boxes[j + 1]["w"]) + MERGE))
            if i < n:
                moves.append((1, 0, SKIP_WORD))
            if j < m:
                moves.append((0, 1, SKIP_BOX))
            for di, dj, cost in moves:
                if c + cost < D[i + di][j + dj]:
                    D[i + di][j + dj] = c + cost
                    B[i + di][j + dj] = (di, dj)
    # backtrack: line of each word
    line_of = [None] * n
    i, j = n, m
    if free_ends:  # ...and may stop anywhere: trailing boxes cost nothing
        j = min(range(m + 1), key=lambda k: D[n][k])
    while i or j:
        di, dj = B[i][j]
        if di and dj:
            for k in range(i - di, i):
                line_of[k] = boxes[j - dj]["line"]
        i, j = i - di, j - dj
    return line_of, D[n][m] if not free_ends else min(D[n])


def main():
    d = json.load(open(f"out/page{PAGE}_lines2.json"))
    img = cv2.imread(f"out/page{PAGE}.png")
    ink = W.ink_mask(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    boxes = boxes_for(d, ink, *LINES)

    truth_lines = open("../test_files/p_115_truth_lines.txt", encoding="utf-8").read().splitlines()
    ref = [(norm(w), li + 1) for li, l in enumerate(truth_lines) for w in l.split()]

    segs = json.load(open("out/p_115_ref_cpu.json"))["segments"]
    hyp_all = [w for w, _ in tokens(" ".join(s["text"] for s in segs))]
    hyp = hyp_all[:len(ref) + 2]
    # trim the spoken words to the paragraph (as the app would, from where the reading of these lines ends)
    path = align([w for w, _ in ref], hyp)
    last = max(j for i, j in path if i is not None and j is not None)
    hyp = hyp[:last + 1]

    letters = sum(len(w) for w in hyp)
    lw = sum(b["w"] for b in boxes) / letters
    line_of, cost = run_dp(hyp, boxes, lw)

    ok = tot = 0
    wrong = []
    for i, j in align([w for w, _ in ref], hyp):
        if i is None or j is None:
            continue
        tot += 1
        if line_of[j] == ref[i][1]:
            ok += 1
        else:
            wrong.append(f"{hyp[j]} (truth line {ref[i][1]}, got {line_of[j]})")
    print(f"{len(hyp)} spoken words, {len(boxes)} word boxes on lines {LINES[0]}-{LINES[1]}, "
          f"letter width {lw:.0f}px")
    print(f"words placed on the right line: {ok}/{tot}")
    for w in wrong:
        print("   ", w)


if __name__ == "__main__":
    main()
