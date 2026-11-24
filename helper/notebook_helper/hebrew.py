"""Hebrew-aware text helpers: comparison ignores niqqud, punctuation and final-letter forms."""
import re

FINALS = str.maketrans("ךםןףץ", "כמנפצ")
PUNCT = ".,:;?!\"'״׳-–—()[]…"
NIQQUD = re.compile(r"[֑-ׇ]")


def norm(word: str) -> str:
    """Comparable form of a word: no niqqud, no surrounding punctuation, final letters normalised."""
    return NIQQUD.sub("", word).strip(PUNCT).translate(FINALS)


def spoken_tokens(text: str) -> list[str]:
    """Split recognised text into words, attaching stray punctuation tokens to the previous word."""
    out: list[str] = []
    for t in text.split():
        if norm(t):
            out.append(t)
        elif out:
            out[-1] += t
    return out
