# Vendored speech components

The prototype now includes the standard Pipecat 1.11.0 TTS service, output transport and assistant aggregation dependencies. B1 remains open: these files execute from `src/pipecat`, with declared local compatibility edits. They are not a supported installation of `pipecat-ai`. The [published package investigation](PACKAGE-CANDIDATE.md) records the separate normal-resolution blockers.

The upstream source remains commit `3dede06bec0b497bddfdcf047af7495ee9d0726e` from the checksum-pinned 1.11.0 source archive. The [vendor manifest](../src/pipecat/VENDOR_MANIFEST.json) records 143 upstream Python files, nine modified upstream files and one local English Punkt loader. Every upstream file has its original source hash; modified files also have their resulting hash. [The compatibility patch](../pipecat-compat.patch) contains every upstream source edit as a zero-context diff (apply with `git apply --unidiff-zero`). The generated loader is identified separately and does not claim upstream provenance.

## Compatibility changes

The earlier version, import, RTVI and import-prewarming changes remain in place. The speech path adds these changes:

- TTS and output instantiate the native resampler only if incoming and selected sample rates differ. The selected Aura PCM output and Pipecat output both use 24 kHz. Resampling, its filter tail and resets retain their upstream path when a resampler exists; native resampling is outside this prototype's supported configuration.
- Output silence detection reads signed PCM16 little-endian samples with the standard library. The threshold is unchanged. Two edge cases are explicit: empty audio is silent, and the full-scale negative sample `-32768` is non-silent. The NumPy expression in the reference overflows when taking the absolute value of that sample.
- Output imports Pillow only when resizing an image. Video remains outside the selected voice configuration. Audio DTMF raises `NotImplementedError` because its tone assets and native/file helpers are omitted; no unresolved `load_dtmf_audio` reference remains.
- Sentence splitting uses normally installed `nltk==3.9.2` with the Task 1 English Punkt tables bundled in a generated Python module. This avoids corpus lookup and download during a Worker request. The sentence tokenizer algorithm remains NLTK's `PunktSentenceTokenizer`.

`websockets==15.0.1` satisfies the upstream service import path. The application's actual network transport still uses its Workers adapters. Both new direct dependencies and their transitive dependencies were resolved normally. `uv.lock` records the host environment; `pylock.toml` records the Workers artifacts. Their regex versions differ because the Workers lock selects the Pyodide artifact. There are no fake dependency modules, ignored requirements, or locally edited installed packages in this change. The vendored Pipecat source itself remains the explicit packaging workaround.

## English Punkt data

The four embedded tables match Task 1 byte for byte. Their hashes and the loader template hash are in the vendor manifest. The original [NLTK data archive](https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/packages/tokenizers/punkt_tab.zip) has SHA-256 `e57f64187974277726a3417ca6f181ec5403676c717672eef6a748a7b20e0106`. Its unchanged [README and attribution](../src/pipecat/PUNKT-README.txt) are retained. The upstream package metadata credits Jan Strunk; the README describes the English training source and credits Jan Strunk and Tibor Kiss.

The [loader template](../scripts/english_punkt.py.in) calls the normal NLTK decoders on those unmodified table strings. No NLTK package code is copied or replaced.

## Reproduction and checks

Use the pinned archives from the reference work directory, or let the vendor script download and checksum-verify them. Regenerate into a temporary directory before replacing a working checkout:

```sh
.venv/bin/python scripts/vendor_pipecat.py \
  --archive /absolute/path/to/pipecat_ai-1.11.0.tar.gz \
  --punkt-archive /absolute/path/to/punkt_tab.zip \
  --destination /tmp/pipecat-regenerated

.venv/bin/python scripts/check_speech_vendor.py \
  --archive /absolute/path/to/pipecat_ai-1.11.0.tar.gz \
  --punkt-archive /absolute/path/to/punkt_tab.zip

.venv/bin/python scripts/check_pipecat_core.py
```

On 5 October 2026 UTC, Python 3.14.7 reproduced all 144 files and the manifest byte for byte. The focused check verified the declared import origins, normal NLTK/websockets versions, 378 sentence-prefix comparisons against NLTK's file-loaded reference tokenizer, PCM silence boundaries and the explicit DTMF error. Thread creation and corpus downloads were guarded; neither occurred. NumPy, Pillow, audioop, loudness, soxr and ONNX Runtime were not loaded. The existing core runtime check also passed with zero remaining Pipecat tasks after cancellation.

These checks establish local source identity and the stated compatibility behavior. They do not establish Workers execution, provider synthesis, device playback or release acceptance. Final application verification must use the committed vendor revision and the exact deployed application revision.
