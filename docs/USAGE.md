# Using Notebook Transcriber

Read handwritten Hebrew notebook pages aloud; get clean text with the notebook's lines and paragraphs.
Runs fully offline on the Mac. Design: [`SPEC.md`](SPEC.md). Assumption tests: [`spike/README.md`](../spike/README.md).

## Run it

**The app:** `~/Applications/Notebook Transcriber.app` — open it from Launchpad, Spotlight or the Dock.
It uses the Python helper and its environments from the repository (`release/helper`, `helper/`) and the
models from `~/.cache/huggingface` and `~/.cache/notebook-transcriber`, so keep the repository where it was built.

The installed app runs a **frozen copy of the helper** (`release/helper`), so work on the code in `helper/`
can't affect it while you use it. The Python environments and models are shared through links.

**To release a new version** (after it's finished and tested): update the frozen helper, then rebuild and
reinstall the app, then restart it:

```
release/update_helper.sh
cd app && npm run dist
ditto "release/mac-arm64/Notebook Transcriber.app" ~/Applications/"Notebook Transcriber.app"
```

**For development**, run it without packaging: `cd app && npm start`.

## Recording again keeps the old take

Recording a page again, using another audio file, or "Transcribe again" replaces the page's words. If you
reviewed words on that page, the app asks first. Either way, the previous words (`page.json`) and recordings
are copied to `pages/NNN/history/<date-time>/` before anything is replaced (re-recording one line too).

## Use it (M1)

1. **Import PDF…** — pick the scanned notebook, then where to put the project folder (`<name>.ntproj`).
   Page images appear within seconds; lines are found in the background (~10 s per page).
2. Pick a page (top bar, or **[** / **]** for the previous / next page — the same keys with the Hebrew
   keyboard; not while typing). Lines are drawn on the scan, numbered. Drawings get a dashed box; lines inside a
   drawing are skipped. If a line is wrong, use **Edit lines…** (top left of the page): click a line, then
   **Merge with next**, **↑ Earlier / ↓ Later** (reading order, e.g. a margin note), **Skip / Include**; drag its
   orange end handles to stretch it; **✂ Split** cuts a line where you click; **+ Line** and **+ Drawing** let you
   drag a rectangle; **×** removes a drawing box. On a transcribed page the words are re-placed automatically.
3. **Click the line where you start reading** (default: line 1; reading can also start mid-page).
4. **● Record**, read naturally, **Stop and transcribe**. Transcription runs in the background (about half a
   minute per page), so you can go on working. **To read many pages in a row**, use **▦ Pages** (top left): select a
   page, press **R**, read it from the notebook, press **Enter** — the page is transcribed in the background and
   the next page is selected; press **R** again. Each card shows the page's status and progress.
   Optional keys while recording:
   `Space` = line end (a hint), `Backspace` = throw away what you said since the last mark, `Enter` = stop.
   Or **Use an audio file…** for an existing recording (m4a, mp3, wav…).
5. The text appears on the right in the **Review** view: one row per notebook line, every word linked to its
   handwriting. Yellow = check, red = unsure, blue = glossary word, dashed = possible sound-alike mistake.
   Click a word (or the handwriting on the page) to select it; the inspector shows the handwritten word.
   Keys: `Tab`/`Shift+Tab` next/previous doubtful word · `←` `→` words · `↑` `↓` lines · `A` accept ·
   `S` accept the suggestion · `Enter` edit · `Delete` remove · `P` play the word · `L` play the line.
   Words on the wrong line: select one and press **⌥↑** (it and the words before it go to the end of the
   previous line) or **⌥↓** (it and the words after it go to the start of the next line); a ⚠ marks a line with
   clearly too many words. **⌘⇧F** marks the page finished (shown in the Pages view).
   Letter keys work by position, so with the Hebrew keyboard too: `ש` accept, `ד` suggestion, `פ` play word,
   `ך` play line, `ו` undo an automatic fix, `⌘ע` add to glossary. `⌘R` re-records the selected word's line
   (read just that line; it's transcribed alone — fine for fixing a misread line, but it loses some commas).
   On the page: each word has its own box (words written close together are separated automatically).
   To fix a box, drag the left/right edge of the selected word's box; **⌥-click** handwriting to link the
   selected word to it (also across lines); **⌥⇧-click** to add a box to the word.
   The **Text** view is the same text as plain text for bigger edits; corrected words stay linked to their
   handwriting, and every word keeps its notebook line even if you join or split lines there.
   **Revert to transcription** undoes all edits; **⌘Z** undoes the last change (words, punctuation, boxes,
   line edits). When you add or remove a word, the line's words are matched to the handwriting again.
6. **Sound-alike mistakes** (e.g. טועה → תועה): after transcribing, a Hebrew language model looks for words
   spelled wrong for the sentence, and a small model trained on your own handwriting checks each one
   (from about the second transcribed page on). When both agree, the word is **fixed automatically** — it
   stays yellow, the inspector shows "was: …", and **U/ו** undoes it. When the handwriting disagrees, the
   suggestion is dropped; otherwise it stays a suggestion (dashed in Review; **S/ד** accepts it).
   **Check again** in the suggestion box re-runs the check (e.g. on pages checked with an older version).
7. **Punctuation** is read from the page too (periods, commas, colons, dashes, quote marks) and merged
   with the speech model's. A **yellow box** on a mark means the page and the speech model disagree, or only
   the page has it (Tab stops there); a **dotted line** under a mark means only the speech model has it
   (often invented). The inspector shows both and lets you pick ("Use …" / "No mark").
   **Quote marks and brackets** can't be read reliably from handwriting (they look like ' and ל). For
   dialogue, turn on **Spoken quotes and brackets** in Settings and say **מרכאות** where a quote mark goes and
   **סוגריים** where a bracket goes, while reading (the first opens, the next closes). Otherwise add them by
   editing the word; the speech model sometimes writes brackets from intonation. **Settings…** can drop
   the speech model's marks that the page doesn't show (for new transcriptions).
8. **Glossary** (names and invented terms): put the cursor in a word in the text and press **⌘G**. The app
   proposes the niqqud (check it), and you choose when to add it: first appearance (default), every appearance,
   or never (spelling only). "Also heard as" lists misspellings to correct (e.g. דאנה). **Glossary…** edits the list.
   Glossary words are found behind prefixes (ולדנה) and are given to the speech model as spelling hints for
   new transcriptions; "Also heard as" spellings are corrected in the text too (e.g. שריפה, also heard as שרפה,
   rule "Never" = spelling only). "First appearance" counts from the start of the notebook, also when copying
   single pages. Niqqud is added **on export**; the text you edit stays plain. Switch the text panel to
   **As exported** to see the page exactly as it will be exported, with glossary words highlighted.
9. **Chapters**: click the chapter's first line on the page and tick **A chapter starts at line N** (shown as §).
   "First appearance" niqqud starts again after each chapter mark.
10. **Copy page** (or **⌘⇧C**) copies the page exactly as **As exported** shows it — glossary niqqud,
   paragraphs per Settings — as text with right-to-left paragraphs, ready to paste into Google Docs.
11. **Export page…** / **Export all…** writes plain UTF-8 `.txt`: one line per paragraph, a blank line between
   paragraphs. **Settings…** can switch export to the notebook's line breaks, set the default niqqud rule,
   and turn the sound-alike check off.

The app reopens the last project on start.

## Layout

- `app/` — Electron + React + TypeScript (window, recording, page view, text).
- `helper/` — Python pipeline (JSON-RPC over stdio): page images, Kraken lines (own environment in
  `helper/kraken_env/`), paragraphs, word boxes, speech recognition (faster-whisper, Metal), word-to-line matching, export.
- `spike/` — throwaway experiments that tested the spec's assumptions.
- `tools/` — the dataset builder, setup, and the git guard against committing private material.
- `test_files/`, `test_projects/`, `dataset/` — private material (scans, recordings, transcripts); never committed.

## Known limits (M1)

- On long silences Whisper may invent a few words; keep pauses short or remove them in the text.
