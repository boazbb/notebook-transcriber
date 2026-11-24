"""Convert ivrit-ai/whisper-large-v3-turbo (Hugging Face transformers weights) to mlx-whisper format.

Usage: uv run python convert_mlx.py  -> models/ivrit-turbo-mlx/{config.json,weights.safetensors}
"""
import json
import os
import re

import mlx.core as mx
from huggingface_hub import snapshot_download

SRC = snapshot_download("ivrit-ai/whisper-large-v3-turbo", local_files_only=True)
DST = "models/ivrit-turbo-mlx"

cfg = json.load(open(os.path.join(SRC, "config.json")))
dims = {
    "n_mels": cfg["num_mel_bins"], "n_audio_ctx": cfg["max_source_positions"],
    "n_audio_state": cfg["d_model"], "n_audio_head": cfg["encoder_attention_heads"],
    "n_audio_layer": cfg["encoder_layers"], "n_vocab": cfg["vocab_size"],
    "n_text_ctx": cfg["max_target_positions"], "n_text_state": cfg["d_model"],
    "n_text_head": cfg["decoder_attention_heads"], "n_text_layer": cfg["decoder_layers"],
}

RULES = [
    (r"^model\.encoder\.layers\.", "encoder.blocks."),
    (r"^model\.decoder\.layers\.", "decoder.blocks."),
    (r"^model\.encoder\.layer_norm\.", "encoder.ln_post."),
    (r"^model\.decoder\.layer_norm\.", "decoder.ln."),
    (r"^model\.decoder\.embed_tokens\.weight$", "decoder.token_embedding.weight"),
    (r"^model\.decoder\.embed_positions\.weight$", "decoder.positional_embedding"),
    (r"^model\.encoder\.", "encoder."),
    (r"\.self_attn_layer_norm\.", ".attn_ln."),
    (r"\.encoder_attn_layer_norm\.", ".cross_attn_ln."),
    (r"\.final_layer_norm\.", ".mlp_ln."),
    (r"\.self_attn\.", ".attn."),
    (r"\.encoder_attn\.", ".cross_attn."),
    (r"\.q_proj\.", ".query."), (r"\.k_proj\.", ".key."), (r"\.v_proj\.", ".value."),
    (r"\.out_proj\.", ".out."),
    (r"\.fc1\.", ".mlp1."), (r"\.fc2\.", ".mlp2."),
]

src = mx.load(os.path.join(SRC, "model.safetensors"))
out = {}
for k, v in src.items():
    if k == "model.encoder.embed_positions.weight" or k.startswith("proj_out"):
        continue  # encoder positions are computed (sinusoids); output projection is tied
    n = k
    for pat, rep in RULES:
        n = re.sub(pat, rep, n)
    if re.match(r"encoder\.conv[12]\.weight", n):
        v = v.transpose(0, 2, 1)  # torch (out, in, k) -> mlx (out, k, in)
    out[n] = v.astype(mx.float16)

os.makedirs(DST, exist_ok=True)
mx.save_safetensors(os.path.join(DST, "weights.safetensors"), out)
json.dump(dims, open(os.path.join(DST, "config.json"), "w"), indent=1)
print(f"{len(out)} tensors -> {DST}", dims)
