"""Handwriting check for sound-alike suggestions (spec stage 4b).

A small letter classifier trained on the notebook's own words: each confidently read (or user-corrected)
word's letters are located by position (its box divided by letter widths, right to left), cropped and
described (HOG + a coarse pixel grid); logistic regression over the letters. For a suggestion that swaps
letters (טועה → תועה), the check compares how much the handwritten letters look like each spelling.

Spike results (3 pages, trained on 2, tested on the 3rd): picks the original over a sound-alike swap in
84% of 2,003 cases; when |score| > 2 it decides 69% of cases and is right in 93%. Real suggestions: 7 of
the 8 right ones confirmed. It can't judge added or removed letters (whole-word comparison ≈ chance).
"""
from __future__ import annotations

import threading
from pathlib import Path

import cv2
import numpy as np

from .hebrew import NIQQUD

LETTERS = "אבגדהוזחטיכךלמםנןסעפףצץקרשת"
NARROW = {"י": 0.45, "ו": 0.5, "ן": 0.5, "ז": 0.6, "ג": 0.7, "נ": 0.7}
S = 32
MIN_LETTERS = 800   # train only with roughly two pages of confidently read words
DECIDE = 2.0        # |score| above this: the handwriting decides

_lock = threading.Lock()


def letters_of(text: str) -> str:
    return "".join(c for c in NIQQUD.sub("", text) if c in LETTERS)


def letter_crops(ink: np.ndarray, box: list[int], word: str) -> list[np.ndarray] | None:
    x0, y0, x1, y1 = box
    H = y1 - y0
    if H < 8 or x1 - x0 < 4 or not word:
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


def _feats(crop: np.ndarray) -> np.ndarray:
    from skimage.feature import hog
    return np.concatenate([hog(crop, orientations=9, pixels_per_cell=(8, 8), cells_per_block=(2, 2)),
                           cv2.resize(crop, (12, 12), interpolation=cv2.INTER_AREA).ravel()])


class Model:
    def __init__(self, clf):
        self.clf = clf
        self.index = {c: i for i, c in enumerate(clf.classes_)}

    def score(self, crops: list[np.ndarray], a: str, b: str) -> float:
        """Log-odds that the handwriting shows spelling a rather than b (same length)."""
        s = 0.0
        for crop, ca, cb in zip(crops, a, b):
            if ca != cb:
                lp = self.clf.predict_log_proba(_feats(crop)[None])[0]
                s += lp[self.index[ca]] if ca in self.index else -20.0
                s -= lp[self.index[cb]] if cb in self.index else -20.0
        return float(s)


def train(samples: list[tuple[str, list[np.ndarray]]]) -> Model | None:
    """samples: (word letters, letter crops) of trusted words. None if there is too little data."""
    X, y = [], []
    for word, crops in samples:
        for c, ch in zip(crops, word):
            X.append(_feats(c))
            y.append(ch)
    if len(y) < MIN_LETTERS or len(set(y)) < 10:
        return None
    from sklearn.linear_model import LogisticRegression
    with _lock:
        clf = LogisticRegression(max_iter=3000, C=0.5)
        clf.fit(np.array(X), np.array(y))
    return Model(clf)


def page_ink(png: Path, line_height: float | None = None) -> np.ndarray:
    """The page's ink mask, with the notebook's ruled lines removed (line height from page.json if not given)."""
    import json
    from .layout import ink_mask, remove_ruling
    gray = cv2.imread(str(png), cv2.IMREAD_GRAYSCALE)
    ink = ink_mask(gray)
    if line_height is None:
        try:
            line_height = json.loads((png.parent / "page.json").read_text(encoding="utf-8")).get("line_height")
        except (OSError, ValueError):
            line_height = None
    if line_height:
        ink, _ = remove_ruling(ink, line_height, gray)
    return ink
