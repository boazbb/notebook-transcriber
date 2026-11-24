"""Spike: word boxes from horizontal gaps inside each detected line.

Usage: uv run python words.py N [--truth FILE --lines a-b]
Uses out/pageN_lines2.json (Kraken + joining). Writes out/pageN_words.png.
With --truth, compares word counts per line against a line-broken ground truth.
"""
import argparse
import json

import cv2
import numpy as np
from skimage.filters import threshold_sauvola


def ink_mask(gray):
    th = threshold_sauvola(gray, window_size=51, k=0.2)
    return (gray < th).astype(np.uint8)


def line_components(ink, polygons):
    mask = np.zeros_like(ink)
    for p in polygons:
        cv2.fillPoly(mask, [np.array(p, np.int32)], 1)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(ink & mask, connectivity=8)
    return [stats[i] for i in range(1, n)]


def baseline_y(baseline, x):
    bl = sorted(baseline, key=lambda p: p[0])
    return float(np.interp(x, [p[0] for p in bl], [p[1] for p in bl]))


def is_mark(c, lh, baseline):
    """Punctuation-like: small, and sitting at or below the baseline (unlike י, which sits high)."""
    x, y, w, h, area = c
    if area < 0.002 * lh * lh:
        return False  # speck
    if area < 0.02 * lh * lh and h < 0.3 * lh:
        return (y + h / 2) > baseline_y(baseline, x + w / 2) - 0.3 * lh
    if h < 0.45 * lh and w < 0.25 * lh:  # comma-sized stroke
        return (y + h / 2) > baseline_y(baseline, x + w / 2) - 0.15 * lh
    return False


def split_words(comps, lh, baseline):
    """Group components into words by horizontal gaps; punctuation marks don't bridge gaps."""
    small = [c for c in comps if is_mark(c, lh, baseline)]
    big = [c for c in comps if c[cv2.CC_STAT_AREA] >= 0.002 * lh * lh and not is_mark(c, lh, baseline)]
    # merge overlapping x-ranges of the big components into "ink runs"
    spans = sorted([[c[0], c[0] + c[2], c[1], c[1] + c[3]] for c in big], key=lambda s: -s[1])
    runs = []
    for s in spans:  # right to left
        if runs and s[1] >= runs[-1][0] - 2:
            r = runs[-1]
            r[0], r[2], r[3] = min(r[0], s[0]), min(r[2], s[2]), max(r[3], s[3])
        else:
            runs.append(list(s))
    gaps = [runs[i][0] - runs[i + 1][1] for i in range(len(runs) - 1)]
    return runs, gaps, small


def threshold_from_gaps(all_gaps, lh):
    """Page-level threshold between letter gaps and word gaps (Otsu on gap widths)."""
    g = np.array([x for x in all_gaps if x > 0], float)
    if len(g) < 4:
        return 0.35 * lh
    best, bt = -1, None
    for t in np.unique(g):
        a, b = g[g < t], g[g >= t]
        if len(a) == 0 or len(b) == 0:
            continue
        v = len(a) * len(b) * (a.mean() - b.mean()) ** 2
        if v > best:
            best, bt = v, t
    return float(bt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("page", type=int)
    ap.add_argument("--truth")
    ap.add_argument("--lines", default=None, help="1-based inclusive range, e.g. 1-7")
    a = ap.parse_args()
    d = json.load(open(f"out/page{a.page}_lines2.json"))
    img = cv2.imread(f"out/page{a.page}.png")
    ink = ink_mask(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    lh = d["line_height_px"]
    lines = [L for L in d["lines"] if L["kind"] == "text"]
    data = []
    for L in lines:
        runs, gaps, small = split_words(line_components(ink, L["polygons"]), lh, L["baseline"])
        data.append((L, runs, gaps, small))
    thr = threshold_from_gaps([g for _, _, gaps, _ in data for g in gaps], lh)
    print(f"line height {lh:.0f}px, word-gap threshold {thr:.0f}px ({thr / lh:.2f} x line height)")

    words_per_line = []
    vis = img.copy()
    for L, runs, gaps, small in data:
        words = []
        for i, r in enumerate(runs):
            if i == 0 or gaps[i - 1] >= thr:
                words.append(list(r))
            else:
                w = words[-1]
                w[0], w[2], w[3] = min(w[0], r[0]), min(w[2], r[2]), max(w[3], r[3])
        words_per_line.append(words)
        for k, w in enumerate(words):
            c = (0, 0, 220) if k % 2 == 0 else (0, 150, 0)
            cv2.rectangle(vis, (w[0], w[2]), (w[1], w[3]), c, 3)
        for s in small:
            cv2.rectangle(vis, (s[0], s[1]), (s[0] + s[2], s[1] + s[3]), (220, 0, 220), 2)
    h, w = vis.shape[:2]
    cv2.imwrite(f"out/page{a.page}_words.png", cv2.resize(vis, (w * 2 // 5, h * 2 // 5)))

    if a.truth:
        truth = [l.split() for l in open(a.truth, encoding="utf-8").read().splitlines() if l.strip()]
        lo, hi = (int(x) for x in a.lines.split("-")) if a.lines else (1, len(truth))
        tot_t = tot_ok = 0
        exact = 0
        for i, t in zip(range(lo - 1, hi), truth):
            got = len(words_per_line[i])
            exact += got == len(t)
            tot_t += len(t)
            tot_ok += min(got, len(t))
            print(f"  line {i + 1}: truth {len(t):2d} words, boxes {got:2d}  {'OK' if got == len(t) else 'diff ' + format(got - len(t), '+d')}")
        print(f"  exact word count on {exact}/{hi - lo + 1} lines; total boxes "
              f"{sum(len(words_per_line[i]) for i in range(lo - 1, hi))} vs {tot_t} words")


if __name__ == "__main__":
    main()
