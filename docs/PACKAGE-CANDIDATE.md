# Published package candidate investigation

B1 remains open. Neither the reference `pipecat-ai==1.11.0` nor the latest published `1.12.0` completes normal Python Workers dependency resolution. Both require ONNX Runtime even though this application's inference is hosted. Allowing normal source builds still fails because the required ONNX Runtime versions have no matching `pyemscripten_2026_0_wasm32` artifact. No candidate replaced the application's vendored Pipecat, and no candidate was deployed.

The check ran on 5 October 2026 UTC. Discovery covered the official [PyPI project](https://pypi.org/project/pipecat-ai/1.12.0/), its package metadata and the [upstream manifest](https://github.com/pipecat-ai/pipecat/blob/v1.12.0/pyproject.toml). Those sources identified 1.12.0 as the latest release; no separately supported Workers/core artifact was identified there. This is a bounded investigation of published upstream candidates, not a claim that every third-party distribution was examined.

## Installation results

The [complete reproduction record](../audit/results/package-candidate-20261005-resolution.json) contains commands, exit codes, raw output, exact application dependency constraints, tool versions and artifact metadata. The toolchain was uv 0.12.18 and workers-py 1.17.4, targeting Python 3.14.2 with the Python 3.14 Pyodide index at `https://index.pyodide.org/314.0.7` and compatibility date `2026-09-24`. CPython checks used 3.14.7 on macOS arm64.

| Candidate and command | Observed result | Classification |
| --- | --- | --- |
| Both releases with the application's declared dependency pins, normal CPython resolution and `pywrangler sync` | Both reject `pydantic==2.12.5`; Pipecat requires `pydantic>=2.13` on Python 3.14 | Application dependency conflict; a future package integration needs a reviewed pin change |
| 1.11.0 alone, `pywrangler sync` | Required `loudness==0.2.0` has no usable target wheel | Workers packaging blocker |
| 1.12.0 alone, `pywrangler sync` | Required `sentencex==1.0.31` has no usable target wheel | Workers packaging blocker |
| Both releases alone, `pywrangler sync --allow-build` | Required `onnxruntime>=1.24.3,<1.25.dev0` has no matching target artifact | Workers packaging blocker persists with source builds allowed |
| 1.12.0 wheel, ordinary CPython installation with dependencies | Installs 50 distributions, including Pydantic 2.13.5, ONNX Runtime 1.24.4 and sentencex 1.0.31 | Local installation passes; Workers installation remains blocked |

The standalone projects intentionally omit application pins to distinguish the Pydantic conflict from the package's platform requirements. They retain every candidate dependency. No ignored dependencies, `--no-deps`, package edits, fake native modules or copied Pipecat source were used. The default resolver's `--no-build` comes from the pinned pywrangler; the separate source-build attempt uses its documented `--allow-build` option.

Cloudflare's [package guidance](https://developers.cloudflare.com/workers/languages/python/packages/) supports pure Python, PyEmscripten and included Pyodide packages. The evidence here is the actual target resolver's failure. Import-denial probes below do not establish that a dependency is unavailable on Workers.

The application was being implemented concurrently. The reproduction records its starting Git revision and the SHA-256 and exact dependencies of the application manifest read at the start. These results establish candidate packaging behavior, not acceptance of the final application revision. [Earlier exploratory resolver attempts](../audit/results/package-candidate-20261005-initial-resolution.json) and the [first source-build attempts](../audit/results/package-candidate-20261005-initial-source-build.json) remain separate.

## Installed components and source identity

The [CPython candidate probe](../audit/results/package-candidate-20261005-cpython.json) imports the selected STT, LLM and TTS interfaces, output transport, context/aggregator pair, hosted-analyzer base, explicit turn strategies and pipeline worker. Imports resolve to the isolated installed distribution. All 634 installed Pipecat Python source files match the downloaded 1.12.0 wheel byte for byte. The report records source hashes and all 50 resolved distributions.

| Published wheel | SHA-256 |
| --- | --- |
| `pipecat_ai-1.11.0-py3-none-any.whl` | `0126b81d453687573ddcc26aa29e2c9509e21d8a8bf9b8c266da9de68b6df70d` |
| `pipecat_ai-1.12.0-py3-none-any.whl` | `4cd3dc071b7b64da7ac700a26a224b773ae6ef8d6694a4566332d2e5e39ed6d9` |

Nine of ten restricted-import probes encounter eager audio or image imports. STT, TTS, aggregators, turn strategies and the worker first reach `audioop`; LLM, context and output first reach Pillow. The hosted analyzer's base interface imports without any denied dependency. These are local import observations with an explicit deny list; they are not a complete platform dependency inventory.

The unchanged Task 1 speech reference ran against normally installed 1.12.0. All ten scenarios reproduced their 1.11.0 expectations while `threading.Thread.start` was denied during execution, with zero thread-start attempts. The five completion/interruption cases preserve the observed sentence-level ordering, including the buffered-tail case. The five synthesis-failure cases reproduce the upstream default's retention of failed text, so application failure handling is still necessary. The fixture HTTP responses and output writes are simulated. STT/LLM/hosted-turn network calls, real audio devices, and both deployed transports are outside this probe.

Two earlier package concerns changed in 1.12.0: the [pipeline worker](https://github.com/pipecat-ai/pipecat/blob/v1.12.0/src/pipecat/pipeline/worker.py) no longer runs the earlier import-prewarming path, and sentencex replaces the NLTK sentence tokenizer. The new tokenizer introduces its own target-wheel requirement. These observations apply to 1.12.0; the preserved Task 1 evidence still describes 1.11.0.

## Reproduction

Use the repository's pinned development tools, a Python 3.14.7 interpreter and a fresh temporary directory. The reproducer creates its own environments, downloads and verifies the published wheels, retains every command result and runs the candidate against the unchanged Task 1 reference. It does not alter or deploy the application. A completed investigation exits 1 because it makes no release-acceptance claim.

```sh
python3 audit/package-candidate/reproduce.py \
  --repo "$PWD" \
  --work /absolute/path/to/new-package-investigation \
  --uv /absolute/path/to/uv \
  --pywrangler "$PWD/.venv/bin/pywrangler" \
  --host-python /absolute/path/to/python3.14
```

`--work` must not exist. The final files are `resolution.json` and, when normal CPython installation succeeds, `cpython-probe.json`. A Workers resolver success on a future artifact would only clear the installation gate; actual Workers execution must then be tested before accepting B1.

## External work still needed

A supported candidate must install the selected pipeline through normal resolution. It needs compatible target artifacts for the dependencies it actually uses, including a declared working sentencex path for 1.12.0. PC2 also requires removing mandatory unused local-inference dependencies and unrelated eager imports from this installation path; supplying more wheels alone does not settle that requirement. A future integration also needs compatible application dependency pins, verified import origins inside Workers, and runtime lifecycle tests. No model substitution, transport change or additional local inference is justified by this package failure.

PC1 fails for both evaluated releases on the selected Workers target. PC2 retains eager-import gaps. WK1 remains untested because normal packaging does not complete. Application implementation and live-provider verification can continue with the explicitly vendored prototype while B1 stays open.
