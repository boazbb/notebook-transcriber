# Notebook Transcriber — Design Spec

Nov 22, 2026 · @Boaz

## Overview

A local Mac app that turns scanned Hebrew notebook pages into clean plain text: the user reads each page aloud, line by line, and the app ties every spoken word to its spot on the page so corrections are fast.

**Core idea:** voice decides *what* the words are; the page image decides *where* they are. Recognizing personal Hebrew handwriting offline is not reliable today, so handwriting recognition is a later, optional second opinion, not the foundation.

**Constraints**

- Runs on an M4 MacBook Air with 16 GB RAM, fully offline. No cloud APIs.
- Input: a PDF of scanned notebook pages (may contain drawings).
- Output: UTF-8 plain text, right-to-left Hebrew, with good punctuation.
- Niqqud only on character names and special words, from a user-maintained glossary. Ordinary text stays unpointed.
- Built later by Claude Code; this spec is its brief.

## User workflow

A page goes through five steps: import, check lines, read aloud, review the uncertain words, export.

1. **Import.** Open a PDF; the app makes a project folder, extracts each page's scan at its native resolution and detects lines, paragraphs and drawings in the background.
2. **Check the lines.** The page shows numbered line boxes in reading order. The user can fix them in a few seconds: drag edges, split or merge lines, reorder (e.g. a margin note), or mark an area as a drawing.
3. **Reading.** The user clicks the line where they start (any line, e.g. where a story begins mid-page) and reads verbatim, naturally, pausing wherever feels natural (typically at sentence ends). The page is recorded as one continuous take and may stop anywhere; unread lines are simply "not read", not errors. Lines are assigned afterwards by matching the words to the page (stage 5), so no key presses are needed. Optional: Space marks a line end (a hint for the matching); Backspace discards the audio since the last mark and lets the user re-read. No need to say punctuation aloud.
4. **Review.** The page and the text appear side by side, every word linked to its box. Words are colored by confidence. The user jumps through the uncertain words with the keyboard, listens to the clip if needed, and fixes them.
5. **Export.** Plain `.txt` for one page, a page range or the whole notebook.

Punctuation is read from the marks on the page and from the speech model (see Pipeline). Names and special words get their niqqud from the glossary automatically.

## Architecture

&#91;embedded content: architecture · app, helper, models, project folder\]

The app never calls a model directly: it sends requests to the helper, which runs the pipeline stages in order and writes results to the project folder.

## Pipeline stages

Each stage is a pure function from JSON in to JSON out, stored in the project folder, so any stage can be re-run alone.

### 1. Page prep

- The test notebook's PDF holds phone-scanner images (one image per page, 180–230 dpi, sizes vary), with curved lines near the binding and the facing page's text visible at the outer edge.
- Use each page's embedded image at its native resolution (`pypdfium2`); re-rendering at 300 dpi only upsamples. Pixel measures (letter width, gap threshold) are kept relative to the line height so resolution doesn't matter.
- Binarize (Sauvola) for analysis. The ruling on the test notebook is faint and mostly disappears on binarizing; remove what's left with morphological filtering so it doesn't merge with the writing.
- Keep the original color image for display; use the cleaned one for analysis.

### 2. Layout: lines, words, drawings

- **Lines:** Kraken's baseline segmenter (runs locally, ~5 s per page on CPU). It follows curved lines tightly but splits a line into pieces at wide gaps, so post-process:
  - join pieces that continue the same baseline (extrapolate from the local slope at the line's end; take the nearest piece that fits),
  - drop pieces lying entirely in the outer edge strip (facing-page text),
  - mark a small isolated piece in a bottom corner as the page number (not text).
  - Output a baseline, polygon and bounding box per line, ordered top to bottom.
  - (Spike: 28/28 lines on all three test pages. Projection profiles were tried first and failed on curved page tops.)
- **Paragraphs:** a vertical gap clearly larger than the normal line spacing starts a new paragraph (the test notebook has 2–4 per page, separated by a blank line). Paragraph breaks are kept through to export.
- **Drawings:** Kraken already leaves sketches mostly without lines. To find them: remove all ink inside the text lines' outlines (and the facing-page edge strip and bottom margin), drop small fragments (bits of letters sticking out of their outline, which would bridge a doodle and the text), join nearby strokes, and keep regions at least 2 lines tall (so circled chapter numbers aren't drawings). Lines lying mostly inside a drawing are skipped (not matched to spoken words). The user can add or remove drawing boxes. Spike on all 15 pages: the checkered square, the beetle, the leaf, the Y-shape and the shield/cat doodles found; no false drawings on the 10 text-only pages; the stray "line" Kraken found in the beetle skipped. Small margin spirals aren't marked (they have no lines anyway).
- **Words:** inside each line, split on horizontal gaps wider than a threshold learned from the page (Otsu on the gap widths; ~0.45 × line height on the test pages). Small marks sitting on the baseline (commas, periods) are left out of the gap computation, otherwise they glue words together; the letter י sits high, so position tells it apart. Output word boxes in reading order (right to left). These are *candidates*: stage 5 may merge or split them. (Spike: 58 boxes for 63 words; the misses are close-written pairs.)

### 3. Recording

- Capture mic audio in the renderer as **one continuous take per page**, saved as `audio/page.wav` (16 kHz mono). `MediaRecorder` gives webm/opus, so capture raw PCM with an AudioWorklet and resample to 16 kHz.
- The user picks the start line before recording; it's a hint for stage 5, which can also find the start on its own.
- **Space is optional**: each press stores a line-end timestamp, taken from the audio clock (`AudioContext.currentTime`), not wall-clock time. Stage 5 uses these as soft hints. (Spike: the reader paused at sentence ends, not line ends, so pauses can't give line breaks; matching to the page does.)
- **Backspace** marks the audio since the last Space (or since the start) as discarded (kept in the file, ignored by the pipeline); the user re-reads in the same take.
- **Re-recording in review (Cmd+R)** records a separate override clip for that line (`audio/line-03.wav`), which replaces the line's window in the take; stages 4–6 re-run for that line only.

### 4. Speech recognition

- Model: ivrit.ai's Hebrew fine-tune of Whisper large-v3-turbo (official CTranslate2 build), via `faster-whisper` with `device="mps"` (Metal), falling back to `device="cpu"`, int8, if the experimental Metal backend fails.
  - Spike: Metal 4.5–5.6× real time, CPU 2.9×. mlx-whisper (after converting the model) was equivalent: 5–6×, same accuracy, word times within 0.06 s, but greedy-only and needs a conversion step; kept as plan B.
  - Decode audio with PyAV ourselves and pass samples in (faster-whisper's own decoding breaks with current PyAV).
- **Transcribe the whole page take**, not line by line. Whisper adds punctuation and picks words better when it hears whole sentences.
- **Chunk the take ourselves at pauses.** Whisper only hears 30 s at a time. Cut the take into chunks of up to ~25 s at silences (sentence ends, from VAD), skipping discarded audio, so no cut falls mid-sentence.
- **Prompt every chunk explicitly** with the glossary words plus the last sentence or two of the previous chunk's text. Turn the engine's own carry-over (`condition_on_previous_text`) off. This keeps spelling and punctuation consistent across the page, keeps glossary hints active in every chunk (with carry-over off, the reference implementation only applies the initial prompt to the first window), and stops one bad chunk (e.g. a hallucination loop) from spreading to the rest.
- Settings: `language=he`, word timestamps on, beam size 5.
- Keep per-word probabilities; they feed the confidence colors.
- Spike accuracy (page 115, paragraph 1): 11% word error rate: 2 spelling variants (השרפה/השריפה), 4 sound-alike words (טועה/תועה, עומה/הומה), 1 dropped word. Word end times land within ~0.05 s of real silences.

### 4b. Sound-alike suggestions and the handwriting check

Most recognition errors are words that sound right but are spelled wrong for the sentence (טועה/תועה, עומה/הומה, הסטני/השטני, בתחתיה/מתחתיה). The speech model is confident about them (0.82–0.98, like correct words), so confidence colors alone won't catch them.

- **Language model.** For each word, generate only sound-alike **letter swaps**: ס↔ש, ת↔ט, כ↔ח↔ק, ב↔ו, א↔ע↔ה, ב↔מ as the first letter; at most 2, each letter changed at most once. No vowel letters removed (that makes a different real word: במרומה → במרמה) or added (in the spike those suggestions were mostly wrong — מנטרים → מנטורים — and the handwriting can't judge them). Score with DictaBERT (`dicta-il/dictabert`, 0.74 GB, Metal): one masked pass per word shortlists the 3 best whole-word variants, then pseudo-log-likelihood of the original vs. the shortlist in ±12 words of context. Spike: 11 suggestions on 3 pages, 8 right.
- **Handwriting check.** A letter classifier trained on the notebook's own trusted words (user-corrected, accepted, or confidently read with no open suggestion): each word's letters are located by position (its box divided by letter widths, narrow letters like י ו ן counted smaller), cropped to 32×32, described with HOG + a 12×12 pixel grid, logistic regression. For a suggestion, compare log P of the two spellings at the swapped letters. Starts once the notebook has ~800 trusted letters (about two pages). Spike: on unseen pages it prefers the original over a sound-alike swap in 84% of 2,003 cases; when |score| > 2 it decides 69% of cases and is right in 93%. It can't judge added/removed letters (≈ chance).
- **Decision.** Handwriting agrees (score ≥ 2): the fix is **applied automatically**, the word stays yellow with "was: …" and **U/ו** undoes it. Handwriting disagrees (≤ −2): the suggestion is dropped. Otherwise it stays a suggestion (Accept / Ignore, S/ד), with the handwriting's leaning shown. Spike on the 3 pages: 5 fixes applied (all right), 2 wrong suggestions dropped, 4 left as suggestions (3 right, 1 wrong).

### 5. Align spoken words to word boxes

The heart of the app. It also decides which line each word belongs to. Align all N spoken words of the take to all M word boxes of the page (lines concatenated in reading order) with dynamic programming:

- Expected width of a word = its letter count × the user's average letter width (learned per notebook, refined after each reviewed page; ~0.35 × line height on the test notebook).
- Allowed moves: 1 word ↔ 1 box, 2 words ↔ 1 box (written joined), 1 word ↔ 2 boxes on the same line (gap inside a word), a spoken word with no box, a box with no spoken word.
- Cost = |log(box width / expected width)| + a penalty per merge, split or skip. Keep the score as alignment confidence.
- **Free start and end:** boxes before the first spoken word and after the last cost nothing, so reading can start mid-page and stop anywhere. The start line picked by the user, and any Space presses, add soft costs.
- Each word's line = the line of its box. Lines with no words are "not read".
- Output: every word gets a box (or is flagged), every box in the read range gets a word (or is flagged).
- Spike: page 115 paragraph 1, 62/62 words on the right line (also when aligning the whole 28-line page at once); page 114's mid-page start and page 116's partial last paragraph found correctly. Seen by eye: occasionally a line-final word is pushed to the next line. To be measured with more ground truth.

### 6. Punctuation from the page

Two sources, checked against each other: Whisper's automatic punctuation and the marks written on the page.

- **Whisper's punctuation** is the starting point. It places marks well, but in the spike it often wrote a period where the page has a comma (the user chains clauses with commas), invented commas where the page has none, and never adds what isn't heard.
- **Marks on the page** (`marks.py`): the ink components of each line (inside its outline plus a band around the baseline, where periods often sit) that aren't letters, measured in letter heights against the **neighbouring letters' top and bottom** (more reliable than the Kraken baseline, which sits a little below the letters):
  - period: small (≤ 0.32 letter heights), starts in the lower half, ends on the bottom line; or a short tick that starts very low and stays on the line. If it touches the word it must sit low (else it's a piece of a letter).
  - comma: a short stroke (≥ 0.3 tall) that starts in the lower part and reaches below the letters, with a clear gap after the word (≥ 0.05). A vav or yod starts at the letter top, so it isn't mistaken for one.
  - colon: two dots stacked; dash: a flat stroke at mid height.
  - **Quote marks: not read from the page.** In the test notebook a quote mark (״) is a short high stroke, like a yod; the gaps to the neighbouring letters overlap (a real closing quote 0.27 / 0.22 letter heights; yods 0.38 / 0.62, and many with a large gap on one side at a word's start or end). Whisper doesn't write them either (it doesn't hear them).
  - a mark belongs to the word on its right (Hebrew runs right to left).
  - **Parentheses / brackets: not read from the page.** The test notebook's square brackets measure like a ל (a tall stroke rising above the letters with a short hook, often at a word's start). Whisper writes them from intonation ("(כמו בשנה שעברה)," for "[כמו בשנה שעברה],"), so its brackets are kept; missing ones are added by hand.
  - Spike: page 115 paragraph 1 (thresholds tuned on it): 11/11 marks found with the right type, 1 extra. Page 116 (not tuned on), checked by eye: roughly 3 of 4 real marks found; where page and Whisper disagree, the page is usually right.
- **One mark per gap**: several page marks in one gap (",,", ".,") are usually one mark in pieces or a letter's dot; the page contributes at most one sentence mark — the one the speech model also has, else the nearest to the word. (Before: 9 doubled marks on the test pages, after: 0.)
- **Shva in a final kaf**: the writer marks the shva inside ך (ךְ), which reads as a comma; after a word ending in ך the page's mark is ignored and the speech model's kept (12 false commas on the test pages).
- Pages transcribed with older rules are re-read when opened (`marks_version`); words the user edited or accepted, and punctuation the user chose, are left alone.
- **Merging** (per word; words the user edited are left alone; Whisper's original marks are kept so it can be re-run):
  - Page mark and Whisper agree → keep it.
  - Page mark only → insert it, yellow on the mark (a Tab stop).
  - Page mark and Whisper differ → use the page, yellow on the mark (a Tab stop).
  - Whisper only → kept by default (setting), a dotted line under the mark, not a Tab stop: in the spike most of these were Whisper's inventions, but some were marks the page reading missed.
  - The inspector shows both versions with "Use …" / "No mark" buttons.
  - Spike result (page 115 paragraph 1 vs. ground truth): Whisper alone 7/11 marks exactly right, 4 wrong type, 2 extra; merged 11/11, 0 wrong type, 3 extra (2 dotted Whisper-only + 1 false page mark).
- Pages transcribed before M4 get their page marks read when opened.
- **Spoken quotes and brackets (built; a setting, off by default).** For dialogue: the reader says **מרכאות** where a quote mark goes and **סוגריים** where a bracket goes; the first opens, the next closes (per page). The command words are removed before matching to the page and turned into marks on the neighbouring words (a comma or period goes inside a quote, ״נכון,״, and outside a bracket, אתמול).). Exact words only (מרכאות / מירכאות, סוגריים): near matches caught real words (מרכבות, סוגרת). They're added to the speech model's hints. Spike: on a synthesised Hebrew reading (macOS voice Carmit) Whisper wrote all six command words exactly; with a real voice still to be confirmed.
- **Optional extras, off by default (not built):** spoken commands for other marks (פסיק → `,`, נקודה → `.`), and a local DictaLM pass that may only insert punctuation (output rejected if any letter changes).

### 7. Glossary and niqqud

- The glossary is a per-project list (in `project.json`) of names **and invented terms**: plain form → pointed form, a rule, optional "also heard as" variants (misspellings that are corrected to the glossary spelling) and a note.
- A word matches when, after removing up to 3 prefix letters (ו ה ב כ ל מ ש, stacked like ולדנה, שבדנה), it equals the plain form or a variant (niqqud and final letters ignored). The prefix stays unpointed.
- Each entry has a rule: first appearance only (default, configurable in settings), every appearance, or never (spelling only). Niqqud is applied **on export** (also "As exported" and Copy page); the text in the app stays plain.
- "First appearance" counts from the start of the notebook (or the last chapter mark), not from the start of what is exported: pages before the first exported one are read first, so copying or exporting single pages doesn't repeat the niqqud on every page.
- "Also heard as" variants are also corrected **in the words themselves** (shown in Review), after every transcription and on all transcribed pages whenever the glossary is saved; words the user edited are left alone, the original stays in `glossary_from`. A variant covers that exact form only (שרפה → שריפה, not the construct שרפת → שריפת, which needs its own entry). Use: a spelling the speech model keeps getting "wrong" for this writer (Whisper always writes שרפה, the notebook has שריפה) → an entry with rule "never" and the variant.
- **Chapters:** the notebook marks them with circled numbers; the user marks the chapter-start line by hand in the app ("A chapter starts at line N"), shown as § on the page. "First appearance" counts from the start of the export and restarts at each chapter mark.
- Handwritten niqqud on the page is never copied into the text; when the handwriting model (M8) sees niqqud on a word that isn't in the glossary, it only suggests adding it.
- Adding entries: select a word in the text (or put the cursor in it) and press ⌘G / "Add to glossary". The app guesses the plain form (removing prefixes of a known entry) and proposes a pointing with dicta-onnx (the local Nakdan model, int8, 308 MB, ~0.1 s); when the word carries a prefix, the bare word is pointed (דָּנָה, not the after-prefix form דָנָה). The user confirms or edits it.
- **Hints to the speech model:** the glossary spellings are passed as faster-whisper `hotwords`, which go into the prompt of every 30 s window (verified in its source), so no own chunking is needed for this. Spike: with דנה + ספירליה as hints, דאנה and ספירלייה disappeared (consistent spelling) and the word error rate was unchanged.

### 8. Confidence

Each word gets one score from three signals: speech probability, alignment cost, and whether it was edited by punctuation or glossary rules. Three bands drive the colors in review: confident, check, unsure. Thresholds are tunable in settings.

## Review screen

The review screen is where time is saved: every word is one click from its handwriting and its audio.

**Layout:** page image on the left (zoom, pan), editable right-to-left text on the right, a thin page strip at the bottom. The text keeps the notebook's line breaks during review.

**Linking**

- Hover or select a word in the text → its box lights up on the page, and the reverse.
- The current line gets a soft band on the page; the text scrolls to follow.

**Colors (box outline + text underline)**

| Band | Color | Meaning |
| --- | --- | --- |
| Confident | none / faint green | high speech probability, clean alignment |
| Check | yellow | lower probability, or a merge/split during alignment |
| Unsure | red | very low probability, or no box / no word |
| Box with no word | red hatched box | written on the page but not heard |
| Glossary word | blue | niqqud added from the glossary |

**Keyboard**

Two modes, so single-letter shortcuts never clash with typing. Provisional; the bindings may be redefined later.

- **Navigate mode** (default): a word is selected, keys are shortcuts.
- **Edit mode**: the selected word is an editable text field.

| Mode | Key | Action |
| --- | --- | --- |
| Navigate | Tab / Shift+Tab | next / previous yellow or red word |
| Navigate | A | accept the word as is (marks it confident) |
| Navigate | Enter | edit the selected word (enters edit mode) |
| Navigate | P | play the word's audio (with 0.3 s padding) |
| Navigate | L | play the whole line |
| Edit | Enter | commit the edit, back to navigate mode |
| Edit | Esc | cancel the edit, back to navigate mode |
| Both | Cmd+R | re-record the current line |
| Both | Cmd+G | add the selected word to the glossary |
| Both | Cmd+Z | undo |

**Word popover (click):** a zoomed crop of the handwriting, a play button, the speech alternatives with their probabilities, and the glossary match if any.

**Fixing alignment:** drag a word onto a different box, or Alt-drag across boxes to merge them. Changing a box never loses the typed text.

**Edits teach the app:** every accepted or corrected word with its box is saved as a labeled sample (crop + text). This feeds the letter-width estimate now and a personal handwriting model later.

## Data model

One folder per notebook, plain files only, so it is easy to back up, diff and debug.

```
MyNotebook.ntproj/
  project.json        # settings, glossary, punctuation table, learned letter width
  source.pdf
  pages/
    007/
      page.png        # the page's scan at native resolution (display)
      clean.png       # binarized, ruling removed (analysis)
      page.json       # everything below
      audio/
        page.wav      # the continuous take for the whole page
        line-03.wav   # override clip, only if line 3 was re-recorded in review
  samples/            # reviewed word crops + text, for a future handwriting model
  export/
```

`page.json` (simplified):

```json
{
  "page": 7,
  "status": "reviewed",
  "audio": {
    "file": "audio/page.wav",
    "discarded": [[12.40, 15.02]]
  },
  "drawings": [{"id": "d1", "bbox": [120, 900, 640, 1380]}],
  "lines": [{
    "id": "l3",
    "order": 3,
    "paragraph": 1,
    "read": true,
    "baseline": [[2380, 470], [1300, 466], [210, 472]],
    "polygon": [[2380, 410], [210, 405], [210, 480], [2380, 488]],
    "audio": {"file": "audio/page.wav", "start": 15.02, "end": 21.65},
    "asr": {"model": "ivrit-turbo", "text": "...", "words": [{"w": "...", "t0": 0.32, "t1": 0.71, "p": 0.93}]},
    "words": [{
      "id": "w3_5",
      "text": "...",
      "bbox": [1650, 412, 1820, 478],
      "audio": [0.32, 0.71],
      "source": "asr",
      "confidence": 0.88,
      "band": "confident",
      "flags": ["glossary"],
      "edited": false
    }]
  }]
}
```

- `paragraph` numbers the paragraphs on the page; `read` is false for lines outside the stretch that was read.
- Each line's `audio` is a window into `page.wav`, from its first word's start to its last word's end; if the line was re-recorded in review, it points to its override clip instead (e.g. `{"file": "audio/line-03.wav", "start": 0.0, "end": 6.6}`). Word `audio` times are relative to the line's file.
- `discarded` lists stretches of the take thrown away with Backspace during reading.
- `source` is one of `asr`, `user`, `glossary`, `punct`, later `htr` (handwriting model).
- Punctuation marks are stored as their own tokens with no box, so the text and the boxes stay in step.
- Export walks the read lines in order, joins words with spaces and attaches punctuation tokens without a space. Default: one line per paragraph (the notebook's line breaks removed), a blank line between paragraphs; a paragraph that runs over a page break (the page doesn't end a sentence) is joined. Option: keep the notebook's line breaks. The app itself always shows the notebook's lines, for checking against the page.

## Tech stack and models

An Electron front end with a Python helper process; every model runs on the Mac.

| Part | Choice | Why / note |
| --- | --- | --- |
| App shell | Electron + React + TypeScript | Mature RTL text support, canvas overlays, mic access |
| Page display | Rendered PNGs on a canvas with an SVG overlay for boxes | Same pixels the analysis used, so boxes line up exactly |
| Helper process | Python 3.12, packaged with `uv`, talks JSON-RPC over stdio | All ML libraries are Python; one long-lived process keeps models loaded |
| PDF page images | `pypdfium2` | Extract each page's scan at native resolution; no system dependencies |
| Image work | OpenCV, scikit-image | Binarize, remove ruling, connected components, word gaps |
| Line segmentation | Kraken 7.2 baseline segmenter + our joining step | Follows curved lines; ~5 s/page on CPU. Kraken needs PyTorch and numpy < 2.5, so it runs in its own Python environment, called as a subprocess |
| Voice detection | Silero VAD | Cut the take into chunks at pauses, trim silence |
| Speech recognition | [ivrit-ai/whisper-large-v3-turbo-ct2](https://huggingface.co/ivrit-ai/whisper-large-v3-turbo-ct2) via `faster-whisper`, `device="mps"` (Metal), CPU int8 fallback | Best open Hebrew model; official build, beam search, ~5× real time on Metal. mlx-whisper with a converted model was equivalent in the spike (plan B) |
| Punctuation model | [DictaLM 3.0](https://arxiv.org/abs/2602.02104) instruct, via Ollama or MLX | Optional, off by default. Hebrew-first open model. The Mac has 16 GB RAM, so use the 1.7B model, loaded only for this pass |
| Sound-alike check | [DictaBERT](https://huggingface.co/dicta-il/dictabert) (base) via `transformers` + PyTorch (Metal) | Hebrew masked language model; scores sound-alike spellings in context (stage 4b). ~12 s per page |
| Niqqud for glossary | [dicta-onnx](https://pypi.org/project/dicta-onnx/) with the int8 model (308 MB, from its GitHub release, stored in `~/.cache/notebook-transcriber/`) | Local Nakdan model (dictabert-large-char-menaked), ~0.1 s per sentence |
| Storage | Plain JSON + PNG + WAV in a project folder | Easy backup and debugging |

All models are downloaded once on first run into `~/Library/Application Support/<app>/models`, with a progress bar. After that the app never needs the network.

## Build order

Eight milestones; the first two already beat Google voice typing, and each ends with something usable.

0. **Spike — done (Nov 23, 2026).** Code and full results in `spike/` (`spike/README.md`). In short:
   - the ivrit.ai model writes good punctuation positions; the mark type sometimes differs from the page;
   - Kraken + joining finds all lines; projection profiles don't on curved scans;
   - matching words to word boxes assigns lines without Space presses;
   - faster-whisper on Metal is fast enough.
   - Not yet tried: `dicta-onnx` (needed for M2). DictaLM 3.0 1.7B exists, not tried (optional feature).
1. **M1 — Read and transcribe.** A usable app, end to end:
   - import a PDF (page images, Kraken lines, paragraphs, word boxes in the background);
   - a page view with the numbered lines drawn on the scan;
   - pick a start line, record one continuous take (Backspace discards, Space optional);
   - whole-page transcription with Whisper's own punctuation;
   - page-level word-to-box matching to place the words on lines;
   - the text shown next to the page with the notebook's line breaks and paragraphs, editable as plain text;
   - plain-text export for a page or the whole notebook (paragraphs, or the notebook's line breaks as an option);
   - sound-alike suggestions (stage 4b) as an Accept / Ignore list.
   - Done when: a 20-line page can be read and exported, with words in the right lines.
2. **M2 — Glossary. Done (Nov 23, 2026).** Glossary editor, add from the text with a proposed pointing (dicta-onnx), stacked-prefix matching, variants, first / every / never rules with manual chapter marks, niqqud on export, glossary hints to Whisper, settings screen (default rule, sound-alike check on/off, export line breaks).
   - Done when: a glossary name appears pointed even with a ו or ל prefix. (Tested: ודָנָה after a chapter mark.)
3. **M3 — Review screen. Done (Nov 23, 2026).**
   - Review view: words colored by confidence (stage 8: speech probability < 0.6 or no box = unsure; < 0.9, alignment fit > 0.6, merge/split, an open suggestion, or an automatic fix = check), linked highlighting both ways (word ↔ its box on the page, rest of the line boxed faintly, the page scrolls to it), an inspector with the handwriting crop, the reasons for the color and the suggestion.
   - Keys (by physical position, so they also work with the Hebrew layout): Tab / Shift+Tab doubtful words, ← → words, ↑ ↓ lines, A/ש accept, S/ד accept the suggestion, U/ו undo an automatic fix, Enter edit inline, Delete remove, P/פ play word (0.3 s padding), L/ך play line, ⌘R re-record the line, ⌘G/⌘ע add to glossary.
   - Words are the source of truth: free-text edits in the Text view are matched back to the words over the whole page, so corrected words keep their box and audio and every word keeps its notebook line, whatever happens to the line breaks. Earlier plain-text edits are migrated automatically.
   - One box per word: about 1 word in 5 was matched together with a neighbour to one box (words written close together); such a box is cut automatically (letter counts predict where; the cut goes to the column with the least ink within ±20% of the box width). On the 3 test pages no word shares a box afterwards. Manual fixes on the page: drag the selected box's left/right edge; ⌥-click a box to link the selected word to it (also across lines); ⌥⇧-click to add a box.
   - Re-recording one line (⌘R): a separate clip `audio/line-NN.wav`, transcribed alone with the previous lines' text as context and the glossary as hints, matched to that line's boxes; sound-alikes re-checked for that line. Note: a line on its own loses some punctuation (Whisper places it better in whole sentences), so this is for fixing a misread line, not routine use.
   - Sound-alike suggestions with the handwriting check (stage 4b).
   - Added after first use (Nov 24, 2026): **⌘Z undo** of every page edit (words, punctuation, boxes, line edits, suggestions, chapter marks; the last 50 steps per page in `pages/NNN/undo/`; typing in a text field keeps its own undo). **Re-matching after edits**: when an edit adds or removes words on a line, that line's words are matched to its boxes again (simulated missed-word-added-back on 6 lines: 47/58 words back on the same handwriting; before, the words pushed out of place stayed there and the added word had no box). **Wider handwriting strip** in the inspector: the word with its neighbours, full line height and room below, the word's box in blue and the neighbouring boxes dashed.
   - Added (Nov 24, 2026, second round): **moving words to the neighbouring line** — ⌥↑ moves the selected word and the words before it on its line to the end of the previous line, ⌥↓ it and the words after it to the start of the next line (skipped lines and lines in drawings are passed over); both lines are re-matched. For pages where crowded writing or small line fragments shift the matching by a line. A line holding clearly more spoken words than handwritten ones (more than 1.3× its boxes and 3 more) gets a ⚠; very long rows scroll inside themselves. **Page finished** (⌘⇧F or the button): shown in the page list and the Pages view; finished pages are fully checked training material for M8.
   - Done when: on 5 test pages at least 90% of words land in the right box, and clicking any word plays the right sound. Not yet measured: only 3 recorded test pages, and box accuracy needs hand-checked boxes; to do when 2 more pages are recorded.
4. **M4 — Punctuation from the page. Done (Nov 23, 2026).** Mark detection by geometry against the neighbouring letters, merging with Whisper's punctuation, yellow marks for page-only / different marks (Tab stops), dotted for Whisper-only, fix buttons in the inspector, a setting for Whisper-only marks; parentheses left to Whisper (see stage 6).
   - Done when: on the test pages most handwritten periods and commas are found, and every disagreement shows yellow. (Page 115 paragraph 1: 11/11; page 116 by eye ≈ 3 of 4; disagreements highlighted.)
5. **M5 — Layout editing and drawings. Done (Nov 23, 2026).**
   - Drawing detection (stage 2) and dashed drawing boxes on the page.
   - **Edit lines** mode: select a line → merge with next, move earlier / later in reading order, skip / include; ✂ split a line where clicked; drag a selected line's end handles to stretch or shrink it (e.g. to take in a first letter written apart); + Line (drag a rectangle around text with no line); + Drawing (drag a rectangle; lines inside are skipped); remove a drawing (×); find drawings again.
   - After every edit: word boxes and paragraphs recomputed; on a transcribed page the words are re-placed on the new lines by the same matching (no new speech recognition), keeping their text, edits, audio and punctuation. Manual box adjustments on that page are lost by a layout edit (boxes are recomputed), and Revert then returns to the re-placed version.
   - Done when: a page with a drawing and a margin note can be fixed in under 30 seconds. Drawings need no action when found automatically, else one drag; a margin note read in the wrong place is one click plus ↑/↓, a missed one one drag (+ Line). Estimated, not timed with the user.
6. **M6 — Recording view. Done (Nov 24, 2026).** Record page after page without waiting.
   - **Background transcription queue** (helper): saving a recording queues the page and returns at once; one worker transcribes pages in order (speech recognition, matching, punctuation, sound-alike + handwriting check, about 30–40 s per page). The queue state (current page with its stage, pages waiting) goes to the app with every change; a page queued again replaces its waiting request; failures are reported per page. Everything else keeps working meanwhile (opening another page: 0.05 s during a transcription). The speech step reports its percentage only at the end, so stage names are shown rather than a progress bar.
   - **Pages view** ("▦ Pages" in the top bar): every page as a card (thumbnail, status: not read / recording / waiting n in queue / transcribing with its stage / transcribed / n of m words reviewed). Select a page and press **R** (or the button) to record it — read from the notebook — and **Enter** to stop: the page is queued and the next page selected, so the next press of R records it. Double-click a card opens the page. Space / Backspace work as before while recording.
   - Recording in the single-page view uses the queue too; a page waiting or being transcribed shows its progress instead of the text and can't be recorded again until it's done.
   - The installed app runs a **frozen copy of the helper** (`release/helper`, updated by `release/update_helper.sh` with each release; Python environments shared through links), so development can't affect the app in use.
7. **M7 (later) — Unfiltered scans ("None" colour mode).** Scans are to be made with no colour correction (Genius Scan: filter "None", file size "Actual"; pages pressed flat): filters behave unpredictably (Genius "Color Soft" accentuated the show-through from the other side of the page) and would have to be re-checked for every new pen, notebook or phone, while unfiltered scans keep the full detail for M8. But they keep the notebook's printed ruling, which the pipeline must separate from the ink.
   - Done so far (Nov 24, 2026): ruling removal (`layout.remove_ruling`): long thin horizontal segments grouped into page-wide ruled lines, a straight line fitted through each, and along it ink lighter than the pen removed (so letters' base strokes lying on the ruling stay). Applied wherever ink is read. On the test scan (`test_scan.pdf`: pages 114–115 rescanned, 300 dpi equivalent, flat) it fixed the word boxes (words on the right line, page 115 paragraph 1: 54/63, old filtered scan 56/63) and found no ruling on the old scan (no change there).
   - To do: punctuation on unfiltered scans. The marks sit on the ruled line and the lightness-based removal eats their light parts (a comma's tail; some marks vanish): page marks on page 115 paragraph 1 dropped from 11/11 (old filtered scan) to 5/11 right, 1 wrong type, 4 missed, 5 extra. Plan: tell the ruling from the pen by colour as well, keep small pieces of ink sitting on the line, then re-tune the mark rules on the unfiltered scan (page 115 has ground truth) and check on a second typed paragraph that wasn't used for tuning (e.g. page 116 paragraph 1) and on the old scan. Expected: about as good as the filtered scans (≈ 9–11/11 on the tuned paragraph, ≈ 3 of 4 elsewhere), not better; the gain is robustness to pen / notebook / camera changes and better material for M8. Estimated 1–2 hours.
   - Until then, on unfiltered scans the page punctuation is weak, so the merged punctuation is roughly Whisper's (≈ 7 of 11 marks right on page 115, plus some false yellow marks). Pages transcribed before the fix keep that punctuation unless re-read (a "re-read punctuation, keeping reviewed words" action would be needed).
   - **Hyphens** (with the punctuation re-tuning): the speech model writes "כחול ירוק" as two words where the page has "כחול-ירוק". Needs the dash detected between the words and the two words joined without a space (in the text and the export). The ground truth has 3 hyphens to test with.
   - Ground truth now: `test_files/gt_114_116.txt` (pages 114–116, 124 punctuation marks), and `spike/compare_scans.py` scores any scan end to end against it. Results (Nov 24, 2026), final punctuation: Whisper alone 100/124 right, 20 wrong type, 4 missed, 5 extra; old filtered scan 112 / 11 / 1 / 18; a better-lit filtered scan (`FFV3_scan_v2.pdf`) 103 / 19 / 2 / 36, because better lighting keeps the printed ruling (9–16 rulings found per page) and the ruling removal eats marks. So the ruling problem isn't only about unfiltered scans: any better scan brings it back. Also: Whisper's marks that the page doesn't show are mostly right (only 5 extra on 3 pages), so keeping them is the right default.
   - With every new pen, notebook or phone: type one paragraph of ground truth for one page, to check the rules still hold.
8. **M8 (later) — Learning your handwriting.** A small classifier for your punctuation marks, then a personal word recognizer (e.g. TrOCR or Kraken) trained on saved word samples, used as a second opinion on spelling.
   - Start when: roughly 2,000 reviewed words are saved (an estimate to test, not a known number).
   - **Crossed-out words**: recognise strikethrough so crossed-out words are skipped (no word expected there, not shown as "not heard", not used as training samples). No ground truth needed beyond the reviewed pages: crossed-out words are simply not read.
   - Training material: pages transcribed **faithfully to the page** — the writer's spelling and grammar errors kept, words misread aloud corrected to what's written (the speech model's version stays in `asr_words` / the page history). Glossary "also heard as" variants and automatic sound-alike fixes push towards standard spelling, so they need checking against the page; a "faithful transcription" setting (never change spelling automatically) could be added. A corrected reading version, if wanted, should be a separate layer on top, not edits to the faithful text.

**Backlog (small, not scheduled)**

- **Notes that aren't read aloud** (e.g. "(לבדוק את הפרק הקודם)"): mark an area or a line as a "note" — not read, not expected in the text, not used for training. Today: a whole line → Edit lines → Skip; a note inside a line leaves "not heard" boxes, harmless.
- **Hesitation pauses** while deciphering one's own handwriting make Whisper add commas/periods. Ideas: drop a Whisper-only mark that follows an unusually long pause when the page shows none (the ground truth can tell whether pause length separates real marks from hesitations); a pause button while recording (not useful when reading from the notebook rather than the screen).
- **⌘R re-record a line**: rarely used (editing is easier); could be made less prominent.
- **"Words not synced"** (reported after first use; partly the re-matching above): needs an example page/line to investigate (wrong handwriting outlined, or the word's audio off?).
- **English keyboard while editing**: macOS doesn't let an app switch the keyboard language; instead, notice English letters typed into a Hebrew word and offer to convert them by key position ("akuo" → "שלום").
- **A "faithful transcription" setting**: never change spelling automatically (automatic sound-alike fixes become suggestions).

## Risks and open questions

The biggest risk is word-to-box alignment on crowded handwriting; everything else has a known fallback.

| Risk | Fallback |
| --- | --- |
| Words in a line touch, so gaps don't separate them | Alignment allows merges; the user can Alt-drag to fix; worst case, words stay linked to the line only |
| A line-final word is matched to the next line (seen occasionally in the spike) | Optional Space presses as hints; in review, a word can be moved to the other line |
| Kraken's environment (PyTorch, older numpy) conflicts with the helper's | Run Kraken in its own environment as a subprocess |
| Disk space (the dev Mac had 13 GB free) | Models need ~1.6 GB (speech) + Kraken/PyTorch; download only what's used |
| Reading aloud doesn't match the page (paraphrasing, skipped words) | Read verbatim; boxes with no word turn red hatched so gaps are visible |
| Whisper invents text on very short or silent clips | VAD trimming; skip clips under 0.3 s of speech; engine carry-over off, each chunk gets an explicit prompt instead (stage 4), so a bad chunk can't spread |
| Dots and commas lost as scan noise or with the ruling lines | Keep small marks near word boxes when cleaning; fall back to Whisper's punctuation, flagged yellow |
| Not enough memory to keep Whisper and DictaLM loaded together | Load DictaLM only for the punctuation pass, then unload it |
| English words or numbers in the text | Whisper handles mixed speech; the UI must render mixed right-to-left / left-to-right correctly (test it) |

**Open questions**

- (Answered: chapters are circled numbers in the notebook; the user marks chapter starts by hand in the app.)
- (Answered: export reflows into paragraphs by default; keeping the notebook's line breaks is an option.)
- (Answered by the spike: the paper is faintly ruled; scans come from a phone scanner app.)

**Notes for Claude Code**

- Collect 5–10 real scanned pages with recordings early and keep them as test fixtures; measure each milestone on them.
- Hide each model behind a small interface (`transcribe`, `segment_lines`, `punctuate`, `point`) so models can be swapped.
- Use Hebrew-aware text comparison: ignore niqqud, treat final letters (ך ם ן ף ץ) as their normal forms when matching.
- Request mic permission properly on macOS (Info.plist usage string + Electron `systemPreferences.askForMediaAccess`).
- Never block the UI: all model work runs in the helper process, with progress events back to the UI.

**Sources**

- [ivrit.ai models on Hugging Face](https://huggingface.co/ivrit-ai)
- [ivrit.ai Whisper large-v3-turbo (faster-whisper build)](https://huggingface.co/ivrit-ai/whisper-large-v3-turbo-ct2)
- [Dicta-LM 3.0 paper](https://arxiv.org/abs/2602.02104)
- [dicta-onnx on PyPI](https://pypi.org/project/dicta-onnx/)
- [Microsoft Q&A: no Hebrew handwriting support in Document Intelligence](https://learn.microsoft.com/en-us/answers/questions/1639127/hebrew-handwritten-text-optimal-solution-for-recog)
