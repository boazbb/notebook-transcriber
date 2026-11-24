"""Spike: transcribe a page recording with the ivrit.ai model via faster-whisper (CPU reference).

Usage: uv run python asr_ref.py p_115 [--vad]
Writes out/<name>_ref.json with segments and word timestamps/probabilities.
"""
import json
import sys
import time

import av
import numpy as np

from faster_whisper import WhisperModel

name = sys.argv[1]
vad = "--vad" in sys.argv
device = "mps" if "--mps" in sys.argv else "cpu"


def load_audio(path, sr=16000):
    """Decode any audio file to 16 kHz mono float32 (PyAV)."""
    c = av.open(path)
    rs = av.AudioResampler(format="flt", layout="mono", rate=sr)
    chunks = []
    for frame in c.decode(audio=0):
        for f in rs.resample(frame):
            chunks.append(f.to_ndarray().reshape(-1))
    for f in rs.resample(None):
        chunks.append(f.to_ndarray().reshape(-1))
    return np.concatenate(chunks).astype(np.float32)


audio = load_audio(f"../test_files/{name}.m4a")
t = time.time()
model = WhisperModel("ivrit-ai/whisper-large-v3-turbo-ct2", device=device, compute_type="int8" if device == "cpu" else "float16")
load = time.time() - t
t = time.time()
segs, info = model.transcribe(audio, language="he", word_timestamps=True,
                              vad_filter=vad, condition_on_previous_text=False, beam_size=5)
out = []
for s in segs:
    out.append({"start": s.start, "end": s.end, "text": s.text.strip(),
                "words": [{"w": w.word.strip(), "t0": round(w.start, 2), "t1": round(w.end, 2),
                           "p": round(w.probability, 3)} for w in s.words]})
run = time.time() - t
json.dump({"audio_s": info.duration, "load_s": round(load, 1), "run_s": round(run, 1), "segments": out},
          open(f"out/{name}_ref_{device}{'_vad' if vad else ''}.json", "w"), ensure_ascii=False, indent=1)
print(f"{name} [{device}]: audio {info.duration:.0f}s, load {load:.0f}s, transcribe {run:.0f}s "
      f"({info.duration / run:.1f}x real time)")
for s in out:
    print(f"[{s['start']:6.1f}-{s['end']:6.1f}] {s['text']}")
