"""Fix sound-alike recognition errors with a Hebrew language model (DictaBERT).

Speech recognition sometimes picks a word that sounds right but is spelled wrong for the sentence
(טועה/תועה, עומה/הומה, ריקים/רכים). For each word we generate only spellings that sound the same
(a few letter swaps, with or without a vowel letter), score every spelling in its sentence with
DictaBERT (pseudo-log-likelihood: each sub-token masked in turn), and replace the word only when a
variant is clearly more likely. Letters can't change in any other way, so the text can't be rewritten.
"""
from __future__ import annotations

import threading

from .hebrew import norm

MODEL_ID = "dicta-il/dictabert"
MARGIN = 4.0          # log-likelihood gain needed to replace a word (nats)
MAX_EDITS = 2
CONTEXT = 12          # words of context on each side

# letters that sound the same (or nearly) in modern Hebrew speech
SWAPS = {
    "ס": "ש", "ש": "ס",
    "ת": "ט", "ט": "ת",
    "כ": "חק", "ח": "כ", "ק": "כ",
    "ב": "ו", "ו": "ב",
    "א": "עה", "ע": "אה", "ה": "אע",
}
FIRST_ONLY = {"ב": "מ", "מ": "ב"}   # prefix letters that are easily misheard
VOWEL_LETTERS = "יו"
TO_FINAL = str.maketrans("כמנפצ", "ךםןףץ")
FROM_FINAL = str.maketrans("ךםןףץ", "כמנפצ")

_model = None
_tok = None
_vocab: dict[str, int] = {}
_device = "cpu"
_lock = threading.Lock()


def _finalise(w: str) -> str:
    w = w.translate(FROM_FINAL)
    return w[:-1] + w[-1].translate(TO_FINAL) if w else w


def variants(word: str) -> set[str]:
    """Sound-alike spellings of a bare Hebrew word (no punctuation), up to MAX_EDITS changes.

    Each original letter changes at most once (no chains like מ→ב→ו). Only letter swaps: vowel letters
    (ו, י) are never removed (that usually makes a different real word: במרומה → במרמה) nor added.
    """
    base = word.translate(FROM_FINAL)
    out: set[str] = set()
    frontier = {(base, frozenset())}  # (spelling, positions already changed or inserted)
    for _ in range(MAX_EDITS):
        nxt = set()
        for w, touched in frontier:
            for i, ch in enumerate(w):
                if i in touched:
                    continue
                for r in SWAPS.get(ch, "") + (FIRST_ONLY.get(ch, "") if i == 0 else ""):
                    nxt.add((w[:i] + r + w[i + 1:], touched | {i}))
            # No added vowel letters: in the spike, suggestions that added a ו/י were mostly wrong
            # (מנטרים → מנטורים), and the handwriting check can't judge them; letter swaps were right.
        nxt = {(w, t) for w, t in nxt if w != base}
        out |= {w for w, _ in nxt}
        frontier = nxt
    return {_finalise(v) for v in out if len(v) >= 2}


def _load(progress=None):
    global _model, _tok, _device, _vocab
    if _model is None:
        import torch
        from transformers import AutoModelForMaskedLM, AutoTokenizer
        if progress:
            progress("Loading the Hebrew language model…")
        _tok = AutoTokenizer.from_pretrained(MODEL_ID)
        _vocab = {t: i for t, i in _tok.get_vocab().items() if t and "\u05d0" <= t[0] <= "\u05ea"}
        _model = AutoModelForMaskedLM.from_pretrained(MODEL_ID)
        _device = "mps" if torch.backends.mps.is_available() else "cpu"
        _model.to(_device).eval()
    return _model, _tok


def _pll_batch(items: list[tuple[list[str], int, str]]) -> list[float]:
    """Pseudo-log-likelihood of `word` placed at position i in a word list, for each item."""
    import torch
    model, tok = _model, _tok
    rows, owners, targets = [], [], []
    for k, (ctx, i, word) in enumerate(items):
        left = " ".join(ctx[:i])
        right = " ".join(ctx[i + 1:])
        l_ids = tok(left, add_special_tokens=False)["input_ids"] if left else []
        w_ids = tok(word, add_special_tokens=False)["input_ids"]
        r_ids = tok(right, add_special_tokens=False)["input_ids"] if right else []
        ids = [tok.cls_token_id] + l_ids + w_ids + r_ids + [tok.sep_token_id]
        start = 1 + len(l_ids)
        for t in range(len(w_ids)):
            row = list(ids)
            row[start + t] = tok.mask_token_id
            rows.append((row, start + t))
            owners.append(k)
            targets.append(w_ids[t])
    scores = [0.0] * len(items)
    B = 128
    with torch.no_grad():
        for b in range(0, len(rows), B):
            chunk = rows[b:b + B]
            L = max(len(r) for r, _ in chunk)
            inp = torch.full((len(chunk), L), tok.pad_token_id)
            att = torch.zeros((len(chunk), L), dtype=torch.long)
            for r, (row, _) in enumerate(chunk):
                inp[r, :len(row)] = torch.tensor(row)
                att[r, :len(row)] = 1
            logits = model(input_ids=inp.to(_device), attention_mask=att.to(_device)).logits
            logp = torch.log_softmax(logits.float(), dim=-1)
            for r, (_, pos) in enumerate(chunk):
                scores[owners[b + r]] += float(logp[r, pos, targets[b + r]])
    return scores


SHORTLIST = 3


def _shortlist(plain: list[str], jobs: list[tuple[int, list[str]]]) -> list[tuple[int, list[str]]]:
    """Stage 1: mask the whole word with one [MASK] and keep the variants the model likes best there."""
    import torch
    tok = _tok
    out = []
    B = 64
    for b in range(0, len(jobs), B):
        chunk = jobs[b:b + B]
        rows, mpos = [], []
        for i, _ in chunk:
            lo, hi = max(0, i - CONTEXT), min(len(plain), i + CONTEXT + 1)
            left = tok(" ".join(plain[lo:i]), add_special_tokens=False)["input_ids"] if i > lo else []
            right = tok(" ".join(plain[i + 1:hi]), add_special_tokens=False)["input_ids"] if hi > i + 1 else []
            rows.append([tok.cls_token_id] + left + [tok.mask_token_id] + right + [tok.sep_token_id])
            mpos.append(1 + len(left))
        L = max(len(r) for r in rows)
        inp = torch.full((len(rows), L), tok.pad_token_id)
        att = torch.zeros((len(rows), L), dtype=torch.long)
        for r, row in enumerate(rows):
            inp[r, :len(row)] = torch.tensor(row)
            att[r, :len(row)] = 1
        with torch.no_grad():
            logits = _model(input_ids=inp.to(_device), attention_mask=att.to(_device)).logits
        logp = torch.log_softmax(logits.float(), dim=-1)
        for r, (i, cands) in enumerate(chunk):
            ids = torch.tensor([_vocab[c] for c in cands], device=logp.device)
            s = logp[r, mpos[r], ids].tolist()
            ranked = sorted(zip(s, cands), reverse=True)[:SHORTLIST]
            out.append((i, [c for _, c in ranked]))
    return out


def _split_punct(token: str) -> tuple[str, str, str]:
    i, j = 0, len(token)
    while i < j and not ("א" <= token[i] <= "ת"):
        i += 1
    while j > i and not ("א" <= token[j - 1] <= "ת"):
        j -= 1
    return token[:i], token[i:j], token[j:]


def correct(words: list[str], progress=None, only: set[int] | None = None) -> list[dict]:
    """For each word: {'text': corrected word with punctuation, 'from': original or None, 'gain': nats}.
    `only`: check just these word positions (the rest is context)."""
    with _lock:
        _load(progress)
        tok = _tok
        bare = [_split_punct(w) for w in words]
        plain = [b[1] if b[1] else w for w, b in zip(words, bare)]
        jobs: list[tuple[int, list[str]]] = []
        for i, (pre, core, post) in enumerate(bare):
            if only is not None and i not in only:
                continue
            if len(core) < 2 or not all("א" <= c <= "ת" for c in core):
                continue
            # only variants that are whole words in the model's vocabulary (real, reasonably common words)
            cands = [v for v in variants(core) if v in _vocab]
            if cands:
                jobs.append((i, cands))
        results = [{"text": w, "from": None, "gain": 0.0} for w in words]
        shortlist = _shortlist(plain, jobs)
        for done, (i, cands) in enumerate(shortlist):
            if not cands:
                continue
            lo, hi = max(0, i - CONTEXT), min(len(plain), i + CONTEXT + 1)
            ctx = plain[lo:hi]
            pos = i - lo
            items = [(ctx, pos, bare[i][1])] + [(ctx, pos, c) for c in cands]
            sc = _pll_batch(items)
            best = max(range(1, len(sc)), key=lambda k: sc[k])
            gain = sc[best] - sc[0]
            if gain > MARGIN:
                pre, core, post = bare[i]
                results[i] = {"text": pre + cands[best - 1] + post, "from": words[i], "gain": round(gain, 2)}
            if progress and done % 20 == 0:
                progress(f"Checking sound-alike words… {int(100 * done / max(1, len(jobs)))}%")
        return results


def same_word(a: str, b: str) -> bool:
    return norm(a) == norm(b)
