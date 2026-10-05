# Development and test guide

Run commands from the repository root unless a section says otherwise. The setup instructions reproduce the existing vendored prototype. The [implementation plan](IMPLEMENTATION-PLAN.md) defines the work needed for the supported configuration: Workers AI STT, hosted Smart Turn, GPT-OSS-120B, and Aura-2 through both transports. Its first task verifies the component interfaces; task 3 implements the selected LLM connection and standard assistant context.

## Set up the prototype

The recorded setup used Node.js 22, uv 0.12.18, and local Python 3.14.7. Lockfiles pin Wrangler 4.139.0, workers-py 1.17.4, and workers-runtime-sdk 1.9.0. Recorded deployment evidence reports Python 3.14.2 / Pyodide 314.0.6. Record the actual runtime when repeating a test.

```sh
npm ci
uv sync --locked --python 3.14.7
uv run pywrangler sync
```

The repository contains selected Pipecat 1.11.0 source. Installing the full package over it would make it unclear which code is running. Use an isolated candidate environment for the supported-package investigation below.

For a new deployment, authenticate and enter the demo key at the private secret prompt. Session creation requires this key.

```sh
npx wrangler login
npx wrangler secret put DEMO_ACCESS_KEY
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

The SFU secret is different from the browser demo key. Workers AI uses the `AI` binding; these provider calls do not use separate provider keys. Keep credentials in Wrangler secrets or an ignored local `.dev.vars` file. Leave fixture routes disabled in production. Review the Worker name and account before deploying; these commands create or update a real service.

Open `/websocket` or `/webrtc`, provide the demo key, select Start conversation, and allow microphone access. Use headphones for the first check. The appointment example returns fixed fictional availability. Mute, End, and Resume audio are available on the call page.

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
uv run pywrangler dev --local --var ENABLE_TEST_ROUTES:true --var ALLOW_UNAUTHENTICATED_LOCAL:true
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

The strict audit requires a separate, clean checkout of baseline commit `6c17c0805f13f7609ba0a93ea8bf4c945797de18`. This handoff contains changed documentation and intentionally fails that exact-tree check. Reports must be written outside the clean checkout.

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

## Test actual speech providers

The existing live scripts exercise the baseline Flux, Llama, and Aura path through direct WebSocket only. Use the browser SFU procedure below for the other route. They do not establish hosted Smart Turn or GPT-OSS integration. Run the selected-model checks in [AI2](ACCEPTANCE.md#ai2-stream-gpt-oss-answers-into-speech) after implementing that provider connection. Use a deployment you own, record its exact source revision, and keep new evidence in a fresh directory.

Inputs are headerless PCM16 little-endian, mono, 16 kHz, between 0.2 and 15 seconds. Container files such as WAV are rejected. Prepare two distinct short questions, a question about appointment availability, and an utterance with an internal one-second pause. Use `prerecorded-human` for a human recording or `prerecorded-tts` for generated speech.

Enter the demo key privately. Captures contain transcripts, provider error details, and output PCM. `--capture-root` must be outside `outputs/`; retain only sanitized evidence in a shared handoff.

```sh
printf 'Demo access key: '
read -r -s DEMO_ACCESS_KEY
printf '\n'
export DEMO_ACCESS_KEY
export VOICE_BASE="https://your-worker.example"
export VOICE_CHECK_WORK="$(mktemp -d "${TMPDIR:-/tmp}/pipecat-voice-check.XXXXXX")"
export VOICE_PCM_DIR="/absolute/path/to/your/recordings"

node scripts/smoke_real_voice.mjs --base "$VOICE_BASE" --pcm "$VOICE_PCM_DIR/normal.pcm" --speech-source prerecorded-tts --capture-root "$VOICE_CHECK_WORK" --evidence "$VOICE_CHECK_WORK/smoke.json"

node scripts/smoke_real_voice.mjs --base "$VOICE_BASE" --pcm "$VOICE_PCM_DIR/pause-1s.pcm" --speech-source prerecorded-tts --expect-single-user-turn --capture-root "$VOICE_CHECK_WORK" --evidence "$VOICE_CHECK_WORK/pause-1s.json"

node scripts/smoke_real_voice.mjs --base "$VOICE_BASE" --pcm "$VOICE_PCM_DIR/tool.pcm" --speech-source prerecorded-tts --capture-root "$VOICE_CHECK_WORK" --evidence "$VOICE_CHECK_WORK/tool.json"

node scripts/check_real_pending.mjs --base "$VOICE_BASE" --pcm "$VOICE_PCM_DIR/normal.pcm" --tool-pcm "$VOICE_PCM_DIR/tool.pcm" --case all --capture-root "$VOICE_CHECK_WORK" --evidence "$VOICE_CHECK_WORK/pending.json"

node scripts/check_real_voice.mjs --base "$VOICE_BASE" --pcm "$VOICE_PCM_DIR/normal.pcm" --pcm-second "$VOICE_PCM_DIR/second.pcm" --duration-ms 600000 --sessions 2 --capture-root "$VOICE_CHECK_WORK" --evidence "$VOICE_CHECK_WORK/duration.json"

node scripts/check_real_idle.mjs "$VOICE_BASE" "$VOICE_CHECK_WORK/idle.json" "$VOICE_CHECK_WORK/idle-errors.private.json"
unset DEMO_ACCESS_KEY
```

The ten-minute, two-session command reproduces an earlier workload. It is not an approved release target. The smoke, pending, and duration scripts accept `--validate-input` to check input files without network requests. A pending-work retry may select `--case tool` or `--case thinking`; use a new evidence filename and retain earlier failures. A startup HTTP 429 means cancellation was never exercised in that attempt.

| Script | What to inspect | Measurement limit |
| --- | --- | --- |
| Smoke | Final transcript, assistant text, nonzero response audio, chunk receipts, and cleanup. | A generic pass can hide an utterance split by the recognizer. |
| Pause | Exactly one final user turn, with no assistant response before paced input finishes. | Covers that recording only. |
| Pending work | Interrupt during model/tool work before audio; reject the canceled response and complete a replacement. | Explicit control, not acoustic interruption or proof remote billing stopped. |
| Duration | Repeated responses, interruptions, reconnect, restored context, and End. | Timed receipts emulate playback; distinct questions alone do not prove isolation. |
| Idle | No client traffic, expected expiry, and released resources. | Provider heartbeat traffic is separate from client liveness. |

After End, inspect Pipecat/provider tasks, sockets, readers, pending requests, queued provider bytes, unacknowledged audio, playback chunks, pending turn tasks, and pending user fragments/characters. Local zero counts must not conceal unknown remote allocations. Provider recovery in the baseline drops uncommitted speech and closes the interrupted turn; repeat the lost utterance when testing it.

## Test a real browser and physical audio

Use `/transport-check.html` for a recorded-file SFU check and the normal call pages for microphone testing. Record the browser, device, network, deployment, selected models, and whether headphones or speakers were used. Session details help distinguish microphone capture, transcript arrival, provider progress, and received audio.

For detailed owned-session diagnostics, use `GET /api/session/{id}/diagnostics?token={capability}`. The session-creation response supplies that capability. Keep the URL private; it is a credential. The production configuration redacts query strings from logs. Export only the allowlisted diagnostics intended for sharing.

Follow the scenarios in [Acceptance](ACCEPTANCE.md): ordinary speech, a mid-thought pause, a follow-up, interruption, failure, reconnect, and End on both routes. Agree sample counts and thresholds before scoring the run. Use an aligned loopback or acoustic recording to measure speech onset and the last stale audible sample. Browser scheduling, nonzero PCM, RTP counters, and server `clear` timing cannot substitute for that measurement.

Controlled Durable Object restart tests belong in an isolated fixture deployment. They deliberately abort a running object and can yield a failed request before recovery. Restoring saved context into a fresh pipeline is the recovery being tested; it is not seamless continuation of live tasks or buffered speech.

If a required platform metric, such as whole-isolate memory or CPU accounting, is unavailable, report it as unavailable. A narrower heap measurement is not an equivalent substitute. Record model identifiers and version details where exposed; a model name alone does not identify immutable weights.
