"""Spike test: sound-alike correction on page 115 (paragraph 1 has ground truth)."""
import pathlib as _pathlib
ROOT = _pathlib.Path(__file__).resolve().parents[1]  # the repository
import json, sys, time
sys.path.insert(0, f"{ROOT}/helper")
sys.path.insert(0, f"{ROOT}/spike")
from notebook_helper import soundalike as S
from score import tokens, align

page = json.load(open(f"{ROOT}/test_projects/FFV3.ntproj/pages/002/page.json"))
words = [w["text"] for L in page["lines"] for w in L.get("words", [])]
t = time.time()
res = S.correct(words, progress=None)
print(f"{len(words)} words checked in {time.time() - t:.1f}s (incl. model load) on {S._device}")
fixed = [r["text"] for r in res]
truth = open(f"{ROOT}/test_files/p_115_truth_lines.txt", encoding="utf-8").read()
ref = [w for w, _ in tokens(truth)]
for name, ws in (("before", words), ("after", fixed)):
    hyp = [w for w, _ in tokens(" ".join(ws[:len(ref) + 2]))]
    path = align(ref, hyp)
    while path and path[-1][0] is None:
        path.pop()
    errs = [(ref[i] if i is not None else "-", hyp[j] if j is not None else "-") for i, j in path
            if i is None or j is None or ref[i] != hyp[j]]
    print(f"paragraph 1 {name}: {len(errs)} word errors of {len(ref)}  {errs}")
print("\nall changes on the page:")
for r in res:
    if r["from"]:
        print(f"   {r['from']:12s} -> {r['text']:12s} (gain {r['gain']})")
