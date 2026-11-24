"""Stage 4: speech recognition with the ivrit.ai Whisper turbo model (faster-whisper, Metal with CPU fallback)."""
from __future__ import annotations

import threading
from pathlib import Path

import av
import numpy as np

MODEL_ID = "ivrit-ai/whisper-large-v3-turbo-ct2"
SR = 16000

_model = None
_device = None
_lock = threading.Lock()


def load_audio(path: Path) -> np.ndarray:
    """Decode any audio file to 16 kHz mono float32 (PyAV; faster-whisper's own decoder breaks on current PyAV)."""
    c = av.open(str(path))
    rs = av.AudioResampler(format="flt", layout="mono", rate=SR)
    chunks = [f.to_ndarray().reshape(-1) for frame in c.decode(audio=0) for f in rs.resample(frame)]
    chunks += [f.to_ndarray().reshape(-1) for f in rs.resample(None)]
    c.close()
    return np.concatenate(chunks).astype(np.float32) if chunks else np.zeros(0, np.float32)


def write_wav(path: Path, audio: np.ndarray) -> None:
    import wave
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def get_model(progress=None):
    global _model, _device
    if _model is None:
        from faster_whisper import WhisperModel
        if progress:
            progress("Loading the speech model…")
        try:
            _model = WhisperModel(MODEL_ID, device="mps", compute_type="float16")
            _device = "mps"
        except Exception:
            _model = WhisperModel(MODEL_ID, device="cpu", compute_type="int8")
            _device = "cpu"
    return _model


def keep_segments(n_samples: int, discarded: list[list[float]]) -> list[tuple[int, int]]:
    """Sample ranges to keep after removing the discarded stretches (Backspace)."""
    cuts = sorted((max(0, int(a * SR)), min(n_samples, int(b * SR))) for a, b in discarded if b > a)
    keep, pos = [], 0
    for a, b in cuts:
        if a > pos:
            keep.append((pos, a))
        pos = max(pos, b)
    if pos < n_samples:
        keep.append((pos, n_samples))
    return keep


def transcribe(audio_path: Path, discarded: list[list[float]] | None = None, hotwords: str | None = None,
               progress=None, context: str | None = None) -> dict:
    """Whole-take transcription. Returns {'device', 'duration', 'words': [{w, t0, t1, p}]} with
    times in the original recording (discarded stretches are cut out before recognition)."""
    audio = load_audio(audio_path)
    keep = keep_segments(len(audio), discarded or [])
    pieces = [audio[a:b] for a, b in keep]
    clean = np.concatenate(pieces) if pieces else np.zeros(0, np.float32)
    # map time in the cleaned audio back to the original recording
    offsets = []
    acc = 0
    for a, b in keep:
        offsets.append((acc / SR, (acc + b - a) / SR, a / SR))
        acc += b - a

    def to_orig(t):
        for c0, c1, o in offsets:
            if t <= c1:
                return round(o + (t - c0), 2)
        return round(t, 2)

    with _lock:
        model = get_model(progress)
        if progress:
            progress(f"Recognising speech ({'Metal' if _device == 'mps' else 'CPU'})…")
        segs, info = model.transcribe(clean, language="he", word_timestamps=True,
                                      condition_on_previous_text=False, beam_size=5,
                                      hotwords=hotwords,  # glossary hints, in every 30 s window
                                      initial_prompt=context)  # e.g. the lines before a re-recorded line
        words = []
        for s in segs:
            for w in s.words or []:
                words.append({"w": w.word.strip(), "t0": to_orig(w.start), "t1": to_orig(w.end),
                              "p": round(w.probability, 3)})
            if progress and info.duration:
                progress(f"Recognising speech… {min(100, int(100 * s.end / info.duration))}%")
    # attach stray punctuation tokens to the previous word
    out = []
    from .hebrew import norm
    for w in words:
        if not w["w"]:
            continue
        if not norm(w["w"]) and out:
            out[-1]["w"] += w["w"]
            continue
        out.append(w)
    return {"device": _device, "model": MODEL_ID, "duration": round(len(audio) / SR, 2), "words": out}
