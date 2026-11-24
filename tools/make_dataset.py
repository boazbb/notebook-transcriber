"""Build a dataset from a notebook project: scans, recordings, transcription and word-level annotations.

Usage (from the repository root, with the helper's environment):
    helper/.venv/bin/python tools/make_dataset.py --project <name>.ntproj --sources <folder> --out dataset

--sources: a folder with the scans and other material to include, e.g.
    <stem>.pdf          the scan the project was made from (v1; the annotations are in its coordinates)
    <stem>_v2.pdf ...   other scans of the same pages (optional; same page order)
    *.m4a / *.wav       original recordings (optional, copied as they are)
    *.txt               transcripts / ground truth (optional; a file named full_transcript.txt is split per page)

The output folder holds private material (scans, voice, text). Keep it out of version control.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import shutil
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helper"))
from notebook_helper import project as P, review as R  # noqa: E402
from notebook_helper.hebrew import norm  # noqa: E402
from notebook_helper.marks import split_word  # noqa: E402

CROP_PAD = 0.15  # × line height around a word crop


# ---------- scans ----------

def render_native(pdf_path: Path, index: int) -> np.ndarray:
    page = pdfium.PdfDocument(str(pdf_path))[index]
    imgs = [o for o in page.get_objects() if o.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE]
    scale = imgs[0].get_px_size()[0] / page.get_size()[0] if imgs else 300 / 72
    return cv2.cvtColor(np.array(page.render(scale=scale).to_pil().convert("RGB")), cv2.COLOR_RGB2BGR)


class Transfer:
    """Maps page coordinates of scan v1 onto another scan of the same page: a homography from SIFT
    matches, plus a local correction (pages curve differently near the binding in each scan): each point
    is shifted by the weighted residual of the nearby, locally consistent matches."""

    def __init__(self, a_gray: np.ndarray, b_gray: np.ndarray, lh: float):
        from scipy.spatial import cKDTree
        sift = cv2.SIFT_create(8000)
        ka, da = sift.detectAndCompute(a_gray, None)
        kb, db = sift.detectAndCompute(b_gray, None)
        good = [x for x, y in cv2.BFMatcher().knnMatch(da, db, k=2) if x.distance < 0.75 * y.distance]
        self.ok = len(good) >= 50
        if not self.ok:
            return
        src = np.float32([ka[x.queryIdx].pt for x in good])
        dst = np.float32([kb[x.trainIdx].pt for x in good])
        self.H, _ = cv2.findHomography(src, dst, cv2.RANSAC, 6.0)
        res = dst - cv2.perspectiveTransform(src[None], self.H)[0]
        tree = cKDTree(src)
        keep = np.array([len(nb) >= 4 and np.linalg.norm(res[i] - np.median(res[nb], axis=0)) < 0.25 * lh
                         for i, nb in enumerate(tree.query_ball_point(src, 2.5 * lh))])
        self.src, self.res, self.lh = src[keep], res[keep], lh
        self.tree = cKDTree(self.src)
        self.matches = int(keep.sum())
        # leave-one-out error of the local model, as a quality figure
        errs = []
        for i in range(0, len(self.src), 5):
            d, idx = self.tree.query(self.src[i], k=min(13, len(self.src)))
            w = 1 / (d[1:] + lh * 0.3)
            errs.append(float(np.linalg.norm((self.res[idx[1:]] * w[:, None]).sum(0) / w.sum() - self.res[i])))
        self.error_px = float(np.median(errs)) if errs else None
        self.ok = self.matches >= 50

    def box(self, b: list[int]) -> list[int]:
        c = np.float32([(b[0] + b[2]) / 2, (b[1] + b[3]) / 2])
        d, idx = self.tree.query(c, k=min(12, len(self.src)))
        w = 1 / (d + self.lh * 0.3)
        shift = (self.res[idx] * w[:, None]).sum(0) / w.sum()
        pts = cv2.perspectiveTransform(np.float32([[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]])[None],
                                       self.H)[0] + shift
        return [int(pts[:, 0].min()), int(pts[:, 1].min()), int(pts[:, 0].max()), int(pts[:, 1].max())]


def crop(img: np.ndarray, b: list[int], pad: int) -> np.ndarray:
    h, w = img.shape[:2]
    return img[max(0, b[1] - pad):min(h, b[3] + pad), max(0, b[0] - pad):min(w, b[2] + pad)]


# ---------- ground-truth transcript, split per page ----------

def tokens_with_spans(text: str) -> list[tuple[str, int, int]]:
    return [(norm(m.group()), m.start(), m.end()) for m in re.finditer(r"\S+", text) if norm(m.group())]


def split_transcript(text: str, pages_words: dict[int, list[str]]):
    """Match the project's words (all pages in order) to the transcript's words; each page gets the part of
    the transcript between its first and last matched word, and each project word its transcript word."""
    gt = tokens_with_spans(text)
    seq, owner = [], []
    for n, ws in pages_words.items():
        for k, w in enumerate(ws):
            seq.append(norm(w))
            owner.append((n, k))
    sm = difflib.SequenceMatcher(None, seq, [t for t, _, _ in gt], autojunk=False)
    word_gt: dict[tuple[int, int], int] = {}
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        # equal runs, and replaced runs of the same length (a word spelled differently), pair word by word
        if op == "equal" or (op == "replace" and i2 - i1 == j2 - j1):
            for d in range(i2 - i1):
                word_gt[owner[i1 + d]] = j1 + d
    by_page: dict[int, tuple[int, int]] = {}
    for (n, k), j in word_gt.items():
        lo, hi = by_page.get(n, (j, j))
        by_page[n] = (min(lo, j), max(hi, j))
    slices = {n: text[gt[lo][1]:gt[hi][2]] for n, (lo, hi) in by_page.items()}
    gt_word = {key: text[gt[j][1]:gt[j][2]] for key, j in word_gt.items()}
    return slices, gt_word


DATASHEET = """# Notebook dataset — {project}

**Private.** Scans of handwritten pages, the writer's voice and the text of an unpublished story. Don't
upload, share or put under version control. Built {created} by `tools/make_dataset.py` (in the repository).

## Contents

- {pages} pages, {words} words; {verified} verified (corrected or accepted by the writer, or on a page marked
  finished: pages {finished}); {with_gt} paired with the writer's full transcript.
- Scans: {scans} (see below), all pages, at their native resolution.
- Each page's recording (16 kHz mono WAV), re-recorded lines, and earlier takes (`history/`).

```
README.md            this file
manifest.json        per page: status, finished, words, verified words, audio, scans; glossary; settings
words.csv            one row per word (all pages): see "Word fields"
sources/             the original files as given (scans as PDF, recordings, transcripts)
pages/NNN/           NNN = PDF page number (001 = the first page of the PDF)
  scan_v1.png        the scan the transcription was made on — all coordinates refer to it
  scan_v2.png ...    other scans of the same page (same page order in their PDF)
  annotations.json   lines (order, paragraph, outline, baseline, read / skipped / in a drawing) with their
                     words; drawings; chapter marks; page status
  text.txt           the page as transcribed in the app (notebook line breaks, blank line between paragraphs)
  transcript_gt.txt  this page's part of the writer's full transcript (if given)
  audio/             page.wav (the page read aloud), line-NN.wav (re-recorded lines)
  history/           earlier takes: their page.json and recordings
  words/             word crops: <id>_v1.png from scan v1, <id>_v2.png ... from the other scans
```

Sources:
{sources}

## Word fields (words.csv, and per page in annotations.json)

| Field | Meaning |
| --- | --- |
| page, line, id | PDF page, notebook line (1 = top), word id (stable within the page) |
| text | the word as transcribed, with punctuation; `letters` without it; `punct_before` / `punct_after` |
| asr_text | what the speech model (ivrit.ai Whisper large-v3-turbo) originally wrote for it |
| gt_text | the same word in the writer's full transcript (empty when it couldn't be paired) |
| bbox_v1 | the word's handwriting, [x0, y0, x1, y1] in scan v1 pixels; empty when no box matched |
| bbox_v2, bbox_v3 ... | the same box transferred to the other scans (see below) |
| t0, t1, audio | the word in the recording (seconds), and which recording (`audio/page.wav` or a line clip) |
| speech_p | the speech model's confidence (0–1) |
| band | the app's confidence colour: confident / check / unsure |
| edited, accepted | the writer corrected / accepted the word; `verified` = edited, accepted or on a finished page |
| crop_v1, crop_v2 ... | the word crops (paths relative to this folder) |

## How it was made

- Transcribed by reading each page aloud; the app matched the spoken words to the handwriting (word boxes
  from gaps between ink, matched by expected word width) and placed them on the notebook's lines.
- The writer reviewed the text in the app, faithful to the page: the writer's own spelling kept, words
  misread aloud corrected to what is written.
- Boxes on the other scans: SIFT matches between scan v1 and the other scan, a homography, plus a local
  correction from nearby matches (the pages curve differently near the binding in each scan). Estimated
  error about 1 px (per page in annotations.json, `scans`).

## Limits

- Word boxes come from the app; the writer checked the **text**, not every box edge: some boxes hold part of
  a word or a piece of the next one (more often on words written close together). Verified words have
  verified text, not necessarily verified boxes.
- Words the speech model heard but that aren't written get no box; written words not read aloud (crossed-out
  words, notes to self) have boxes but no word.
- Punctuation: from the speech model and marks read on the page, partly corrected by the writer.
- One writer, one notebook, one pen; scans from phone apps (v1/v2: Google Drive with its filter, v3: Genius
  Scan, no filter, full size).
"""


# ---------- the dataset ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, type=Path)
    ap.add_argument("--sources", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()
    proj, src, out = a.project.resolve(), a.sources.resolve(), a.out.resolve()
    info = P.read_json(proj / "project.json")
    if out.exists():
        shutil.rmtree(out)
    (out / "sources").mkdir(parents=True)
    t0 = time.time()

    # sources: copied as they are
    for f in sorted(src.iterdir()):
        if f.is_file() and f.suffix.lower() in (".pdf", ".m4a", ".wav", ".mp3", ".txt"):
            shutil.copy2(f, out / "sources" / f.name)
    stem = Path(info.get("name", "scan")).stem
    v1 = src / f"{stem}.pdf"
    other_scans = {p.stem[len(stem) + 1:]: p for p in sorted(src.glob(f"{stem}_*.pdf"))}  # e.g. "v2", "v3"
    print(f"project: {info['pages']} pages; scans: v1 + {sorted(other_scans)}")

    pages_words: dict[int, list[str]] = {}
    pages = {}
    for n in range(1, info["pages"] + 1):
        try:
            pages[n] = P.load_page(proj, n)
        except FileNotFoundError:
            continue
        R.ensure_ids(pages[n])
        pages_words[n] = [w["text"] for L in pages[n].get("lines", []) for w in L.get("words", [])]

    gt_file = src / "full_transcript.txt"
    slices, gt_word = ({}, {})
    if gt_file.exists():
        slices, gt_word = split_transcript(gt_file.read_text(encoding="utf-8"), pages_words)

    rows, manifest_pages = [], []
    for n, pg in pages.items():
        d = out / "pages" / f"{n:03d}"
        (d / "words").mkdir(parents=True)
        pd = P.page_dir(proj, n)
        img1 = cv2.imread(str(pd / "page.png"))
        shutil.copy2(pd / "page.png", d / "scan_v1.png")
        lh = pg.get("line_height") or 40
        transfers, scans = {}, {}
        for tag, pdf in other_scans.items():
            try:
                imgx = render_native(pdf, n - 1)
            except Exception:
                continue
            cv2.imwrite(str(d / f"scan_{tag}.png"), imgx)
            tr = Transfer(cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY), cv2.cvtColor(imgx, cv2.COLOR_BGR2GRAY), lh)
            scans[tag] = {"size": [imgx.shape[1], imgx.shape[0]],
                          "boxes_transferred": tr.ok, "matches": getattr(tr, "matches", 0),
                          "transfer_error_px": getattr(tr, "error_px", None)}
            if tr.ok:
                transfers[tag] = (tr, imgx)
        if (pd / "audio").exists():
            shutil.copytree(pd / "audio", d / "audio")
        if (pd / "history").exists():
            shutil.copytree(pd / "history", d / "history")
        transcribed = pg.get("status") == "transcribed"
        if transcribed:
            (d / "text.txt").write_text(P.page_text(pg) + "\n", encoding="utf-8")
        if n in slices:
            (d / "transcript_gt.txt").write_text(slices[n].strip() + "\n", encoding="utf-8")

        finished = bool(pg.get("finished"))
        asr_by_id = {w.get("id"): w for L in pg.get("lines", []) for w in L.get("asr_words", [])}
        lines_out, k_all = [], 0
        for L in pg.get("lines", []):
            words_out = []
            for w in L.get("words", []):
                key = (n, k_all)
                k_all += 1
                bs = [L["boxes"][b] for b in w.get("boxes", []) if b < len(L["boxes"])]
                bbox = [min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)] if bs else None
                verified = bool(w.get("edited") or w.get("accepted") or finished
                                or (w.get("punct") or {}).get("status") == "user")
                asr = w.get("auto_from") or w.get("glossary_from") or asr_by_id.get(w.get("id"), {}).get("text") or w["text"]
                lead, core, tail = split_word(w["text"])
                rec = {"id": w["id"], "text": w["text"], "letters": core, "punct_after": tail, "punct_before": lead,
                       "asr_text": asr, "gt_text": gt_word.get(key), "bbox_v1": bbox,
                       "t0": w.get("t0"), "t1": w.get("t1"), "audio": w.get("audio", "audio/page.wav"),
                       "speech_p": w.get("p"), "band": R.band(w, False),
                       "edited": bool(w.get("edited")), "accepted": bool(w.get("accepted")), "verified": verified}
                crops = {}
                if bbox:
                    p1 = int(CROP_PAD * lh)
                    cv2.imwrite(str(d / "words" / f"{w['id']}_v1.png"), crop(img1, bbox, p1))
                    crops["v1"] = f"pages/{n:03d}/words/{w['id']}_v1.png"
                    for tag, (tr, imgx) in transfers.items():
                        bx = tr.box(bbox)
                        rec[f"bbox_{tag}"] = bx
                        px = int(CROP_PAD * lh * imgx.shape[1] / img1.shape[1])
                        cv2.imwrite(str(d / "words" / f"{w['id']}_{tag}.png"), crop(imgx, bx, px))
                        crops[tag] = f"pages/{n:03d}/words/{w['id']}_{tag}.png"
                rec["crops"] = crops
                words_out.append(rec)
                rows.append({"page": n, "line": L["order"], **{k: v for k, v in rec.items() if k != "crops"},
                             **{f"crop_{k}": v for k, v in crops.items()}})
            lines_out.append({"order": L["order"], "paragraph": L.get("para_text", L.get("paragraph")),
                              "read": bool(L.get("read")), "skip": bool(L.get("skip")),
                              "in_drawing": bool(L.get("in_drawing")), "bbox": L.get("bbox"),
                              "baseline": L.get("baseline"), "polygons": L.get("polygons"), "words": words_out})
        ann = {"page": n, "status": pg.get("status"), "finished": finished, "size_v1": pg.get("size"),
               "line_height_v1": lh, "scans": {"v1": {"size": pg.get("size")}, **scans},
               "drawings": pg.get("drawings", []), "chapter_starts": pg.get("chapter_starts", []),
               "audio_duration": (pg.get("audio") or {}).get("duration"), "lines": lines_out}
        (d / "annotations.json").write_text(json.dumps(ann, ensure_ascii=False, indent=1), encoding="utf-8")
        nw = sum(len(L["words"]) for L in lines_out)
        nv = sum(1 for L in lines_out for w in L["words"] if w["verified"])
        manifest_pages.append({"page": n, "status": pg.get("status"), "finished": finished, "words": nw,
                               "verified_words": nv, "has_audio": (pd / "audio" / "page.wav").exists(),
                               "scans": sorted(["v1", *scans])})
        print(f"page {n:2d}: {nw:3d} words ({nv} verified), scans {sorted(['v1', *scans])}"
              + "".join(f", {t} boxes: {s['matches']} matches, ±{s['transfer_error_px']:.1f}px" for t, s in scans.items()
                        if s.get("transfer_error_px") is not None))

    with open(out / "words.csv", "w", newline="", encoding="utf-8") as f:
        cols = sorted({k for r in rows for k in r}, key=lambda k: (k not in ("page", "line", "id", "text"), k))
        wr = csv.DictWriter(f, fieldnames=cols)
        wr.writeheader()
        for r in rows:
            wr.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v) for k, v in r.items()})
    manifest = {"created": time.strftime("%Y-%m-%d %H:%M"), "project": info.get("name"), "pages": manifest_pages,
                "words": len(rows), "verified_words": sum(1 for r in rows if r["verified"]),
                "words_with_gt": sum(1 for r in rows if r.get("gt_text")),
                "glossary": info.get("glossary", []), "settings": info.get("settings", {})}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "README.md").write_text(DATASHEET.format(
        created=manifest["created"], project=info.get("name"), pages=len(manifest_pages), words=len(rows),
        verified=manifest["verified_words"], with_gt=manifest["words_with_gt"],
        finished=", ".join(str(p["page"]) for p in manifest_pages if p["finished"]) or "none",
        scans=", ".join(["v1", *sorted(other_scans)]),
        sources="\n".join(f"- `{f.name}`" for f in sorted((out / "sources").iterdir()))), encoding="utf-8")
    print(f"done in {time.time() - t0:.0f}s: {len(rows)} words, {manifest['verified_words']} verified, "
          f"{manifest['words_with_gt']} matched to the full transcript")


if __name__ == "__main__":
    main()
