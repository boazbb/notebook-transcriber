"""Download the spike's models, most important first. Resumable: re-run to continue."""
import sys, time
from huggingface_hub import snapshot_download

REPOS = [
    ("ivrit-ai/whisper-large-v3-turbo-ct2", None),  # reference: faster-whisper, CPU (1.6 GB)
    ("ivrit-ai/whisper-large-v3-turbo", None),      # full weights, to convert to MLX (3.2 GB)
]
for repo, patterns in REPOS:
    for attempt in range(1, 11):
        try:
            t = time.time()
            p = snapshot_download(repo, allow_patterns=patterns)
            print(f"OK {repo} -> {p} ({time.time()-t:.0f}s)", flush=True)
            break
        except Exception as e:
            print(f"retry {attempt} {repo}: {e}", flush=True)
            time.sleep(10 * attempt)
    else:
        print(f"FAILED {repo}", flush=True); sys.exit(1)
print("ALL DONE", flush=True)
