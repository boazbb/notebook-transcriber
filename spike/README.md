# Spike — checking the spec's assumptions

Test material: `../test_files/` — `FFV3_scan.pdf` (15 pages; PDF pages 1–3 = notebook pages 114–116),
recordings `p_114.m4a` (starts mid-page), `p_115.m4a` (full page), `p_116.m4a` (stops after the first
line of the last paragraph), ground truth `p_115_truth.txt` / `p_115_truth_lines.txt` (paragraph 1, 7 lines).

## Results (Nov 23, 2026)

### Input
- The PDF holds phone-scanner images (one image per page, 180–230 dpi, varying sizes), not flatbed scans.
  Rendering "at 300 dpi" only upsamples; the app should use the embedded image at its native resolution.
- Paper: faint ruled lines (removal is easy, most pages have almost no ruling ink left after binarizing).
- Pages curve near the binding (strongly at the top of some pages), and text from the facing page shows
  at the outer edge.
- Writing is in clear paragraphs separated by a blank line; crossed-out words, margin doodles and some
  drawings occur.

### Line detection
- Projection profiles (`segment.py`): fine on flat pages, fragile on curved tops (lines cross or merge).
  Not good enough as the primary method on these scans.
- Kraken 7.2 baseline segmenter (`kraken_env/`, ~5 s/page on CPU): tight, curve-following lines; finds
  one-word lines; its only weakness is splitting a line into pieces at wide gaps.
- Kraken + joining pieces along the baseline + dropping facing-page bleed at the edge
  (`kraken_lines.py`): **28/28 lines on all three pages**, correct order.
- → Use Kraken as the primary line detector, projections not needed.

### Word boxes (`words.py`)
- Gap threshold learned per page (Otsu on gap widths): ~0.45 × line height.
- Commas glue words together unless small baseline marks are excluded from the gap computation; with that,
  page 115 paragraph 1 gives 58 boxes for 63 words. Remaining errors are close-written pairs (handled by the
  alignment's 2-words↔1-box move), plus small issues: margin first letters cut by the line outline, a
  word-final ו/י occasionally taken for a mark.

### Speech recognition (`asr_ref.py`, `score.py`)
- **The ivrit.ai turbo model outputs sensible punctuation** (commas and periods at clause/sentence ends).
- Page 115 paragraph 1: **WER 11%** (7 of 63): 2 spelling variants (השרפה/השריפה, החץ/החיץ),
  4 sound-alike words (הסטני/השטני, טועה/תועה, עומה/הומה, ריקים/רכים), 1 dropped word (עד).
- Punctuation vs the page: **all 11 written marks found at the right place**; 3 of them got a period where
  the page has a comma; 2 extra commas. Position is reliable, mark type isn't → M4 (page marks) decides type.
- Speed: faster-whisper int8 CPU 2.9× real time; **faster-whisper on Metal (`device="mps"`, CTranslate2's
  experimental backend) 4.5–5.6× real time**. CPU vs Metal differ on 6 of 254 words (minor).
- Word end times land within ~0.05 s of real silences in the audio.
- Note: PyAV ≥ current drops an argument faster-whisper passes; decode audio ourselves (see `load_audio`).

### Reading style and line assignment (`align_page.py`)
- You paused at **sentence ends, not line ends** — natural reading. Pauses can't give line breaks.
- **Page-level alignment of spoken words to word boxes (widths only, no Space presses):**
  - page 115 paragraph 1: 62/62 words on the right line, also when aligning the whole page at once;
    rest of the page by eye: 2 line-final words pushed to the next line.
  - page 114 (reading starts mid-page): found the start at the right line (the ① paragraph).
  - page 116 (reading stops after one line of the last paragraph): every line matches the page by eye.
- → Space presses look unnecessary for line assignment; they could become optional hints.

### MLX vs faster-whisper on Metal (`convert_mlx.py`, `asr_mlx.py`)
- No MLX build is published; `convert_mlx.py` converts the HF weights (1.5 GB, float16, one-off, seconds).
- mlx-whisper: 5.2–6.3× real time, peak 2.1 GB; WER on paragraph 1 identical (11.1%); 1–6 word
  differences per page vs faster-whisper; word end times within 0.06 s of each other.
- Practically equivalent. mlx-whisper is greedy-only (no beam search) and needs the conversion step;
  faster-whisper uses the official ivrit build, has beam search and built-in VAD, and the same code runs
  on CPU, but its Metal backend is labelled experimental.
- → Recommendation: faster-whisper with `device="mps"`, falling back to `device="cpu"` (still 3× real
  time) if the Metal backend misbehaves. Keep `convert_mlx.py` as a plan B.

## Not done yet
- DictaLM 3.0 1.7B: exists (`dicta-il/DictaLM-3.0-1.7B-Instruct`, plus GGUF only for the Thinking variant);
  not downloaded or run (3.4 GB, optional feature).
- `dicta-onnx` (niqqud): exists on PyPI (1.0.9), not tried yet.
- Ground truth for more lines would allow a real line-accuracy number beyond one paragraph.
