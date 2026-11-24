"""Overlay Kraken lines on a page: out/pageN_kraken.png (small)."""
import json, sys
import cv2, numpy as np
colors = [(0, 0, 255), (0, 160, 0), (255, 0, 0), (0, 140, 255)]
for n in map(int, sys.argv[1:]):
    d = json.load(open(f"out/page{n}_kraken.json"))
    img = cv2.imread(f"out/page{n}.png")
    lines = d["lines"]
    # reading order: top to bottom by mean baseline y
    lines.sort(key=lambda l: np.mean([p[1] for p in l["baseline"]]))
    for i, l in enumerate(lines):
        c = colors[i % 4]
        cv2.polylines(img, [np.array(l["boundary"], np.int32)], True, c, 3)
        cv2.polylines(img, [np.array(l["baseline"], np.int32)], False, c, 2)
        x, y = max(l["baseline"], key=lambda p: p[0])
        cv2.putText(img, str(i + 1), (min(x + 10, img.shape[1] - 80), y), cv2.FONT_HERSHEY_SIMPLEX, 1.6, c, 4)
    img = cv2.resize(img, (img.shape[1] * 2 // 5, img.shape[0] * 2 // 5))
    cv2.imwrite(f"out/page{n}_kraken.png", img)
    print(n, len(lines), "lines")
