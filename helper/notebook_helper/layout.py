"""Stages 1-2: page image, lines (Kraken + joining), paragraphs, word boxes.

All size thresholds are relative to the line height, so the scan's resolution doesn't matter.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium
from skimage.filters import threshold_sauvola

HELPER_DIR = Path(__file__).resolve().parent.parent
KRAKEN = HELPER_DIR / "kraken_env" / ".venv" / "bin" / "kraken"

EDGE = 0.035        # outer strip (fraction of width) treated as facing-page bleed
JOIN_DY = 0.4       # max baseline mismatch when joining line pieces, in line heights
JOIN_GAP = 0.45     # max horizontal gap when joining line pieces, in page widths
PARA_GAP = 1.6      # line spacing above this multiple of the normal spacing starts a paragraph


# ---------- page image ----------

def extract_page_image(pdf_path: Path, index: int, out_png: Path) -> tuple[int, int]:
    """Save PDF page `index` (0-based) as PNG at the native resolution of its scan."""
    pdf = pdfium.PdfDocument(str(pdf_path))
    page = pdf[index]
    w_pt = page.get_size()[0]
    scale = 300 / 72
    imgs = [o for o in page.get_objects() if o.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE]
    if imgs:
        px_w = max(o.get_px_size()[0] for o in imgs)
        scale = min(max(px_w / w_pt, 100 / 72), 400 / 72)
    img = page.render(scale=scale).to_pil().convert("RGB")
    img.save(out_png)
    return img.size


def page_count(pdf_path: Path) -> int:
    return len(pdfium.PdfDocument(str(pdf_path)))


def ink_mask(gray: np.ndarray) -> np.ndarray:
    th = threshold_sauvola(gray, window_size=51, k=0.2)
    return (gray < th).astype(np.uint8)


# ---------- lines ----------

def run_kraken(png: Path) -> dict:
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "lines.json"
        r = subprocess.run([str(KRAKEN), "-i", str(png), str(out), "--native", "segment", "-bl",
                            "-d", "horizontal-rl"], capture_output=True, text=True)
        if r.returncode != 0 or not out.exists():
            raise RuntimeError(f"Kraken failed: {r.stderr[-500:]}")
        return json.loads(out.read_text())


def _fit(points):
    xs = np.array([p[0] for p in points], float)
    ys = np.array([p[1] for p in points], float)
    if len(points) > 1 and np.ptp(xs) > 0:
        return np.polyfit(xs, ys, 1)
    return np.array([0.0, ys.mean()])


def join_lines(kraken: dict, w: int, h: int) -> tuple[list[dict], float]:
    """Kraken line pieces -> notebook lines (joined, edge bleed dropped, page number flagged)."""
    frags = []
    for l in kraken["lines"]:
        bl = sorted(([int(p[0]), int(p[1])] for p in l["baseline"]), key=lambda p: -p[0])
        xs = [p[0] for p in l["boundary"]]
        ys = [p[1] for p in l["boundary"]]
        frags.append({"baseline": bl, "boundary": [[int(x), int(y)] for x, y in l["boundary"]],
                      "x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys)})
    ys_mid = sorted(np.mean([p[1] for p in f["baseline"]]) for f in frags)
    lh = float(np.median(np.diff(ys_mid))) if len(ys_mid) > 2 else 0.03 * h
    lh = max(lh, 0.012 * h)

    frags = [f for f in frags if not (f["x1"] < w * EDGE * 1.6 or f["x0"] > w * (1 - EDGE * 1.6))]
    frags.sort(key=lambda f: -f["x1"])
    used: set[int] = set()
    chains = []
    for i, f in enumerate(frags):
        if i in used:
            continue
        chain = [f]
        used.add(i)
        while True:
            last = chain[-1]
            pts = sorted((p for g in chain for p in g["baseline"]), key=lambda p: p[0])
            local = [p for p in pts if p[0] <= pts[0][0] + 0.15 * w]
            coef = _fit(local if len(local) >= 2 else pts)
            best, bgap = None, None
            for j, g in enumerate(frags):
                if j in used:
                    continue
                gap = last["x0"] - g["x1"]
                if gap < -0.05 * w or gap > JOIN_GAP * w:
                    continue
                gx, gy = g["baseline"][0]
                if abs(np.polyval(coef, gx) - gy) < JOIN_DY * lh and (bgap is None or gap < bgap):
                    best, bgap = j, gap
            if best is None:
                break
            chain.append(frags[best])
            used.add(best)
        chains.append(chain)

    lines = []
    for c in chains:
        x0, x1 = min(f["x0"] for f in c), max(f["x1"] for f in c)
        y0, y1 = min(f["y0"] for f in c), max(f["y1"] for f in c)
        kind = "page_number" if y0 > h * 0.94 and (x1 - x0) < w * 0.08 else "text"
        lines.append({"baseline": [p for f in c for p in f["baseline"]],
                      "polygons": [f["boundary"] for f in c],
                      "bbox": [int(x0), int(y0), int(x1), int(y1)], "kind": kind})
    lines.sort(key=lambda L: L["baseline"][0][1])
    return lines, lh


def mark_paragraphs(lines: list[dict], lh: float) -> None:
    """Number paragraphs: a gap clearly larger than normal line spacing starts a new one."""
    para, prev = 1, None
    for L in lines:
        y = float(np.median([p[1] for p in L["baseline"]]))
        if prev is not None and y - prev > PARA_GAP * lh:
            para += 1
        L["paragraph"] = para
        prev = y


# ---------- word boxes ----------

def _baseline_y(baseline, x):
    bl = sorted(baseline, key=lambda p: p[0])
    return float(np.interp(x, [p[0] for p in bl], [p[1] for p in bl]))


def _is_mark(c, lh, baseline) -> bool:
    """Punctuation-like: small and at or below the baseline (unlike the letter י, which sits high)."""
    x, y, w, h, area = (int(v) for v in c)
    if area < 0.002 * lh * lh:
        return False
    if area < 0.02 * lh * lh and h < 0.3 * lh:
        return (y + h / 2) > _baseline_y(baseline, x + w / 2) - 0.3 * lh
    if h < 0.45 * lh and w < 0.25 * lh:
        return (y + h / 2) > _baseline_y(baseline, x + w / 2) - 0.15 * lh
    return False


def _runs(ink, line, lh):
    mask = np.zeros_like(ink)
    for p in line["polygons"]:
        cv2.fillPoly(mask, [np.array(p, np.int32)], 1)
    n, _, stats, _ = cv2.connectedComponentsWithStats(ink & mask, connectivity=8)
    comps = [stats[i] for i in range(1, n)]
    big = [c for c in comps if c[4] >= 0.002 * lh * lh and not _is_mark(c, lh, line["baseline"])]
    spans = sorted(([int(c[0]), int(c[0] + c[2]), int(c[1]), int(c[1] + c[3])] for c in big),
                   key=lambda s: -s[1])
    runs: list[list[int]] = []
    for s in spans:  # right to left
        if runs and s[1] >= runs[-1][0] - 2:
            r = runs[-1]
            r[0], r[2], r[3] = min(r[0], s[0]), min(r[2], s[2]), max(r[3], s[3])
        else:
            runs.append(s)
    gaps = [runs[i][0] - runs[i + 1][1] for i in range(len(runs) - 1)]
    return runs, gaps


def _otsu(values, default):
    g = np.array([v for v in values if v > 0], float)
    if len(g) < 4:
        return default
    best, bt = -1.0, default
    for t in np.unique(g):
        a, b = g[g < t], g[g >= t]
        if len(a) and len(b):
            v = len(a) * len(b) * (a.mean() - b.mean()) ** 2
            if v > best:
                best, bt = v, float(t)
    return bt


def word_boxes(ink: np.ndarray, lines: list[dict], lh: float) -> float:
    """Add candidate word boxes [x0, y0, x1, y1] (right to left) to each text line. Returns the gap threshold.

    The threshold between letter gaps and word gaps is learned per page (Otsu on the gap widths). Very
    wide gaps (next to a drawing, a short line) are capped first: one 8-line-heights gap once made Otsu
    split it off from everything else, leaving 1-2 boxes per line. The result is kept in a sane range."""
    data = [(L, *_runs(ink, L, lh)) for L in lines]
    gaps = [min(g, 1.2 * lh) for _, _, gs in data for g in gs]
    thr = min(max(_otsu(gaps, 0.45 * lh), 0.25 * lh), 0.75 * lh)
    for L, runs, gaps in data:
        words: list[list[int]] = []
        for i, r in enumerate(runs):
            if i == 0 or gaps[i - 1] >= thr:
                words.append(list(r))
            else:
                w = words[-1]
                w[0], w[2], w[3] = min(w[0], r[0]), min(w[2], r[2]), max(w[3], r[3])
        L["boxes"] = [[w[0], w[2], w[1], w[3]] for w in words]
    return thr


def split_box(ink: np.ndarray, box: list[int], shares: list[int], line_height: float) -> list[list[int]]:
    """Split one word box that holds several spoken words (written close together).

    `shares` are the words' letter counts in reading order (right to left). Each cut goes to the column
    with the least ink within ±20% of the box width around the position the letter counts predict, so it
    falls in the gap between the words. Each piece is then shrunk vertically to its own ink.
    """
    x0, y0, x1, y1 = box
    region = ink[y0:y1, x0:x1]
    if region.size == 0 or len(shares) < 2:
        return [box]
    cols = region.sum(axis=0).astype(float)
    k = max(3, int(line_height * 0.08)) | 1
    cols_s = np.convolve(cols, np.ones(k) / k, mode="same")
    width = x1 - x0
    total = sum(shares)
    cuts = []  # x positions, right to left
    acc = 0
    for s in shares[:-1]:
        acc += s
        expect = x1 - width * acc / total
        lo = int(max(x0 + 2, expect - 0.2 * width, (cuts[-1] - 2) if cuts else x0))
        hi = int(min(x1 - 2, expect + 0.2 * width, (cuts[-1] - 2) if cuts else x1))
        if hi <= lo:
            cuts.append(int(expect))
            continue
        window = cols_s[lo - x0:hi - x0]
        # least ink; among ties, closest to the expected position
        best = min(range(len(window)), key=lambda i: (window[i], abs(lo + i - expect)))
        cuts.append(lo + best)
    edges = [x1] + cuts + [x0]
    pieces = []
    for right, left in zip(edges[:-1], edges[1:]):
        sub = ink[y0:y1, left:right]
        rows = np.nonzero(sub.sum(axis=1))[0]
        cols_i = np.nonzero(sub.sum(axis=0))[0]
        if len(rows) and len(cols_i):
            pieces.append([int(left + cols_i[0]), int(y0 + rows[0]), int(left + cols_i[-1] + 1), int(y0 + rows[-1] + 1)])
        else:
            pieces.append([int(left), y0, int(right), y1])
    return pieces


def analyse_page(png: Path) -> dict:
    """Full layout of one page image -> {'line_height', 'lines': [...]} in reading order."""
    img = cv2.imread(str(png))
    h, w = img.shape[:2]
    lines, lh = join_lines(run_kraken(png), w, h)
    text = [L for L in lines if L["kind"] == "text"]
    mark_paragraphs(text, lh)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ink, _ = remove_ruling(ink_mask(gray), lh, gray)  # scans without a "document" filter keep the ruling
    word_boxes(ink, text, lh)
    for i, L in enumerate(text):
        L["order"] = i + 1
        L["id"] = f"l{i + 1}"
    drawings = [{"id": f"d{k}", "bbox": b, "source": "auto"} for k, b in enumerate(find_drawings(ink, text, lh, w, h))]
    inside = lines_in_drawings(text, [d["bbox"] for d in drawings])
    for L in text:
        L["in_drawing"] = L["id"] in inside
    return {"size": [w, h], "line_height": lh, "lines": text, "drawings": drawings,
            "page_number_boxes": [L["bbox"] for L in lines if L["kind"] == "page_number"]}


# ---------- drawings ----------

def find_drawings(ink: np.ndarray, lines: list[dict], lh: float, w: int, h: int) -> list[list[int]]:
    """Regions of ink that isn't text: everything inside the text lines' outlines is removed, the rest is
    joined (strokes of one sketch close together) and kept when it is big enough to be a drawing.
    The facing page's edge strip and the bottom margin (page number) are ignored. Returns boxes."""
    rest = ink.copy()
    for L in lines:
        for p in L["polygons"]:
            cv2.fillPoly(rest, [np.array(p, np.int32)], 0)
    edge = int(w * EDGE * 1.6)
    rest[:, :edge] = 0
    rest[:, w - edge:] = 0
    rest[int(h * 0.95):, :] = 0
    # drop small fragments (bits of letters sticking out of their line's outline): they would bridge a
    # doodle and the text next to it
    n0, lab0, st0, _ = cv2.connectedComponentsWithStats(rest, connectivity=8)
    keep = st0[:, cv2.CC_STAT_AREA] >= 0.08 * lh * lh
    keep[0] = False
    rest = keep[lab0].astype(np.uint8)
    k = max(3, int(lh * 0.35))
    joined = cv2.dilate(rest, np.ones((k, k), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(joined, connectivity=8)
    boxes = []
    for i in range(1, n):
        x, y, bw, bh, _ = (int(v) for v in stats[i])
        ink_px = int(rest[y:y + bh, x:x + bw].sum())
        # at least two lines tall: circled chapter numbers and strips of stray ink are smaller
        if bh >= 2.0 * lh and bw >= 1.0 * lh and ink_px >= 0.25 * lh * lh:
            pad = k // 2
            boxes.append([max(0, x + pad), max(0, y + pad), min(w, x + bw - pad), min(h, y + bh - pad)])
    return boxes


def lines_in_drawings(lines: list[dict], drawings: list[list[int]]) -> set[str]:
    """Lines lying mostly inside a drawing (Kraken sometimes finds a 'line' in a sketch)."""
    out = set()
    for L in lines:
        x0, y0, x1, y1 = L["bbox"]
        area = max(1, (x1 - x0) * (y1 - y0))
        for d in drawings:
            ix = max(0, min(x1, d[2]) - max(x0, d[0]))
            iy = max(0, min(y1, d[3]) - max(y0, d[1]))
            if ix * iy > 0.5 * area:
                out.add(L["id"])
    return out


# ---------- notebook ruling ----------

def remove_ruling(ink: np.ndarray, lh: float, gray: np.ndarray) -> tuple[np.ndarray, int]:
    """Remove the notebook's ruled lines from an ink mask (scans without a 'document' filter keep them; they
    link the words of a line together and leave dots on the baseline).

    Long thin horizontal segments are found and grouped into ruled lines (segments at the same height,
    together at least a third of the page wide); a straight line is fitted through each. In a band along
    it, ink that is lighter than the pen is removed: the ruling is printed light, the pen is dark, so the
    base strokes of letters lying on the ruling (ב, כ, נ) stay. Returns (cleaned mask, number of rulings)."""
    from skimage.filters import threshold_otsu
    h, w = ink.shape
    k = max(25, int(lh * 1.5))
    bridged = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((3, 1), np.uint8))
    long_ = cv2.morphologyEx(bridged, cv2.MORPH_OPEN, np.ones((1, k), np.uint8))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(long_, connectivity=8)
    segs = sorted((float(cent[i][1]), i) for i in range(1, n) if stats[i][3] <= 0.15 * lh)
    groups: list[list[int]] = []
    last_y = None
    for y, i in segs:
        if groups and last_y is not None and y - last_y < 0.25 * lh:
            groups[-1].append(i)
        else:
            groups.append([i])
        last_y = y
    groups = [g for g in groups if sum(int(stats[i][2]) for i in g) >= 0.3 * w]
    if not groups:
        return ink, 0
    ink_vals = gray[ink.astype(bool)]
    pen = threshold_otsu(ink_vals) if ink_vals.size > 100 else 0   # pen (dark) vs ruling (light) ink
    out = ink.copy()
    band = int(max(4, 0.08 * lh))
    xs_all = np.arange(w)
    for g in groups:
        ys, xs = np.nonzero(np.isin(lab, g))
        a, b = np.polyfit(xs, ys, 1)
        yc = np.round(a * xs_all + b).astype(int)
        for dy in range(-band, band + 1):
            y = yc + dy
            ok = (y >= 0) & (y < h)
            yy, xx = y[ok], xs_all[ok]
            light = gray[yy, xx] > pen
            out[yy[light], xx[light]] = 0
    return out, len(groups)
