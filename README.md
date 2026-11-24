<p align="center"><img src="docs/icon.png" width="96" alt=""></p>

# Notebook Transcriber

A Mac app that turns handwritten **Hebrew** notebook pages into clean text. You read each page aloud; the app
transcribes your voice and ties every spoken word to its place on the scanned page, so checking and
correcting the text is quick. Everything runs **offline** on the Mac.

**Why voice first:** reading personal Hebrew handwriting with a model isn't reliable today, but speech
recognition is. So the voice decides *what* the words are and the page decides *where* they are: the page
supplies the lines, paragraphs and punctuation, and lets you check each word against its handwriting.

## What it does

- **Import** a scanned PDF. Lines (curved ones too), paragraphs and drawings are found on each page.
- **Read aloud** — one page, or page after page (**Pages** view: record the next page while earlier ones are
  transcribed in the background). Start and stop anywhere on the page; no key presses needed while reading.
- **Speech recognition** with ivrit.ai's Hebrew Whisper (large-v3-turbo) on the Mac's GPU, about 5× real time.
- **Every word linked to its handwriting**: the words are matched to the word boxes on the page by expected
  width, which also decides each word's line. Words written close together are separated automatically.
- **Review**: click a word to see and hear it (its handwriting, its audio); doubtful words colored; keyboard
  review (Hebrew layout too); edit, undo, move words between lines, fix boxes on the page.
- **Punctuation** from the speech model and from the marks on the page, checked against each other.
- **Sound-alike fixes** (טועה → תועה): a Hebrew language model (DictaBERT) suggests them, and a small model
  trained on *your* handwriting confirms or rejects them.
- **Glossary** for names and invented terms: niqqud on first appearance (Dicta's Nakdan proposes it), found
  behind prefixes (ו ה ב כ ל מ ש), spelling corrected, given to the speech model as hints.
- **Export / copy** plain UTF-8 text with paragraphs, right-to-left, ready for Google Docs.
- **Dataset builder** for training your own handwriting model: scans, audio, verified words with their boxes
  and timing, word crops (`tools/make_dataset.py`).

See [`docs/USAGE.md`](docs/USAGE.md) for how to use it and the keyboard shortcuts, and
[`docs/SPEC.md`](docs/SPEC.md) for the design, the milestones, the measurements behind each decision, and
what's planned.

## Setup

Requirements: a Mac with Apple Silicon, [uv](https://docs.astral.sh/uv/), Node.js 22+.

```sh
tools/setup.sh          # Python environments, ~3 GB of models (once), the app's packages
cd app && npm start     # run it
```

To build a standalone app in `~/Applications`, see "Run it" in [`docs/USAGE.md`](docs/USAGE.md).

## How it's built

| Part | |
| --- | --- |
| `app/` | Electron + React + TypeScript: the window, recording (AudioWorklet, 16 kHz), page view, review |
| `helper/` | Python pipeline behind it (JSON-RPC over stdio): page images, lines ([Kraken](https://kraken.re), in `helper/kraken_env`), word boxes, speech recognition ([faster-whisper](https://github.com/SYSTRAN/faster-whisper) + [ivrit.ai](https://huggingface.co/ivrit-ai)), word matching, punctuation, glossary ([dicta-onnx](https://pypi.org/project/dicta-onnx/)), sound-alikes ([DictaBERT](https://huggingface.co/dicta-il/dictabert)), handwriting check, export |
| `tools/` | setup, the dataset builder, the git guard |
| `release/` | `update_helper.sh`: the frozen copy of the helper that the installed app uses |
| `spike/` | the experiments that tested the design's assumptions, with their results ([`spike/README.md`](spike/README.md)) |
| `docs/` | usage, design spec |

A notebook is a project folder (`<name>.ntproj`) of plain files: page images, `page.json` per page (lines,
words, boxes, timing, edits), recordings, history of earlier takes, undo steps.

## Privacy

Notebooks, recordings and transcripts are personal. They stay on the Mac: no cloud services, and the models
run locally. In this repository, `.gitignore` excludes scans, audio, images, projects and datasets, and
`tools/git-hooks/pre-commit` (installed by `tools/setup.sh`) refuses commits that contain them or long
Hebrew text.

## Credits

- Speech recognition: [ivrit.ai](https://www.ivrit.ai) Hebrew Whisper models. Hebrew language models and
  niqqud: [Dicta](https://dicta.org.il) (DictaBERT, Nakdan via dicta-onnx). Line detection: Kraken.
- App icon: <a href="https://www.flaticon.com/free-icons/journalism" title="journalism icons">Journalism icons created by Magnific - Flaticon</a>
