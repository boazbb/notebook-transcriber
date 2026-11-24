#!/bin/sh
# Set up Notebook Transcriber on a Mac (Apple Silicon): Python environments, the app's dependencies, and
# the models (about 3 GB, downloaded once; afterwards everything runs offline).
# Needs: uv (https://docs.astral.sh/uv/), Node.js 22+ (npm), curl. Run from anywhere: tools/setup.sh
set -e
cd "$(dirname "$0")/.."

echo "== Python helper"
(cd helper && uv sync)
echo "== Kraken (line detection, its own environment: PyTorch, older numpy)"
(cd helper/kraken_env && uv sync)

echo "== Models"
helper/.venv/bin/python - <<'PY'
from huggingface_hub import snapshot_download
# speech recognition: ivrit.ai's Hebrew Whisper large-v3-turbo (CTranslate2 build, for faster-whisper)
snapshot_download("ivrit-ai/whisper-large-v3-turbo-ct2")
# sound-alike check: DictaBERT (Hebrew masked language model)
snapshot_download("dicta-il/dictabert", allow_patterns=["*.json", "*.txt", "model.safetensors"])
# tokenizer of the niqqud model (dicta-onnx loads it from the Hub)
snapshot_download("dicta-il/dictabert-large-char-menaked", allow_patterns=["*.json", "*.txt"])
print("models downloaded")
PY
mkdir -p "$HOME/.cache/notebook-transcriber"
NIQQUD="$HOME/.cache/notebook-transcriber/dicta-1.0.int8.onnx"
if [ ! -f "$NIQQUD" ]; then
  echo "== Niqqud model (dicta-onnx, int8)"
  curl -L --fail -o "$NIQQUD.part" \
    "https://github.com/thewh1teagle/dicta-onnx/releases/download/model-files-v1.0/dicta-1.0.int8.onnx"
  mv "$NIQQUD.part" "$NIQQUD"
fi

echo "== App"
(cd app && npm install)

echo "== Git guard (refuses commits with scans, recordings or transcripts)"
if [ -d .git ]; then
  cp tools/git-hooks/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
fi

echo
echo "Done. Run the app for development:  cd app && npm start"
echo "Or build it:  release/update_helper.sh && cd app && npm run dist   (see docs/USAGE.md)"
