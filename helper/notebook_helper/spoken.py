"""Spoken punctuation commands (optional, off by default; a project setting).

While reading, the user says "מרכאות" where a quote mark goes and "סוגריים" where a bracket goes. The
first one opens, the next closes (per page). The command words are taken out of the recognised words
before matching to the page (they aren't written there) and turned into marks on the neighbouring words:
an opening mark goes before the next word, a closing mark after the previous word (a quote after its comma
or period, as in ״נכון,״; a bracket before it, as in אתמול).). Only the exact command words count: near
matches would catch real words of the story (מרכבות, סוגרת). Punctuation Whisper put on the command word itself moves to the previous word.

Spike: on a synthesised Hebrew reading (macOS voice "Carmit"), Whisper wrote both command words exactly.
"""
from __future__ import annotations

from .hebrew import norm

# Exact words only: near-matches catch real words of the story (מרכבות "chariots", סוגרת "closes").
COMMANDS = {"quote": ["מרכאות", "מירכאות"], "bracket": ["סוגריים"]}
MARKS = {"quote": ('"', '"'), "bracket": ("(", ")")}
HINTS = "מרכאות סוגריים"


def _edit1(a: str, b: str) -> bool:
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    if len(a) > len(b):
        a, b = b, a
    return any(b[:i] + b[i + 1:] == a for i in range(len(b)))


def command_of(token: str) -> str | None:
    w = norm(token)
    for kind, forms in COMMANDS.items():
        if any(w == norm(f) for f in forms):
            return kind
    return None


def extract(words: list[dict]) -> tuple[list[dict], list[tuple[str, int | None, int | None, str]]]:
    """Remove command words. Returns the remaining words and the commands as
    (kind, index of the previous remaining word, index of the next one, punctuation on the command word)."""
    out, cmds = [], []
    for w in words:
        kind = command_of(w["w"])
        if kind:
            tail = "".join(ch for ch in w["w"] if ch in ".,:;?!")
            cmds.append((kind, len(out) - 1 if out else None, len(out), tail))
        else:
            out.append(w)
    return out, cmds


def apply(seq: list[dict], cmds) -> None:
    """Turn the commands into marks on the word dicts (in reading order). Opening and closing alternate."""
    state = {"quote": False, "bracket": False}
    for kind, prev, nxt, tail in cmds:
        open_, close = MARKS[kind]
        if not state[kind] and nxt is not None and nxt < len(seq):
            w = seq[nxt]
            w["text"] = open_ + w["text"]
            w.setdefault("spoken", []).append("lead")
            state[kind] = True
            if tail and prev is not None:
                seq[prev]["text"] = _add_tail(seq[prev]["text"], tail)
        elif prev is not None:
            w = seq[prev]
            # Hebrew: a comma or period goes inside a quote (״נכון,״) but outside a bracket (אתמול).)
            w["text"] = (_add_tail(w["text"], tail) + close) if kind == "quote" else (w["text"] + close + tail)
            w.setdefault("spoken", []).append("tail")
            state[kind] = False


def _add_tail(text: str, tail: str) -> str:
    return text if not tail or text.endswith(tail) else text + tail
