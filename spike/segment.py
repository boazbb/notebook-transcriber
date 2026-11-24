"""Spike: page prep + line detection by projection profiles.

Usage: uv run python segment.py PAGE_NUMBER [PAGE_NUMBER ...]
Reads out/pageN.png (rendered at 300 dpi), writes out/pageN_clean.png,
out/pageN_lines.png (overlay) and out/pageN_lines.json.
"""
import json
import sys

import cv2
import numpy as np
from skimage.filters import threshold_sauvola

STRIPS = 10  # vertical strips for piecewise profiles (handles slanted lines)


def binarize(gray):
    th = threshold_sauvola(gray, window_size=51, k=0.2)
    ink = (gray < th).astype(np.uint8)
    # drop speckle: components smaller than a dot of a period
    n, lab, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    keep = stats[:, cv2.CC_STAT_AREA] >= 12
    keep[0] = False
    return keep[lab].astype(np.uint8)


def remove_ruling(ink):
    """Remove long, thin horizontal lines (notebook ruling)."""
    w = ink.shape[1]
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (w // 8, 1))
    lines = cv2.morphologyEx(ink, cv2.MORPH_OPEN, kernel)
    lines = cv2.dilate(lines, np.ones((3, 1), np.uint8))
    return ink & (1 - lines), int(lines.sum())


def page_mask(gray):
    """Area of the actual paper (crop off scanner background / page edges)."""
    h, w = gray.shape
    m = np.zeros_like(gray, dtype=bool)
    mx, my = int(w * 0.02), int(h * 0.01)
    m[my:h - my, mx:w - mx] = True
    return m


def smooth(v, k):
    k = max(3, k | 1)
    return np.convolve(v, np.ones(k) / k, mode="same")


def estimate_line_height(profile):
    """Dominant period of the row profile via autocorrelation."""
    p = profile - profile.mean()
    ac = np.correlate(p, p, mode="full")[len(p) - 1:]
    lo, hi = 40, 400
    return lo + int(np.argmax(ac[lo:hi]))


def find_lines(ink):
    h, w = ink.shape
    cols = np.linspace(0, w, STRIPS + 1).astype(int)
    full = ink.sum(axis=1).astype(float)
    period = estimate_line_height(smooth(full, 15))
    # per-strip text-line centres = peaks of the smoothed profile
    strip_peaks = []
    for i in range(STRIPS):
        prof = smooth(ink[:, cols[i]:cols[i + 1]].sum(axis=1).astype(float), period // 3)
        thr = 0.15 * prof.max()
        peaks = [y for y in range(1, h - 1)
                 if prof[y] >= thr and prof[y] >= prof[y - 1] and prof[y] > prof[y + 1]]
        # non-max suppression within half a line
        peaks.sort(key=lambda y: -prof[y])
        chosen = []
        for y in peaks:
            if all(abs(y - c) > period * 0.55 for c in chosen):
                chosen.append(y)
        strip_peaks.append(sorted(chosen))
    # link peaks across strips, right to left. Each track predicts where it
    # continues using its own slope (or the local median slope if it is new),
    # so slanted / curved lines don't jump to the neighbouring line.
    centres_x = [(cols[i] + cols[i + 1]) // 2 for i in range(STRIPS)]
    dx = centres_x[1] - centres_x[0]
    tracks = []  # each: list of (strip, y)
    for i in range(STRIPS - 1, -1, -1):
        slopes = [(t[-1][1] - t[-2][1]) / (dx * (t[-2][0] - t[-1][0]))
                  for t in tracks if len(t) >= 2 and t[-1][0] == i + 1]
        med = float(np.median(slopes)) if slopes else 0.0

        def predict(t):
            gap = t[-1][0] - i
            s = ((t[-1][1] - t[-2][1]) / (dx * (t[-2][0] - t[-1][0]))) if len(t) >= 2 else med
            return t[-1][1] + s * dx * gap

        cand = [(abs(predict(t) - y), ti, y) for ti, t in enumerate(tracks)
                if t[-1][0] - i <= 2 for y in strip_peaks[i]]
        cand.sort()
        used_t, used_y = set(), set()
        for d, ti, y in cand:
            if d > period * 0.4 or ti in used_t or y in used_y:
                continue
            tracks[ti].append((i, y))
            used_t.add(ti)
            used_y.add(y)
        for y in strip_peaks[i]:
            if y not in used_y:
                tracks.append([(i, y)])
    # turn tracks into centre curves; a one-strip track (short line) gets a flat line
    lines = []
    for t in tracks:
        xs = np.array([centres_x[i] for i, _ in t], float)
        ys = np.array([y for _, y in t], float)
        deg = 0 if len(t) == 1 else (1 if len(t) < 5 else 2)
        coef = np.polyfit(xs, ys, deg)
        lines.append({"coef": coef, "x0": cols[min(i for i, _ in t)], "x1": cols[max(i for i, _ in t) + 1]})
    lines.sort(key=lambda L: np.polyval(L["coef"], (L["x0"] + L["x1"]) / 2))
    return lines, period


def line_bands(ink, lines, period):
    """Assign each ink component to the nearest line centre, then box each line's ink."""
    h, w = ink.shape
    n, lab, stats, cent = cv2.connectedComponentsWithStats(ink, connectivity=8)
    members = [[] for _ in lines]
    margin = w / STRIPS
    for c in range(1, n):
        cx, cy = cent[c]
        best, bd = None, period * 0.75
        for k, L in enumerate(lines):
            if not (L["x0"] - margin <= cx <= L["x1"] + margin):
                continue
            d = abs(np.polyval(L["coef"], cx) - cy)
            if d < bd:
                best, bd = k, d
        if best is not None:
            members[best].append(c)
    out = []
    for k, L in enumerate(lines):
        comps = members[k]
        if not comps:
            continue
        x0 = int(min(stats[c, cv2.CC_STAT_LEFT] for c in comps))
        x1 = int(max(stats[c, cv2.CC_STAT_LEFT] + stats[c, cv2.CC_STAT_WIDTH] for c in comps))
        # vertical extent: robust envelope around the centre (ignore tall ascenders / descenders)
        ink_px = int(sum(stats[c, cv2.CC_STAT_AREA] for c in comps))
        off = []
        for c in comps:
            m = lab[stats[c, 1]:stats[c, 1] + stats[c, 3], stats[c, 0]:stats[c, 0] + stats[c, 2]] == c
            ys, xs = np.nonzero(m)
            ys = ys + stats[c, 1]
            xs = xs + stats[c, 0]
            off.append(ys - np.polyval(L["coef"], xs))
        off = np.concatenate(off)
        up, down = np.percentile(off, 1), np.percentile(off, 99)
        cy0, cy1 = np.polyval(L["coef"], x0), np.polyval(L["coef"], x1)
        poly = [[x1, int(cy1 + up)], [x0, int(cy0 + up)], [x0, int(cy0 + down)], [x1, int(cy1 + down)]]
        out.append({"polygon": poly,
                    "bbox": [x0, int(min(cy0, cy1) + up), x1, int(max(cy0, cy1) + down)],
                    "centre": [[int(x), int(np.polyval(L["coef"], x))] for x in np.linspace(x1, x0, 8)],
                    "ink": ink_px, "components": comps})
    return out


def run(n):
    img = cv2.imread(f"out/page{n}.png")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ink = binarize(gray)
    ink[~page_mask(gray)] = 0
    ink, ruling_px = remove_ruling(ink)
    cv2.imwrite(f"out/page{n}_clean.png", (1 - ink) * 255)
    lines, period = find_lines(ink)
    bands = line_bands(ink, lines, period)
    # drop bands with very little ink (noise between lines)
    med = np.median([b["ink"] for b in bands]) if bands else 0
    bands = [b for b in bands if b["ink"] > 0.08 * med]
    for i, b in enumerate(bands):
        b["order"] = i + 1
    vis = img.copy()
    colors = [(0, 0, 255), (0, 160, 0), (255, 0, 0), (0, 140, 255)]
    for b in bands:
        c = colors[b["order"] % len(colors)]
        cv2.polylines(vis, [np.array(b["polygon"], np.int32)], True, c, 3)
        cv2.polylines(vis, [np.array(b["centre"], np.int32)], False, c, 2)
        x1, y = b["polygon"][0]
        cv2.putText(vis, str(b["order"]), (min(x1 + 10, vis.shape[1] - 80), y + int(period * 0.7)),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.6, c, 4)
    cv2.imwrite(f"out/page{n}_lines.png", vis)
    for b in bands:
        b.pop("components", None)
    json.dump({"page": n, "line_height_px": period, "ruling_px_removed": ruling_px, "lines": bands},
              open(f"out/page{n}_lines.json", "w"), indent=1)
    print(f"page {n}: line height ~{period}px, {len(bands)} lines, ruling px removed {ruling_px}")


if __name__ == "__main__":
    for a in sys.argv[1:]:
        run(int(a))
