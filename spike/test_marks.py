"""Spike: page punctuation on page 115 paragraph 1 vs. ground truth (and vs. Whisper)."""
import pathlib as _pathlib
ROOT = _pathlib.Path(__file__).resolve().parents[1]  # the repository
import re, shutil, sys, tempfile
from pathlib import Path
import cv2
sys.path.insert(0, f"{ROOT}/helper")
from notebook_helper import layout, marks as M, project as P
from notebook_helper.__main__ import m_get_page
from notebook_helper.review import _align_tokens

src = Path(f"{ROOT}/test_projects/FFV3.ntproj"); tmp = Path(tempfile.mkdtemp()) / "t.ntproj"
shutil.copytree(src, tmp)
m_get_page(str(tmp), 2)
pg = P.load_page(tmp, 2)
ink = layout.ink_mask(cv2.imread(str(tmp / "pages/002/page.png"), cv2.IMREAD_GRAYSCALE))
truth = [l.split() for l in open(f"{ROOT}/test_files/p_115_truth_lines.txt", encoding="utf-8").read().splitlines()]
PUN = ".,:;?!\"'()-"
def tail(t): return re.sub(r"^.*?([%s]*)$" % re.escape(PUN), r"\1", t)
tp = fp = fn = wrongtype = 0
asr_ok = 0
for li in range(7):
    L = pg["lines"][li]
    found = M.find_marks(ink, L, pg["line_height"])
    ws = L["words"]
    tw = truth[li]
    row = []
    pairs = {i: j for i, j in _align_tokens([w["text"] for w in ws], tw) if i is not None and j is not None}
    for k, w in enumerate(ws):
        page_mark = "".join(m for m in found.get(k, []) if m != "(")
        t = tail(tw[pairs[k]]) if k in pairs else ""
        if t and page_mark:
            tp += page_mark == t; wrongtype += page_mark != t
        elif page_mark and not t:
            fp += 1
        elif t and not page_mark:
            fn += 1
        if page_mark or t:
            row.append(f"{w['text']}[page:{page_mark or '-'} truth:{t or '-'}]")
    print(f"line {li + 1} ({len(ws)} words / truth {len(tw)}):", "  ".join(row))
print(f"\npage marks vs truth: {tp} right, {wrongtype} wrong type, {fn} missed, {fp} extra")
shutil.rmtree(tmp.parent)
