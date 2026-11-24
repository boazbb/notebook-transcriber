"""Compare scans of pages 114-116 end to end against the ground truth (test_files/gt_114_116.txt).

For each scan: import, find lines, transcribe pages 1-3 with the recordings (sound-alike check off: it
doesn't depend on the scan), then score
  - punctuation read from the page alone, and the final merged punctuation, against the ground truth
  - words (the same audio for every scan; differences come only from word placement / punctuation merge)
Usage: uv run python compare_scans.py scan1.pdf [scan2.pdf ...]
"""
import pathlib as _pathlib
ROOT = _pathlib.Path(__file__).resolve().parents[1]  # the repository
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, f"{ROOT}/helper")
from notebook_helper import handwriting as H, marks as M, project as P  # noqa: E402
from notebook_helper.hebrew import norm  # noqa: E402
from notebook_helper.review import _align_tokens  # noqa: E402

GT = Path(f"{ROOT}/test_files/gt_114_116.txt")
REC = {1: "p_114", 2: "p_115", 3: "p_116"}
PUN = ".,:;?!"


def tail(t):
    m = re.search(r"([%s]+)[\"'״)\]]*$" % re.escape(PUN), t)
    return m.group(1)[0] if m else ""


def score_marks(hyp_words, hyp_marks, ref_words):
    """hyp_words: words in reading order; hyp_marks: mark after each ('' or one char)."""
    ref_marks = [tail(w) for w in ref_words]
    path = _align_tokens(hyp_words, ref_words)
    r = wt = mi = ex = 0
    for i, j in path:
        if i is None or j is None:
            continue
        h, t = hyp_marks[i], ref_marks[j]
        if h and t:
            r += h == t
            wt += h != t
        elif h:
            ex += 1
        elif t:
            mi += 1
    total = sum(1 for m in ref_marks if m)
    return r, wt, mi, ex, total


def word_errors(hyp, ref):
    a, b = [norm(w) for w in hyp], [norm(w) for w in ref]
    return sum(1 for i, j in _align_tokens(a, b) if i is None or j is None or a[i] != b[j])


def run(pdf: Path):
    tmp = Path(tempfile.mkdtemp()) / "c.ntproj"
    P.create_project(pdf, tmp, lambda p: None)
    st = P.get_settings(tmp)
    st["check_soundalikes"] = False
    P.save_settings(tmp, st)
    info = {}
    hyp_words, hyp_final, page_marks = [], [], []
    for n in (1, 2, 3):
        pg = P.analyse(tmp, n)
        P.set_audio_file(tmp, n, Path(f"{ROOT}/test_files/{REC[n]}.m4a"))
        pg = P.transcribe_page(tmp, n, 1, [], [], lambda p: None)
        ink = H.page_ink(P.page_dir(tmp, n) / "page.png")
        read = [L for L in pg["lines"] if L.get("read")]
        info[n] = (len(pg["lines"]), len(read), round(pg["line_height"]), len(pg.get("drawings", [])))
        for L in read:
            found = M.find_marks(ink, L, pg["line_height"])
            for k, w in enumerate(L["words"]):
                hyp_words.append(M.split_word(w["text"])[1])
                hyp_final.append(tail(w["text"]))
                pm = [m for m in found.get(k, []) if m in PUN]
                page_marks.append(pm[0] if pm else "")
    shutil.rmtree(tmp.parent)
    return info, hyp_words, hyp_final, page_marks


def main():
    ref = GT.read_text(encoding="utf-8").split()
    ref_words = [re.sub(r"^[\"'״(\[]+|[\"'״)\].,:;?!\-]+$", "", w) for w in ref]
    for f in sys.argv[1:]:
        t = time.time()
        info, hw, hf, pm = run(Path(f))
        print(f"\n=== {Path(f).name} ({time.time() - t:.0f}s)")
        for n, (nl, nr, lh, nd) in info.items():
            print(f"  page {113 + n}: {nl} lines, {nr} read, line height {lh}px, drawings {nd}")
        print(f"  word errors vs ground truth: {word_errors(hw, ref_words)} of {len(ref)} words")
        for label, marks in (("page marks alone   ", pm), ("final (merged) text", hf)):
            r, wt, mi, ex, tot = score_marks(hw, marks, ref)
            print(f"  {label}: {r}/{tot} right, {wt} wrong type, {mi} missed, {ex} extra")


if __name__ == "__main__":
    main()
