"""Spike: word error rate + punctuation agreement of a transcript against ground truth.

Usage: uv run python score.py out/p_115_ref_cpu.json ../test_files/p_115_truth_lines.txt
Only the stretch of the transcript that best matches the ground truth is scored.
"""
import json
import re
import sys

FINALS = str.maketrans("ךםןףץ", "כמנפצ")
PUNCT = ".,:;?!\"'״׳-–()"


def norm(w):
    w = re.sub(r"[֑-ׇ]", "", w)         # niqqud / cantillation
    w = w.strip(PUNCT)
    return w.translate(FINALS)


def tokens(text):
    """[(word, punctuation_after)]"""
    out = []
    for raw in text.split():
        m = re.match(r"^(.*?)([.,:;?!]*)$", raw)
        w, p = norm(m.group(1)), m.group(2)[:1]
        if w:
            out.append((w, p))
        elif p and out:
            out[-1] = (out[-1][0], p)
    return out


def align(a, b):
    """Levenshtein alignment of word lists; returns list of (i or None, j or None)."""
    n, m = len(a), len(b)
    D = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        D[i][0] = i
    for j in range(m + 1):
        D[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            D[i][j] = min(D[i - 1][j] + 1, D[i][j - 1] + 1, D[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
    i, j, path = n, m, []
    while i or j:
        if i and j and D[i][j] == D[i - 1][j - 1] + (a[i - 1] != b[j - 1]):
            path.append((i - 1, j - 1)); i -= 1; j -= 1
        elif i and D[i][j] == D[i - 1][j] + 1:
            path.append((i - 1, None)); i -= 1
        else:
            path.append((None, j - 1)); j -= 1
    return path[::-1]


def main():
    hyp_json, truth_file = sys.argv[1], sys.argv[2]
    segs = json.load(open(hyp_json))["segments"]
    hyp = tokens(" ".join(s["text"] for s in segs))
    ref = tokens(open(truth_file, encoding="utf-8").read())
    # best-matching window of the transcript (the recording covers the whole page)
    best = None
    for start in range(0, max(1, len(hyp) - len(ref) // 2)):
        win = hyp[start:start + len(ref) + 5]
        path = align([w for w, _ in ref], [w for w, _ in win])
        # trim trailing insertions
        while path and path[-1][0] is None:
            path.pop()
        cost = sum(1 for i, j in path if i is None or j is None or ref[i][0] != win[j][0])
        if best is None or cost < best[0]:
            best = (cost, start, path, win)
    cost, start, path, win = best
    subs = [(ref[i][0], win[j][0]) for i, j in path if i is not None and j is not None and ref[i][0] != win[j][0]]
    dels = [ref[i][0] for i, j in path if j is None]
    ins = [win[j][0] for i, j in path if i is None]
    print(f"WER {cost / len(ref):.1%}  ({len(subs)} substituted, {len(dels)} missing, {len(ins)} extra, of {len(ref)} words)")
    for r, h in subs:
        print(f"   {r}  ->  {h}")
    for r in dels:
        print(f"   missing: {r}")
    for h in ins:
        print(f"   extra: {h}")
    # punctuation: compare the mark after each aligned word
    agree = page_only = asr_only = differ = 0
    rows = []
    for i, j in path:
        if i is None or j is None:
            continue
        rp, hp = ref[i][1], win[j][1]
        if rp and hp:
            if rp == hp:
                agree += 1
            else:
                differ += 1; rows.append(f"   after {ref[i][0]}: page '{rp}'  model '{hp}'")
        elif rp:
            page_only += 1; rows.append(f"   after {ref[i][0]}: page '{rp}'  model none")
        elif hp:
            asr_only += 1; rows.append(f"   after {ref[i][0]}: page none  model '{hp}'")
    total = agree + differ + page_only
    print(f"punctuation: {agree}/{total} page marks matched exactly; {differ} different mark, "
          f"{page_only} missed, {asr_only} extra")
    for r in rows:
        print(r)


if __name__ == "__main__":
    main()
