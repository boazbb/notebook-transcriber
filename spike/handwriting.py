"""Spike: can the handwriting decide between sound-alike spellings (טועה / תועה)?

Letter classifier trained on the user's own words. Each word's letters are located by position
(the box split proportionally to letter widths), cropped, and classified (HOG + logistic regression).
For a candidate pair, compare log P(letter) of the two spellings at the letters where they differ.

Evaluation (no hand labels needed): train on 2 pages, test on the 3rd. For every confidently read test
word and every letter that has a sound-alike, swap it; the check should prefer the original.
Then the real suggestions with known answers.
"""
import pathlib as _pathlib
ROOT = _pathlib.Path(__file__).resolve().parents[1]  # the repository
import shutil
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
from skimage.feature import hog
from sklearn.linear_model import LogisticRegression

sys.path.insert(0, f"{ROOT}/helper")
from notebook_helper import layout, project as P  # noqa: E402
from notebook_helper.__main__ import m_get_page  # noqa: E402
from notebook_helper.hebrew import NIQQUD  # noqa: E402

LETTERS = "אבגדהוזחטיכךלמםנןסעפףצץקרשת"
NARROW = {"י": 0.45, "ו": 0.5, "ן": 0.5, "ז": 0.6, "ג": 0.7, "נ": 0.7}
SWAP = {"ס": "ש", "ש": "ס", "ת": "ט", "ט": "ת", "כ": "חק", "ח": "כ", "ק": "כ", "ב": "ומ", "ו": "ב",
        "א": "עה", "ע": "אה", "ה": "אע", "מ": "ב"}
S = 32


def letters_of(text):
    t = NIQQUD.sub("", text)
    return "".join(c for c in t if c in LETTERS)


def letter_crops(ink, box, word):
    """One S×S crop per letter, located by proportional letter widths (right to left)."""
    x0, y0, x1, y1 = box
    H = y1 - y0
    if H < 8 or x1 - x0 < 4:
        return None
    ws = np.array([NARROW.get(c, 1.0) for c in word])
    edges = x1 - np.concatenate([[0], np.cumsum(ws)]) / ws.sum() * (x1 - x0)
    out = []
    for i in range(len(word)):
        cx = (edges[i] + edges[i + 1]) / 2
        half = max((edges[i] - edges[i + 1]) * 0.75, H * 0.25)
        a, b = int(max(0, cx - half)), int(min(ink.shape[1], cx + half))
        crop = ink[max(0, y0 - 2):y1 + 2, a:b].astype(np.float32)
        if crop.size == 0:
            return None
        out.append(cv2.resize(crop, (S, S), interpolation=cv2.INTER_AREA))
    return out


def feats(crop):
    return np.concatenate([hog(crop, orientations=9, pixels_per_cell=(8, 8), cells_per_block=(2, 2)),
                           cv2.resize(crop, (12, 12), interpolation=cv2.INTER_AREA).ravel()])


def collect(proj, n):
    """[(word text, [letter crops], band)] for every word with exactly one box."""
    pg = m_get_page(str(proj), n)
    ink = layout.ink_mask(cv2.imread(str(P.page_dir(proj, n) / "page.png"), cv2.IMREAD_GRAYSCALE))
    out = []
    for L in pg["lines"]:
        for w in L.get("words", []):
            word = letters_of(w["text"])
            if len(w["boxes"]) != 1 or not (2 <= len(word) <= 10):
                continue
            crops = letter_crops(ink, L["boxes"][w["boxes"][0]], word)
            if crops:
                out.append((word, crops, w["band"], (ink, L["boxes"][w["boxes"][0]])))
    return out


def train(samples):
    X, y = [], []
    for word, crops, band, _ in samples:
        if band != "confident":
            continue
        for c, ch in zip(crops, word):
            X.append(feats(c))
            y.append(ch)
    clf = LogisticRegression(max_iter=3000, C=0.5)
    clf.fit(np.array(X), np.array(y))
    return clf


def logp(clf, crop):
    p = clf.predict_log_proba(feats(crop)[None])[0]
    return {c: p[i] for i, c in enumerate(clf.classes_)}


def handwriting_score(clf, crops, a, b):
    """log-odds that the handwriting shows spelling a rather than b (same length)."""
    s = 0.0
    for crop, ca, cb in zip(crops, a, b):
        if ca != cb:
            lp = logp(clf, crop)
            s += lp.get(ca, -20) - lp.get(cb, -20)
    return s


def whole_word_score(clf, ink_box, a, b):
    """For spellings of different length: mean per-letter log P of each spelling over its own crops
    (the box is re-divided for each spelling). Positive = the handwriting looks more like a."""
    ink, box = ink_box
    out = []
    for word in (a, b):
        crops = letter_crops(ink, box, word)
        lp = [logp(clf, c).get(ch, -20) for c, ch in zip(crops, word)]
        out.append(np.mean(lp))
    return (out[0] - out[1]) * 4  # scaled to be comparable with the letter-swap score


def main():
    src = Path(f"{ROOT}/test_projects/FFV3.ntproj")
    tmp = Path(tempfile.mkdtemp()) / "t.ntproj"
    shutil.copytree(src, tmp)
    data = {n: collect(tmp, n) for n in (1, 2, 3)}
    print("words with one box:", {n: len(v) for n, v in data.items()},
          "| letters for training:", sum(len(w) for v in data.values() for w, _, b, _ in v if b == "confident"))

    per_pair = defaultdict(lambda: [0, 0])
    scores = []
    models = {}
    for test in (1, 2, 3):
        clf = train([s for n, v in data.items() if n != test for s in v])
        models[test] = clf
        for word, crops, band, _ in data[test]:
            if band != "confident":
                continue
            for i, ch in enumerate(word):
                for alt in SWAP.get(ch, ""):
                    if i == len(word) - 1 and alt in "כמנפצ" and alt + "" in "כמנפצ":
                        alt = alt.translate(str.maketrans("כמנפצ", "ךםןףץ"))
                    swapped = word[:i] + alt + word[i + 1:]
                    sc = handwriting_score(clf, crops, word, swapped)
                    scores.append(sc)
                    key = "/".join(sorted((ch, alt)))
                    per_pair[key][0] += sc > 0
                    per_pair[key][1] += 1
    scores = np.array(scores)
    print(f"\nsynthetic test (original vs. sound-alike swap, unseen page): prefers the original in "
          f"{(scores > 0).mean():.0%} of {len(scores)} cases")
    for t in (1, 2, 4):
        sel = np.abs(scores) > t
        print(f"   when |score| > {t}: decides {sel.mean():.0%} of cases, right in {(scores[sel] > 0).mean():.0%}")
    print("   per letter pair:", {k: f"{v[0] / v[1]:.0%} of {v[1]}" for k, v in sorted(per_pair.items(), key=lambda kv: -kv[1][1])})

    ins = []
    for test in (1, 2, 3):
        for word, crops, band, inkbox in data[test]:
            if band != "confident" or len(word) < 3:
                continue
            for i in range(1, len(word)):
                for v in "וי":
                    ins.append(whole_word_score(models[test], inkbox, word, word[:i] + v + word[i:]))
    ins = np.array(ins)
    print(f"synthetic test (original vs. an added ו/י): prefers the original in {(ins > 0).mean():.0%} of {len(ins)}; "
          f"when |score| > 2: decides {(np.abs(ins) > 2).mean():.0%}, right in {(ins[np.abs(ins) > 2] > 0).mean():.0%}")

    # real suggestions with known answers: (page, recognised, suggested, suggestion right?)
    known = [(2, "הסטני", "השטני", True), (2, "בתחתיה", "מתחתיה", True), (2, "עומה", "הומה", True),
             (2, "טועה", "תועה", True), (3, "לחוט", "לחות", True), (3, "זהיר", "זעיר", True),
             (1, "עלונים", "אלונים", True), (1, "אבה", "עבה", True), (1, "בורשים", "מורשים", False),
             (3, "מנטרים", "מנטורים", False), (3, "מנקדים", "מנוקדים", False), (1, "העילו", "הועילו", False),
             (3, "נודם", "נוודים", False), (1, "מעוד", "בהוד", False)]
    print("\nreal suggestions:")
    for n, rec, sug, right in known:
        hit = [(c, t) for w, c, _, t in data[n] if w in (letters_of(rec), letters_of(sug))]  # may be corrected already
        if not hit:
            print(f"   {rec} → {sug}: word not found with a single box")
            continue
        if len(rec) == len(sug):
            sc = handwriting_score(models[n], hit[0][0], sug, rec)
        else:
            sc = whole_word_score(models[n], hit[0][1], sug, rec)
        verdict = "agrees" if sc > 1 else ("disagrees" if sc < -1 else "unsure")
        ok = (sc > 0) == right
        print(f"   {rec} → {sug}: handwriting {verdict} ({sc:+.1f}); suggestion is {'right' if right else 'wrong'} → "
              f"{'✓' if ok else '✗'}")
    shutil.rmtree(tmp.parent)


if __name__ == "__main__":
    main()
