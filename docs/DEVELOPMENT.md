# Development and test guide

Run commands from the repository root unless a section says otherwise. The setup instructions reproduce the existing vendored prototype. The [implementation plan](IMPLEMENTATION-PLAN.md) defines the work needed for the supported configuration: Workers AI STT, hosted Smart Turn, GPT-OSS-120B, and Aura-2 through both transports. Task 1 verified the reference interfaces; Task 2 implements the Nova/Smart Turn candidate; Task 3 implements the selected LLM connection and standard assistant context.

## Set up the prototype

The recorded setup used Node.js 22, uv 0.12.18, and local Python 3.14.7. Lockfiles pin Wrangler 4.139.0, workers-py 1.17.4, and workers-runtime-sdk 1.9.0. Recorded deployment evidence reports Python 3.14.2 / Pyodide 314.0.6. Record the actual runtime when repeating a test. The [Task 2 deployment follow-up](EVIDENCE.md#task-2-deployment-follow-up) uses these locked tools and records the current service version, startup/access checks and remaining voice-test limits.

```sh
npm ci
uv sync --locked --python 3.14.7
uv run pywrangler sync
```

The repository contains selected Pipecat 1.11.0 source. Installing the full package over it would make it unclear which code is running. Use an isolated candidate environment for the supported-package investigation below.

Authenticate with Cloudflare before deploying. Both voice examples allow session creation without a shared demo key.

```sh
npx wrangler login
```

For the SFU route, create a Realtime SFU app and enter its app ID and secret:

```sh
npx wrangler secret put REALTIME_SFU_APP_ID
npx wrangler secret put REALTIME_SFU_APP_SECRET
```

Deploy after configuring the secrets for your selected route:

```sh
uv run pywrangler deploy
```

SFU credentials remain private Worker secrets. Workers AI uses the `AI` binding; these provider calls do not use separate provider keys. Keep credentials in Wrangler secrets or an ignored local `.dev.vars` file. Leave fixture routes disabled in production. Review the Worker name and account before deploying; these commands create or update a real service.

Open `/websocket` or `/webrtc`, select Start conversation, and allow microphone access. Use headphones for the first check. The appointment example returns fixed fictional availability. Mute, End, and Resume audio are available on the call page.

## Run offline checks

```sh
node --test public/*.test.mjs scripts/*.test.mjs
uv run python -m unittest discover -s tests -v
uv run python scripts/check_pipecat_core.py
uv run python scripts/check_providers.py
uv run python scripts/check_entry_lifecycle.py
uv run python scripts/check_access_auth.py
uv run python scripts/check_sfu_entry.py
uv run python -m unittest discover -s acceptance -p 'test_runner.py' -v
uv run python -m unittest discover -s audit/suite -p 'test_*.py' -v
```

Existing regressions preserve some known limitations, including missing SFU assistant history. A passing regression suite must be read alongside the [acceptance plan](ACCEPTANCE.md).

## Run local Worker fixtures

Start the local Worker and Durable Object runtime:

```sh
uv run pywrangler dev --local --var ENABLE_TEST_ROUTES:true
```

In another terminal, use the port printed by Wrangler and a new result path:

```sh
node scripts/check_worker.mjs http://127.0.0.1:8787 /absolute/path/to/new-lifecycle.json
```

The normal browser still selects real providers; only the test harness selects fixture routes. For local real-model development, authenticate to Cloudflare and set `"remote": true` on the `ai` binding before starting development. SFU callbacks need a publicly reachable WSS address, so a plain local server does not exercise the full SFU route.

`scripts/probe_worker.mjs` writes directly to `evidence/workerd-probe.json`. Run it only in a disposable checkout if preserving the included evidence. `scripts/check_worker.mjs` and `scripts/soak_worker.mjs` also default to historical evidence paths; supply new output paths when using them.

## Inspect the conversation diagnostics

```sh
uv run python acceptance/run.py --output /absolute/path/to/new-conversation-diagnostics.json
```

This runner uses real application Pipecat queues with fixed-text providers, silent audio, and transport doubles. It needs no credentials or network access. Each experiment runs in a new process with a 35-second deadline. Child processes remove inherited Python optimization settings because the fixtures use assertions. The report records source hashes, loaded module locations, interpreter and dependency versions, and the runner hash.

| Diagnostic | What it checks |
| --- | --- |
| `B3.generated-context`, both routes | Whether a completed fixture answer reaches the next model input and saved history without browser receipts. The expected answer comes independently from the fixture provider. |
| `B3.receipt-control`, direct WebSocket | Whether simulated chunk receipts allow the current custom history policy to retain the answer. |
| `B4.pending-generation`, both routes | Whether a pending model response is canceled and rejected while a replacement response proceeds. |
| `B4.cleanup-order`, SFU | Whether the next model call waits when the old transport's cleanup is deliberately held. |

These are baseline diagnostics. The generated-context check exposes receipt-dependent history; it does not specify how much interrupted speech the selected Pipecat pipeline should retain. New context acceptance must use the reference configuration and equivalent TTS/output events. A test should not send raw model tokens directly to an aggregator and claim voice-pipeline parity.

A fixture's clean shutdown shows that the test released local resources. It does not establish remote SFU cleanup. The held-cleanup observation describes application ordering, not audible stop time or an approved latency threshold.

## Reproduce the package/runtime audit

The strict audit requires a separate, clean checkout of baseline commit `6c17c0805f13f7609ba0a93ea8bf4c945797de18`. This handoff contains Task 2 application changes and intentionally fails that exact-tree check. Reports must be written outside the clean checkout.

The audit runner itself uses the standard library. Its core subprocess needs the small dependency set used by the investigated configuration. The original local experiment used CPython 3.12.14, while the application declares Python 3.14. A local pass in that environment does not establish Workers compatibility.

Create a separate environment if needed:

```sh
python3.12 -m venv .audit-venv
.audit-venv/bin/python -m pip install -r audit/requirements-audit.txt
```

Obtain the published [Pipecat 1.11.0 source archive](https://files.pythonhosted.org/packages/source/p/pipecat-ai/pipecat_ai-1.11.0.tar.gz). Its expected SHA-256 is `49e7532a1f035e8884c47448a06d998a1b7f0943d11eae9bc8600a9e749a2a04`. The runner does not download packages or silently substitute another release.

```sh
.audit-venv/bin/python -B audit/suite/audit.py \
  --repo /absolute/path/to/clean-baseline-checkout \
  --archive /absolute/path/to/pipecat_ai-1.11.0.tar.gz \
  --python /absolute/path/to/.audit-venv/bin/python \
  --existing-tests \
  --output /absolute/path/to/new-package-audit.json

.audit-venv/bin/python -B audit/acceptance/sfu_gaps.py \
  --repo /absolute/path/to/clean-baseline-checkout \
  --output /absolute/path/to/new-sfu-diagnostics.json

.audit-venv/bin/python -B audit/benchmarks/media_costs.py \
  --repo /absolute/path/to/clean-baseline-checkout \
  --seconds 10 --output /absolute/path/to/new-local-media-costs.json
```

The source archive is optional. Omitting it leaves upstream metadata and restored-import experiments untested. The main runner leaves the baseline checkout unchanged and uses temporary copies for controlled edits. It blocks selected optional imports and `threading.Thread.start`; this guard does not cover every native extension or every possible thread API. It also verifies that assertions remain enabled.

An experiment that restores upstream behavior is an ablation: change one workaround, hold the other conditions fixed, and observe the result. An unrelated failure or unexpected success is a test error, not evidence of the expected capability gap.

The PCM benchmark measures local conversion work. It cannot predict deployed CPU, memory, throughput, or cost.

## Check a supported package candidate

Install the candidate in its own environment and add `--candidate-python /absolute/path/to/candidate-venv/bin/python` to the audit command. If another distribution owns the `pipecat` module, identify it with `--candidate-dist`.

The candidate process uses isolated Python, rejects editable installs, checks which distribution owns the imported module, and removes the baseline guard's injected vendored-source path. It does not patch the candidate to obtain a pass. An incompatible test API is a test error requiring review. For example, a candidate that removes prewarming entirely might lack the baseline's `enable_import_prewarm` parameter without needing thread support.

Distribution ownership alone does not prove unchanged installation. Verify the release artifact and installed-file hashes as well. Extend the exercised components to the chosen hosted-turn, TTS, output, and aggregator configuration: the existing guard covers a smaller core. B1 also requires normal installation and execution on actual Workers. [Capability request](CAPABILITY-REQUEST.md)

## Interpret results

| Status | Meaning |
| --- | --- |
| `scoped_pass` or `supported_in_scope` | The named behavior passed within that test's environment and limits. |
| `observed_gap` | A valid experiment observed the expected missing behavior. |
| `untested` | The runner collected no suitable evidence for that requirement. |
| `test_error` | Setup, prerequisites, timing, cleanup, or another harness problem prevented a reliable conclusion. |

The two main runners always report `readiness: not_established`. Exit 1 means gaps or untested requirements remain; exit 2 means a test error needs investigation. Keep the actual exit code when wrapping a command. The separate `sfu_gaps.py` exits 0 when it reproduces both known gaps, while its report still says capability acceptance has not passed.

Recorded JSON is immutable evidence of its original run. The current runners' outstanding-check lists have been narrowed to the agreed scope; earlier reports retain retired entries and original counts. The executable probes themselves are unchanged by that checklist update. Exact earlier runner copies are in the pre-consolidation archive. Use [Evidence](EVIDENCE.md) to interpret historical results and [Acceptance](ACCEPTANCE.md) for release decisions.

## Repeat Task 1 reference probes

These probes are independent of the production pipeline. The recorded environment was CPython 3.12.14 on macOS arm64 with a normal installation of the published Pipecat 1.11.0 source archive. Full installation and execution on Workers remain untested. Use a fresh environment and result directory; every probe refuses to overwrite its output.

```sh
export TASK1_WORK="$(mktemp -d "${TMPDIR:-/tmp}/pipecat-reference.XXXXXX")"
export TASK1_ARCHIVE="$TASK1_WORK/pipecat_ai-1.11.0.tar.gz"
curl -fL https://files.pythonhosted.org/packages/source/p/pipecat-ai/pipecat_ai-1.11.0.tar.gz -o "$TASK1_ARCHIVE"
shasum -a 256 "$TASK1_ARCHIVE"
# Expected: 49e7532a1f035e8884c47448a06d998a1b7f0943d11eae9bc8600a9e749a2a04
python3.12 -m venv "$TASK1_WORK/venv"
"$TASK1_WORK/venv/bin/python" -m pip install "$TASK1_ARCHIVE"
export NLTK_DATA="$TASK1_WORK/nltk_data"
"$TASK1_WORK/venv/bin/python" -m nltk.downloader -d "$NLTK_DATA" punkt_tab

"$TASK1_WORK/venv/bin/python" -I -B audit/reference/speech_reference.py --archive "$TASK1_ARCHIVE" --output "$TASK1_WORK/speech.json"
"$TASK1_WORK/venv/bin/python" -I -B audit/reference/turn_reference.py --output "$TASK1_WORK/turn.json"
"$TASK1_WORK/venv/bin/python" -I -B audit/reference/runtime_probe.py --archive "$TASK1_ARCHIVE" --output "$TASK1_WORK/runtime.json"
"$TASK1_WORK/venv/bin/python" -I -B audit/reference/runtime_constraints.py --archive "$TASK1_ARCHIVE" --app-pyproject pyproject.toml --target-python 3.14.7 --output "$TASK1_WORK/constraints.json"
python3 -B audit/reference/gpt_probe.py --output "$TASK1_WORK/gpt-fixtures.json"
```

The tokenizer resource is a normal runtime requirement of the chosen sentence aggregator. Download it deliberately during setup and retain its hashes; do not let a silent first-use download count as supported Workers startup. No provider SDK extra was needed for the HTTP TTS reference. Archive and loaded-source verification prevent copied or patched Pipecat from silently satisfying these tests. The turn probe rejects the repository's vendored source; run it in the same verified environment. Do not disable Python assertions.

| Probe | Result to inspect | Exit meaning |
| --- | --- | --- |
| `speech_reference.py` | Ten event traces and exact assistant messages; HTTP/output leaves are simulated; full upstream startup uses normal local threads | 0 means expected reference observations, including failure-policy gaps, were reproduced; 2 means a test error |
| `turn_reference.py` | Six controller-level cases, including two positive coordination cases and four integration gaps | 0 means recorded behavior reproduced, not that an async Workers adapter passes; 2 means test error |
| `runtime_probe.py` | Normal imports, restricted-import controls, actual thread attempts and HTTP analyzer error | 1 means gaps/untested Workers readiness remain; 2 means test error |
| `runtime_constraints.py` | Normal resolver dry run of the exact Pydantic declarations active for Python 3.14.7 | Resolves only that dependency projection using the local interpreter; 1 means reproduced conflict, 2 means test error |
| `gpt_probe.py` | Eleven baseline parser/cancellation fixtures and the actual baseline request | 0 means expected observations reproduced, including known parser gaps; no live/provider success claim |

`gpt_probe.py` supplies explicit JavaScript/FFI stand-ins solely to exercise the unchanged application parser on CPython. It does not import Pipecat or establish a supported package/runtime. The speech and runtime probes do not inject dependency modules or edit installed source.

The [interface record](../audit/results/task1-gpt-interface.json) records the failed authentication check. After authorized test access is restored, the isolated [Python binding recipe](../audit/reference/gpt_binding.py) can make fixed-prompt calls without changing production routes:

```sh
# Use the repository's locked development tools from “Set up the prototype”.
uv run pywrangler dev --config audit/reference/gpt.wrangler.jsonc --local
# In another terminal, use the actual local port and fresh result files.
curl -X POST http://127.0.0.1:8787/normal -o "$TASK1_WORK/gpt-live-normal.json"
curl -X POST http://127.0.0.1:8787/one-token -o "$TASK1_WORK/gpt-live-limit.json"
curl -X POST http://127.0.0.1:8787/invalid-budget -o "$TASK1_WORK/gpt-live-error.json"
curl -X POST http://127.0.0.1:8787/preabort -o "$TASK1_WORK/gpt-live-preabort.json"
curl -X POST http://127.0.0.1:8787/cancel-after-first-event -o "$TASK1_WORK/gpt-live-cancel.json"
```

The recipe is syntax-checked only. Its Python imports, startup, remote AI binding and cancellation are untested. It has a 45-second request/read budget and two-second reader cleanup budget, records event shapes and synthetic answer text, and counts reasoning characters without saving reasoning. It assumes one JSON `data:` payload per SSE line. An unexpected framing or return type is a harness question to resolve before interpreting the model result. Record deployment/runtime/tool identities and source hashes with each capture; the recipe's local JSON alone is not a complete acceptance report. Do not deploy it or combine it with production routes.

For Nova/Smart Turn, use the Task 2 checks below. The original [request candidates](../audit/reference/turn-sources.json) remain preserved. The expired authentication result does not establish a provider or runtime incompatibility.

## Verify the Task 2 turn connection

Run the focused integration fixtures and binding-adapter checks after setup:

```sh
uv run python -m unittest tests.test_turn_coordination tests.test_smart_turn -v
uv run python scripts/check_providers.py
```

The turn fixtures run Nova-shaped events and controlled Smart Turn decisions through the application's Pipecat queues and standard user aggregator. They exercise pending decisions, speech resumption, final transcript coverage, duplicate events, timeouts and cleanup. The provider checks use explicit JavaScript/FFI doubles on local Python; those checks do not establish binding acceptance or Python Workers support. Record the actual test summary before treating the candidate as passed. The two-second binding wait and five-second pause-readiness deadline are application settings; fixtures may shorten waits to test the same failure path.

The [Task 2 access check](../audit/results/task2-live-access.json) made no model requests: existing authentication remained expired. It used cached Wrangler 4.119.0 only to inspect authentication, not as the selected runtime. For live repetition, use the locked tools above and existing authorized access. Start the [isolated binding probe](../audit/reference/task2_turn_binding.py):

```sh
uv run pywrangler dev --config audit/reference/task2_turn.wrangler.jsonc --local --ip 127.0.0.1
```

In a second terminal, prepare an authorized synthetic speech recording as headerless PCM16 little-endian, mono, 16 kHz, with at most eight seconds of audio. Record its expected words and known speech/pause offsets. Create fresh request and result files:

```sh
export TASK2_WORK="$(mktemp -d "${TMPDIR:-/tmp}/pipecat-turn-check.XXXXXX")"
export TASK2_PCM="/absolute/path/to/synthetic-speech.pcm"
python3 - <<'PY_PROBE'
import base64, json, os, pathlib
pcm = pathlib.Path(os.environ["TASK2_PCM"]).read_bytes()
assert 0 < len(pcm) <= 8 * 16000 * 2 and len(pcm) % 2 == 0
request = {"pcm16_base64": base64.b64encode(pcm).decode("ascii"),
           "fixture_label": "authorized synthetic speech"}
(pathlib.Path(os.environ["TASK2_WORK"]) / "request.json").write_text(json.dumps(request))
PY_PROBE
# Use the actual local port printed by the dev server.
curl --fail-with-body -H 'Content-Type: application/json' --data-binary "@$TASK2_WORK/request.json" http://127.0.0.1:8787/nova -o "$TASK2_WORK/nova-live.json"
curl --fail-with-body -H 'Content-Type: application/json' --data-binary "@$TASK2_WORK/request.json" http://127.0.0.1:8787/smart-turn -o "$TASK2_WORK/smart-turn-live.json"
```

The Nova probe sends 20 ms packets, adds one second of generated silence and sends `Finalize`. It records up to 128 selected events with provider timestamps, transcript text and the sent-audio cursor. Its three-second `Finalize` wait is bounded, and an absent acknowledgement does not mean transcript readiness: the provider does not guarantee that acknowledgement. The Smart Turn probe submits normalized float32 base64 for the supplied audio. These probe waits differ from the application limits and must not be presented as application latency measurements. The probe's synthetic silence suffix is for observing Nova endpoint events; the application does not append that suffix to a detector snapshot.

Repeat with completed speech, a mid-thought pause and speech resuming after a pause. Compare provider ranges to known fixture offsets. The application takes detector audio through the Nova transcript-range cursor, not the latest buffer tail; verify that this conservative slice works for the hosted model. Final transcript coverage and onset/word timing alignment also need real captures. [Primary source contracts and open questions](../audit/reference/task2_turn_sources.json)

Only syntax and pure event-sanitizer checks have run for this isolated probe. Its imports, startup, model requests and cancellation remain untested. Record the exact application, runtime and tool versions with new results. Do not deploy the probe or merge it into production routes. A successful request capture would still leave end-to-end Pipecat execution, both real transports, physical audio and workload acceptance to verify.

## Test actual speech providers

Historical live-script results exercised Flux, Llama and Aura through direct WebSocket. The current source selects Nova/Smart Turn while retaining Llama/Aura, so a fresh deployment and new captures are required. Use the browser SFU procedure below for the other route. The scripts alone do not establish GPT-OSS or the standard assistant speech/output/context integration; those remain Task 3 checks under [AI2](ACCEPTANCE.md#ai2-stream-gpt-oss-answers-into-speech). Use a deployment you own, record its exact source revision, and keep new evidence in a fresh directory.

Inputs are headerless PCM16 little-endian, mono, 16 kHz, between 0.2 and 15 seconds. Container files such as WAV are rejected. Prepare two distinct short questions, a question about appointment availability, and an utterance with an internal one-second pause. Use `prerecorded-human` for a human recording or `prerecorded-tts` for generated speech.

The current deployment requires no demo key. Captures contain transcripts, provider error details, and output PCM. `--capture-root` must be outside `outputs/`; retain only sanitized evidence in a shared handoff.

```sh
export VOICE_BASE="https://your-worker.example"
export VOICE_CHECK_WORK="$(mktemp -d "${TMPDIR:-/tmp}/pipecat-voice-check.XXXXXX")"
export VOICE_PCM_DIR="/absolute/path/to/your/recordings"

node scripts/smoke_real_voice.mjs --base "$VOICE_BASE" --pcm "$VOICE_PCM_DIR/normal.pcm" --speech-source prerecorded-tts --capture-root "$VOICE_CHECK_WORK" --evidence "$VOICE_CHECK_WORK/smoke.json"

node scripts/smoke_real_voice.mjs --base "$VOICE_BASE" --pcm "$VOICE_PCM_DIR/pause-1s.pcm" --speech-source prerecorded-tts --expect-single-user-turn --capture-root "$VOICE_CHECK_WORK" --evidence "$VOICE_CHECK_WORK/pause-1s.json"

node scripts/smoke_real_voice.mjs --base "$VOICE_BASE" --pcm "$VOICE_PCM_DIR/tool.pcm" --speech-source prerecorded-tts --capture-root "$VOICE_CHECK_WORK" --evidence "$VOICE_CHECK_WORK/tool.json"

node scripts/check_real_pending.mjs --base "$VOICE_BASE" --pcm "$VOICE_PCM_DIR/normal.pcm" --tool-pcm "$VOICE_PCM_DIR/tool.pcm" --case all --capture-root "$VOICE_CHECK_WORK" --evidence "$VOICE_CHECK_WORK/pending.json"

node scripts/check_real_voice.mjs --base "$VOICE_BASE" --pcm "$VOICE_PCM_DIR/normal.pcm" --pcm-second "$VOICE_PCM_DIR/second.pcm" --duration-ms 600000 --sessions 2 --capture-root "$VOICE_CHECK_WORK" --evidence "$VOICE_CHECK_WORK/duration.json"

node scripts/check_real_idle.mjs "$VOICE_BASE" "$VOICE_CHECK_WORK/idle.json" "$VOICE_CHECK_WORK/idle-errors.private.json"
```

The ten-minute, two-session command reproduces an earlier workload. It is not an approved release target. The smoke, pending, and duration scripts accept `--validate-input` to check input files without network requests. A pending-work retry may select `--case tool` or `--case thinking`; use a new evidence filename and retain earlier failures. A startup HTTP 429 means cancellation was never exercised in that attempt.

| Script | What to inspect | Measurement limit |
| --- | --- | --- |
| Smoke | Final transcript, assistant text, nonzero response audio, chunk receipts, and cleanup. | A generic pass can hide an utterance split by the recognizer. |
| Pause | Exactly one final user turn, with no assistant response before paced input finishes. | Covers that recording only. |
| Pending work | Interrupt during model/tool work before audio; reject the canceled response and complete a replacement. | Explicit control, not acoustic interruption or proof remote billing stopped. |
| Duration | Repeated responses, interruptions, reconnect, restored context, and End. | Timed receipts emulate playback; distinct questions alone do not prove isolation. |
| Idle | No client traffic, expected expiry, and released resources. | Provider heartbeat traffic is separate from client liveness. |

After End, inspect Pipecat/provider tasks, sockets, readers, pending requests, queued provider bytes, unacknowledged audio, playback chunks, pending turn tasks, and pending user fragments/characters. Local zero counts must not conceal unknown remote allocations. Provider recovery drops uncommitted speech, invalidates pending detector work and resets the Nova audio clock; repeat the lost utterance when testing it.

## Test a real browser and physical audio

Use `/transport-check.html` for a recorded-file SFU check and the normal call pages for microphone testing. Record the browser, device, network, deployment, selected models, and whether headphones or speakers were used. Session details help distinguish microphone capture, transcript arrival, provider progress, and received audio.

For detailed owned-session diagnostics, use `GET /api/session/{id}/diagnostics?token={capability}`. The session-creation response supplies that capability. Keep the URL private; it is a credential. The production configuration redacts query strings from logs. Export only the allowlisted diagnostics intended for sharing.

Follow the scenarios in [Acceptance](ACCEPTANCE.md): ordinary speech, a mid-thought pause, a follow-up, interruption, failure, reconnect, and End on both routes. Agree sample counts and thresholds before scoring the run. Use an aligned loopback or acoustic recording to measure speech onset and the last stale audible sample. Browser scheduling, nonzero PCM, RTP counters, and server `clear` timing cannot substitute for that measurement.

Controlled Durable Object restart tests belong in an isolated fixture deployment. They deliberately abort a running object and can yield a failed request before recovery. Restoring saved context into a fresh pipeline is the recovery being tested; it is not seamless continuation of live tasks or buffered speech.

If a required platform metric, such as whole-isolate memory or CPU accounting, is unavailable, report it as unavailable. A narrower heap measurement is not an equivalent substitute. Record model identifiers and version details where exposed; a model name alone does not identify immutable weights.
