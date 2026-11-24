"""Spike, end to end: one notebook page + its recording -> text with the notebook's line breaks.

Usage (from the spike folder):
  uv run python run_page.py ../test_files/FFV3_scan.pdf 2 ../test_files/p_115.m4a
  (2 = page number inside the PDF, counting from 1)

Writes out/pageN.txt (the text) and out/pageN_lines2.png (detected lines, for checking).
Steps: render page -> Kraken lines -> join pieces -> word boxes -> speech recognition (Metal)
-> match spoken words to word boxes -> text per line.
"""
import json
import os
import subprocess
import sys
import time

import av
import cv2
import numpy as np
import pypdfium2 as pdfium

import align_page as A
import kraken_lines
import words as W
from score import norm

LETTER_WIDTH = 43.0  # px at 300 dpi, measured on page 115; good enough for this notebook


def load_audio(path, sr=16000):
    c = av.open(path)
    rs = av.AudioResampler(format="flt", layout="mono", rate=sr)
    chunks = [f.to_ndarray().reshape(-1) for frame in c.decode(audio=0) for f in rs.resample(frame)]
    chunks += [f.to_ndarray().reshape(-1) for f in rs.resample(None)]
    return np.concatenate(chunks).astype(np.float32)


def step(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    pdf_path, n, audio_path = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    os.makedirs("out", exist_ok=True)

    step(f"rendering PDF page {n}")
    pdf = pdfium.PdfDocument(pdf_path)
    pdf[n - 1].render(scale=300 / 72).to_pil().save(f"out/page{n}.png")

    step("finding lines (Kraken)")
    subprocess.run(["kraken_env/.venv/bin/kraken", "-i", f"out/page{n}.png", f"out/page{n}_kraken.json",
                    "--native", "segment", "-bl", "-d", "horizontal-rl"], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    kraken_lines.process(n)

    step("finding word boxes")
    d = json.load(open(f"out/page{n}_lines2.json"))
    img = cv2.imread(f"out/page{n}.png")
    ink = W.ink_mask(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    nlines = len([L for L in d["lines"] if L["kind"] == "text"])
    boxes = A.boxes_for(d, ink, 1, nlines)

    step("recognising speech (ivrit.ai turbo, Metal)")
    from faster_whisper import WhisperModel
    try:
        model = WhisperModel("ivrit-ai/whisper-large-v3-turbo-ct2", device="mps", compute_type="float16")
    except Exception as e:  # experimental backend: fall back to CPU
        print("   Metal unavailable, using CPU:", e)
        model = WhisperModel("ivrit-ai/whisper-large-v3-turbo-ct2", device="cpu", compute_type="int8")
    segs, info = model.transcribe(load_audio(audio_path), language="he",
                                  condition_on_previous_text=False, beam_size=5)
    text = " ".join(s.text.strip() for s in segs)

    # spoken words with their punctuation attached; letters (normalised) drive the matching
    raw = []
    for t in text.split():
        if norm(t):
            raw.append(t)
        elif raw:
            raw[-1] += t
    letters = [norm(t) for t in raw]

    step(f"matching {len(raw)} spoken words to {len(boxes)} word boxes on {nlines} lines")
    line_of, _ = A.run_dp(letters, boxes, LETTER_WIDTH, free_ends=True)
    out_lines = []
    for li in range(1, nlines + 1):
        ws = [raw[k] for k in range(len(raw)) if line_of[k] == li]
        if ws:
            out_lines.append(" ".join(ws))
    unplaced = [raw[k] for k in range(len(raw)) if line_of[k] is None]

    with open(f"out/page{n}.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines) + "\n")
    step(f"done -> out/page{n}.txt ({len(out_lines)} lines)"
         + (f"; {len(unplaced)} words not placed: {' '.join(unplaced)}" if unplaced else ""))


if __name__ == "__main__":
    main()
