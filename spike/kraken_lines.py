"""Spike: post-process Kraken line fragments into notebook lines.

- join fragments that continue the same baseline across a wide gap
- drop fragments that sit entirely in the outer edge strip (facing-page bleed)
- flag a small isolated fragment in a bottom corner as the page number
Usage: uv run python kraken_lines.py N [N ...]  -> out/pageN_lines2.json / .png
"""
import json
import sys

import cv2
import numpy as np

EDGE = 0.035          # outer strip (fraction of width) treated as facing-page bleed
JOIN_DY = 0.4         # max baseline mismatch when joining, in line heights
JOIN_GAP = 0.45       # max horizontal gap when joining, in page widths


def fit(bl):
    xs = np.array([p[0] for p in bl], float)
    ys = np.array([p[1] for p in bl], float)
    return np.polyfit(xs, ys, 1) if len(bl) > 1 and np.ptp(xs) > 0 else np.array([0.0, ys.mean()])


def process(n):
    d = json.load(open(f"out/page{n}_kraken.json"))
    img = cv2.imread(f"out/page{n}.png")
    h, w = img.shape[:2]
    frags = []
    for l in d["lines"]:
        bl = sorted(l["baseline"], key=lambda p: -p[0])  # right to left
        xs = [p[0] for p in l["boundary"]]
        ys = [p[1] for p in l["boundary"]]
        frags.append({"baseline": bl, "boundary": l["boundary"],
                      "x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys)})
    ys_mid = sorted(np.mean([p[1] for p in f["baseline"]]) for f in frags)
    lh = float(np.median(np.diff(ys_mid))) if len(ys_mid) > 2 else 120.0
    lh = max(lh, 60.0)

    dropped = []
    keep = []
    for f in frags:
        if f["x1"] < w * EDGE * 1.6 or f["x0"] > w * (1 - EDGE * 1.6):
            dropped.append(f)
        else:
            keep.append(f)
    frags = keep

    # join: repeatedly attach the best fragment continuing a chain to its left
    frags.sort(key=lambda f: -f["x1"])
    chains = []
    used = set()
    for i, f in enumerate(frags):
        if i in used:
            continue
        chain = [f]
        used.add(i)
        while True:
            last = chain[-1]
            # local slope at the chain's left end (lines curve near the binding)
            pts = [p for f in chain for p in f["baseline"]]
            pts.sort(key=lambda p: p[0])
            local = [p for p in pts if p[0] <= pts[0][0] + 0.15 * w]
            coef = fit(local if len(local) >= 2 else pts)
            # among fragments that continue the baseline, take the nearest one
            best, bgap = None, None
            for j, g in enumerate(frags):
                if j in used:
                    continue
                gap = last["x0"] - g["x1"]
                if gap < -0.05 * w or gap > JOIN_GAP * w:
                    continue
                gx, gy = g["baseline"][0]           # right end of the candidate
                dy = abs(np.polyval(coef, gx) - gy)
                if dy < JOIN_DY * lh and (bgap is None or gap < bgap):
                    best, bgap = j, gap
            if best is None:
                break
            chain.append(frags[best])
            used.add(best)
        chains.append(chain)

    lines = []
    for c in chains:
        bl = [p for f in c for p in f["baseline"]]
        lines.append({"baseline": bl, "parts": [f["boundary"] for f in c],
                      "x0": min(f["x0"] for f in c), "x1": max(f["x1"] for f in c),
                      "y0": min(f["y0"] for f in c), "y1": max(f["y1"] for f in c),
                      "fragments": len(c)})
    # page number: small, alone, in the bottom 6% of the page
    for L in lines:
        L["kind"] = "text"
        if L["y0"] > h * 0.94 and (L["x1"] - L["x0"]) < w * 0.08:
            L["kind"] = "page_number"
    # reading order: top to bottom by the baseline height at the line's right end
    lines.sort(key=lambda L: L["baseline"][0][1])
    text = [L for L in lines if L["kind"] == "text"]
    for i, L in enumerate(text):
        L["order"] = i + 1

    vis = img.copy()
    colors = [(0, 0, 255), (0, 160, 0), (255, 0, 0), (0, 140, 255)]
    for f in dropped:
        cv2.polylines(vis, [np.array(f["boundary"], np.int32)], True, (150, 150, 150), 3)
    for L in lines:
        c = (128, 0, 128) if L["kind"] != "text" else colors[L["order"] % 4]
        for part in L["parts"]:
            cv2.polylines(vis, [np.array(part, np.int32)], True, c, 3)
        cv2.polylines(vis, [np.array(L["baseline"], np.int32)], False, c, 3)
        label = str(L.get("order", "#"))
        if L["fragments"] > 1:
            label += f"({L['fragments']})"
        x, y = L["baseline"][0]
        cv2.putText(vis, label, (min(x + 10, w - 170), y), cv2.FONT_HERSHEY_SIMPLEX, 1.5, c, 4)
    vis = cv2.resize(vis, (w * 2 // 5, h * 2 // 5))
    cv2.imwrite(f"out/page{n}_lines2.png", vis)
    json.dump({"page": n, "line_height_px": lh,
               "lines": [{k: v for k, v in L.items() if k != "parts"} | {"polygons": L["parts"]} for L in lines],
               "dropped_edge_fragments": len(dropped)},
              open(f"out/page{n}_lines2.json", "w"))
    joined = sum(1 for L in text if L["fragments"] > 1)
    print(f"page {n}: {len(text)} text lines ({joined} joined from pieces), "
          f"{len(lines) - len(text)} page number, {len(dropped)} edge fragments dropped")


if __name__ == "__main__":
    for a in sys.argv[1:]:
        process(int(a))
