"""Spike: transcribe a page recording with the converted ivrit model on mlx-whisper (Metal).

Usage: uv run python asr_mlx.py p_115   -> out/<name>_mlx.json (same format as asr_ref.py)
"""
import json
import sys
import time

import mlx.core as mx
import mlx_whisper

src = open("asr_ref.py").read()
ns = {}
exec(src.split("audio = load_audio")[0].replace("from faster_whisper import WhisperModel", ""), ns)

name = sys.argv[1]
audio = ns["load_audio"](f"../test_files/{name}.m4a")
MODEL = "models/ivrit-turbo-mlx"
t = time.time()
mlx_whisper.transcribe(audio[:16000], path_or_hf_repo=MODEL, language="he", fp16=True)  # warm-up / load
load = time.time() - t
t = time.time()
r = mlx_whisper.transcribe(audio, path_or_hf_repo=MODEL, language="he", word_timestamps=True,
                           condition_on_previous_text=False, fp16=True)
run = time.time() - t
out = [{"start": s["start"], "end": s["end"], "text": s["text"].strip(),
        "words": [{"w": w["word"].strip(), "t0": round(w["start"], 2), "t1": round(w["end"], 2),
                   "p": round(w["probability"], 3)} for w in s.get("words", [])]} for s in r["segments"]]
dur = len(audio) / 16000
json.dump({"audio_s": dur, "load_s": round(load, 1), "run_s": round(run, 1), "segments": out},
          open(f"out/{name}_mlx.json", "w"), ensure_ascii=False, indent=1)
print(f"{name} [mlx]: audio {dur:.0f}s, load {load:.0f}s, transcribe {run:.0f}s ({dur / run:.1f}x real time), "
      f"peak GPU memory {mx.get_peak_memory() / 1e9:.1f} GB")
for s in out[:6]:
    print(f"[{s['start']:6.1f}-{s['end']:6.1f}] {s['text']}")
