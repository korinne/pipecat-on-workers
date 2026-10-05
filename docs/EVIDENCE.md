# Evidence and remaining unknowns

## Diagnostic update, 5 October 2026

The diagnostic-only revision `39fe5b0` is now deployed as `425815d1-a965-47bc-b863-4dad470ad0a1`. The export retains all seven abort reasons and bounded SFU/Nova/provider timing. Independent checks and one direct synthetic provider call pass; the failed laptop call still lacks its actual trace. Conversation behavior and the unapproved silence fallback are unchanged. See [causal diagnostics and next capture](CAUSAL-DIAGNOSTICS.md). The earlier deployment and findings below are preserved as history.


The application baseline is [6c17c0805f13f7609ba0a93ea8bf4c945797de18](https://github.com/korinne/pipecat-on-workers/tree/6c17c0805f13f7609ba0a93ea8bf4c945797de18). This handoff changes documentation and diagnostic scope, not the voice application. Recorded evidence retains its original source, environment, and test expectations.

The release target is one Pipecat voice configuration using Workers AI for hosted Smart Turn, STT, GPT-OSS-120B, and Aura-2 through direct WebSocket and SFU. [GOALS.md](GOALS.md) defines that target. No complete acceptance result exists for it. A bug in custom application code does not establish a missing Pipecat or Cloudflare feature.

## What the investigation established

| Finding | Evidence | What follows from it | What remains unproven |
| --- | --- | --- | --- |
| The prototype copies and edits Pipecat | [Patch](../pipecat-compat.patch), [119-module manifest](../src/pipecat/VENDOR_MANIFEST.json), [original audit](../audit/results/current-audit.json) | B1 needs a supported installation for the selected components | A particular package split is the only solution |
| Installation, imports, and startup are separate problems | Restoring upstream eager audio imports triggers the expected `audioop` rejection; restoring prewarming triggers the guarded thread-start rejection | Removing mandatory dependencies alone cannot fix those execution paths | The guards model every Workers restriction or every optional feature |
| The restricted core passes its local lifecycle checks | [Core harness](../scripts/check_pipecat_core.py), [original audit](../audit/results/current-audit.json) | Selected Pipecat context, cancellation, and lifecycle behavior can be exercised under these local guards | The full speech/output pipeline works on Workers |
| The application writes assistant history itself | [Pipeline and history code](../src/conversation.py), [controlled SFU observation](../audit/results/sfu-gaps.json) | Integrate Pipecat's assistant aggregator through the selected speech/output flow | A new SFU playback-report API or custom delivery ledger is required |
| Slow SFU cleanup delays the next model request | [Held-cleanup probe](../audit/acceptance/sfu_gaps.py), [recorded result](../audit/results/sfu-gaps.json) | Remove the application's scheduling dependency while retaining cleanup ownership | Sound continued during the wait or the SFU lacks a cancellation feature |
| Workers AI already supplies STT, LLM, and TTS; hosted Smart Turn is absent | [Provider calls](../src/providers.py), [Flux turn handling](../src/conversation.py) | Add and verify the hosted turn integration with one coordinated decision to answer | Model availability alone proves a compatible client or correct turn behavior |
| The existing SFU adapter converts browser Opus to/from PCM | [Managed adapter contract](https://developers.cloudflare.com/realtime/sfu/features/media-transport-adapters/websocket-adapter/) | The Worker can use the existing PCM audio boundary | It resolves application history, cleanup, or capacity requirements |
| Existing PCM conversion costs CPU | [Local measurement](../audit/results/local-media-costs.json) | Measure this processing in the deployed workload | A local number predicts Workers performance or call capacity |

The inspected upstream Pipecat version is 1.11.0 at `3dede06bec0b497bddfdcf047af7495ee9d0726e`. The audit verified the source archive, copied modules, and original/patched file fingerprints. The archive's SHA-256 is `49e7532a1f035e8884c47448a06d998a1b7f0943d11eae9bc8600a9e749a2a04`.

## What the history and cleanup tests mean

The prototype saves an assistant sentence only after browser completion reports arrive for its audio chunks. Its SFU route supplies no equivalent reports, so the next model call lacks the previous assistant answer. An existing regression expects that omission and passes. The diagnostic examines the next model input and exposes the consequence for follow-ups.

The shared runner also observes receipt dependence on direct WebSocket. Supplying simulated receipts is a passing control. These checks expose the custom application rule; they do not establish that every generated word belongs in history during interrupted speech. The target is the selected Pipecat speech/output behavior described in [CONVERSATION.md](CONVERSATION.md).

The cleanup test pauses the transport's cleanup operation after browser clearing has been sent, then queues the next model turn. That turn proceeds only after the test releases cleanup. This establishes application ordering. It does not measure network delivery of the clear event or audible silence.

The guarded core harness already uses Pipecat's user and assistant aggregators. It checks an interrupted text prefix, a later complete reply, and message ordering while rejecting selected imports and thread creation. It supplies synthetic text events. The prototype's source subset lacks the normal TTS and base output-transport components, so this result cannot establish their speech-progress or interruption behavior on Workers.

## Task 1 reference investigation

Task 1 began on a clean `codex/transport-contract` checkout at `e0ddc037d760b45e5411a9e2aaead00e9ae34a78`. Application source, vendored Pipecat, browser code and deployment configuration are unchanged. New probes and their results are separate from every earlier report. The [selected configuration](GOALS.md#task-1-reference-configuration) and [speech behavior](CONVERSATION.md#task-1-speech-reference) now define the reference for implementation.

| New evidence | What was exercised | Limit |
| --- | --- | --- |
| [Speech/output reference](../audit/results/task1-speech-final.json) | Normally installed upstream HTTP Aura TTS service, sentence aggregation, audio output queues, interruption and assistant context | Controlled HTTP and output leaves on CPython; no Workers AI inference or browser playback |
| [Turn coordination](../audit/results/task1-turn-verified.json) | Installed turn strategy and controller with controlled completion, transcript, resume and failure events | No live Nova/Smart Turn and no complete frame-queue race reproduction |
| [GPT parser fixtures](../audit/results/task1-gpt-fixtures.json) | Unchanged application parser with documented-client and adversarial stream shapes, including reader cancellation | Fake JavaScript stream/FFI boundaries; no package or runtime support claim |
| [Expanded runtime audit](../audit/results/task1-runtime.json) | Installation metadata, actual imports, denied-import paths, startup thread attempts, hosted HTTP analyzer execution | Guarded CPython is not Workers; a denied import alone does not prove that dependency is unavailable there |

The [initial speech report](../audit/results/task1-speech-reference.json) is also retained. The [verified rerun](../audit/results/task1-speech-verified.json) adds explicit checks for the intended provider error and preceding output progress, so a fixture timeout cannot masquerade as a synthesis failure. The final rerun also verifies the probe after adding refusal to overwrite existing evidence. All three reports agree on the ten reference outcomes. [Installation evidence](../audit/results/task1-speech-install.json) preserves the first DNS failure, subsequent normal installation and tokenizer preparation. No installed Pipecat source was changed.

The first turn-probe run is retained as [task1-turn-reference.json](../audit/results/task1-turn-reference.json). Five cases failed because the fixture omitted the required `STTMetadataFrame.service_name`; that is a test error. Adding the field produced the separate verified report. No platform claim relies on those fixture failures.

### Task 1 GPT-OSS interface

The initial request is `env.AI.run("@cf/openai/gpt-oss-120b", {messages, stream: true, reasoning_effort: "low", max_tokens: 2048})`. The model page documents binding streaming; Cloudflare's own Workers AI provider source supplies the `reasoning_effort` field. This is a source-verified request candidate, not a request accepted in this investigation. The separate `/ai/v1/responses` endpoint documents non-streaming requests only. [Model interface](https://developers.cloudflare.com/workers-ai/models/gpt-oss-120b/), [endpoint compatibility](https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/), [Cloudflare provider source](https://github.com/cloudflare/ai/tree/0e5bc3b2f489e4b8215905812a36fe69d37afb8d/packages/workers-ai-provider)

Cloudflare's client handles SSE `choices[].delta.content` as answer text and `delta.reasoning_content` or `delta.reasoning` separately. It reads `finish_reason` and usage; `[DONE]` terminates the stream. These are documented client conventions, not captured GPT-OSS events. The implementation must confirm the actual binding shape before mapping only answer text into `LLMTextFrame`. Explicitly distinguish a normal stop, token limit, provider error, empty answer and truncated stream. A function/tool event would need separate handling and must never become speech through a generic text fallback.

The baseline parser already skips the two documented reasoning fields in the controlled cases. It handles split UTF-8 reads and raises its provider error for a top-level error object. It nevertheless treats `finish_reason=length`, `finish_reason=error`, empty completion and unexpected EOF as normal generator completion. An adversarial mixed reasoning/`response` fixture also reaches the generic string fallback. That last shape is deliberately invented for defensive testing; it is not evidence that GPT-OSS emits reasoning as `response`.

Use 2,048 output tokens for the first bounded live probe. The baseline's 192-token cap is not evidence of a sufficient GPT-OSS budget. Budget for both reasoning and answer generation, then measure the provider's actual accounting and length outcomes. The fixture includes reasoning-only exhaustion, but cannot establish which cap or usage fields the binding applies. Low effort is the first setting to test; neither its accepted effect nor a latency improvement has been measured. First reasoning output must not count as first speakable answer.

Canceling the fixture task before an answer and closing the generator after its first answer both cancel/release the owned fake reader. Cloudflare's [pinned binding source](https://github.com/cloudflare/workerd/blob/34c69bf22fadb581411e430434ebdd90af0998a2/src/cloudflare/internal/ai-api.ts) supports an `AbortSignal` in the third options argument and forwards it to fetch. Verify that API in the pinned Python Workers deployment, including cancellation while awaiting `AI.run`, closing a reader and rejecting late response frames. The baseline comment about no documented abort signal is historical. Neither an abort signal nor reader cancellation proves remote compute or billing stopped.

Existing local Wrangler authorization could not refresh. No new GPT-OSS, Nova-3, Smart Turn or Aura inference request was made, and no service was deployed. Live request acceptance, stream shape, Python/JavaScript conversion, real provider errors, low-effort effect, output-budget sufficiency, answer quality, timing, cost and remote cancellation remain untested. The [interface and authentication record](../audit/results/task1-gpt-interface.json) and [pinned source list](../audit/reference/gpt-sources.json) preserve those limits.

### Task 1 obstacles and ownership

| Obstacle | Classification and reproducer | Smallest next action |
| --- | --- | --- |
| Receipt-dependent history and absent SFU answers | Application defect; historical next-model-input probes remain valid | Task 3 connects the standard speech/output/assistant path; no new receipt API or delivery ledger |
| Parser hides incomplete completion | Application defect; `gpt_probe.py` length/error/EOF fixtures | Task 3 adds a typed GPT-OSS event mapping, terminal outcome and answer-only handling after live interface verification |
| Normal TTS catches provider errors and still forwards sentence text | Standard behavior conflicts with safe failure continuation; `speech_reference.py` error cases | Task 3 must choose and test fail/abort handling at the service boundary. Retaining failed text is the observed reference, not successful synthesis |
| Required selected imports pull unused native/audio/image code | Package/import coupling; `runtime_probe.py` normal controls and denied-import subprocesses | Request a supported install/import path for the expanded components. Check individual dependency support in actual Workers before calling them unavailable |
| Prewarming attempts a Python thread | Package/runtime incompatibility under the thread guard; `worker-prewarm` probe | Supported startup that skips prewarming; do not patch installed candidate source |
| HTTP Smart Turn submits executor work | Package/runtime incompatibility under the thread guard; `http-analyzer-thread-guard` probe | Async binding client implementing `BaseTurnAnalyzer`, without local model inference |
| Stock HTTP Smart Turn reports INCOMPLETE after a local event-loop error | Upstream client defect; normal CPython probe logs `no running event loop` and observes zero HTTP attempts | Fix the client upstream or use the explicit Workers binding adapter; it is not evidence of a hosted model outage |
| HTTP analyzer uses a NumPy `.npy` POST contract | Provider/client protocol mismatch from pinned source and hosted input schema | Serialize the chosen Workers AI request. Renaming the endpoint cannot establish compatibility |
| Stale completion can emit inference after resumed speech | Strategy integration gap; `resume_during_pending` controller probe | Revision-aware completion handling; reproduce through actual frame scheduling in Task 2 before making a broader upstream claim |
| p99 fallback and inactivity watchdog can finish without both required signals | Configuration/integration gap; `no_stt_metadata` and `incomplete_watchdog` | Explicit strict readiness and bounded failure outcome; no unmeasured p99 value presented as transcript readiness |
| Detector exception escapes its strategy call | Integration error handling gap; `detector_error` | Owned async call, deadline, error reporting and revision invalidation in Task 2 |
| Available authentication fails | Test limitation; attempted standard Wrangler refresh | Restore test access, then run the focused live probes; no platform capability request follows |
| Nova events, hosted float32 request, selected Python deployment and full transport behavior | Unanswered questions | Run the recorded requests in the declared runtime and retain raw sanitized observations |

The expanded package metadata includes mandatory NumPy, Pillow, audio utilities/resamplers, NLTK, ONNX Runtime, aiohttp and OpenAI dependencies, among others. The runtime report records the full requirements and resolved versions. For Python 3.14, Pipecat 1.11.0 requires Pydantic at least 2.13, while the application pins 2.12.5. The [normal resolver dry run](../audit/results/task1-runtime-constraints.json) reproduces this conflict from the actual package and application declarations for Python 3.14.7. Its [first sandbox attempt](../audit/results/task1-runtime-constraints-sandbox.json) failed to reach the package index and remains a test limitation. Resolve the pin conflict in a reviewed dependency update, not a new Workers capability request. The successful CPython 3.12 install does not settle it.

Default user-turn strategies instantiate local Smart Turn v3. Configure both start and stop explicitly. Normal sentence aggregation also uses NLTK's `punkt_tab` data; make its availability part of installation/startup rather than relying on an unrecorded runtime download. `BaseOutputTransport` constructs a resampler, and imports Pillow even for audio-only use. Its video executor is lazy: the existence of that executor does not establish an audio-path thread requirement.

These results support a broader package request than the old restricted-core audit. They do not justify a new SFU playback API, local inference, or a general thread capability for the chosen hosted model path. A supported package and actual Workers execution remain open.

## Task 2 turn integration

Task 2 started from clean local checkpoint `3521b8fad6c49deee48ad990100e431ca7932d5d`, which preserved Task 1's documentation, probes and evidence. No historical result file was overwritten. Task 2 changes the application input/turn path to Nova-3 and hosted Smart Turn; it leaves Llama, Aura speech output, browser receipt history and the known SFU history/cleanup gaps in place.

The provider uses Nova's speech-start events, finalized transcript ranges and pause flag. A pause launches a bounded async hosted request. Only a current COMPLETE decision with finalized, nonempty transcript coverage can reach Pipecat's standard user aggregation and inference signal. The custom strategy checks the revision at commitment. There is no Flux end trigger or extra 1,200 ms grace period. [Configuration and timing limits](CONVERSATION.md#task-2-user-turn-coordination)

| New record | Observation | Limit |
| --- | --- | --- |
| [Complete offline checks](../audit/results/task2-offline-verification.json) | 58 Python and 62 JavaScript tests passed; provider, restricted-core, entry/lifecycle/access, 10 SFU-entry checks, 12 conversation-runner checks and 13 audit-tool checks passed | CPython 3.12.14 with the vendored package and synthetic provider/media I/O; no deployed acceptance |
| [Final focused checks](../audit/results/task2-final-checks.json) | 20 turn tests and the restricted-core check passed after retaining decision probability and the 8.2-second input buffer/8-second maximum snapshot | Exact source hashes are recorded; the small final changes do not alter transport or assistant behavior |
| [Runtime fixture probe](../audit/results/task2-runtime-probe.json) | Twelve synthetic scenarios passed, including incomplete/resumed speech and detector cancellation | Executed on CPython, despite being callable from the Worker fixture route; not a Workers result |
| [Provider checks](../audit/results/task2-provider-checks.json) | Nova request, KeepAlive, float32 encoding, response validation, timeout, cancellation and late settlement passed with fake bindings | No live model acceptance or remote cancellation proof |
| [Fresh access check](../audit/results/task2-live-access.json) | Existing noninteractive Cloudflare authentication could not refresh; no model request or deployment was made | Test limitation; no provider/runtime failure claim follows |

The frame-queue tests reproduce and guard several application races: resumed speech overtaking a queued completion, a late old pause after a newer onset, and closure of an aborted turn overtaking a fresh turn. Watchdog closure now reports the abandoned turn instead of silently dropping it. A late final transcript cannot revive a timed-out revision. These were application defects found during implementation and are covered by reproducible tests in `tests/test_smart_turn.py` and `tests/test_turn_coordination.py`; they do not require a new Workers capability.

Detector results and finalized text can arrive in either order. Reordered final ranges wait for complete coverage; duplicates cannot cause another response. Malformed, overlapping or cross-turn ranges fail without adding user context. A transcript advancing beyond a pending pause before a matching resumption event also fails visibly. The latter rule is deliberately conservative pending actual Nova event observations.

The detector wait is two seconds; the full pause-readiness deadline is five seconds. There is at most one unresolved hosted binding request per provider instance. A canceled or timed-out request keeps that slot until its promise settles, so another pause can fail visibly while old remote work remains unresolved. This prevents local request accumulation; it does not prove remote cancellation or that a stuck provider will recover without starting a new call. The application retains 16 final segments, 8,192 transcript characters and 8.2 seconds of PCM, with an analysis snapshot no longer than eight seconds.

The remaining obstacle is evidence, not an established platform incompatibility: the hosted float32 request, Nova timestamp/empty-event ordering, final-range coverage, Python Workers execution, real speech, timing and cancellation still need focused live checks. A transcript range is not a trailing-silence watermark. The candidate snapshots at that range's endpoint and adds no guessed silence. Missing or inconsistent coverage fails closed and asks for repetition. [Primary contracts and isolated live reproducer](../audit/reference/task2_turn_sources.json), [reproduction instructions](DEVELOPMENT.md#verify-the-task-2-turn-connection)

No package-support claim changes: the vendored subset remains, the full installation/runtime gaps from Task 1 remain open, and no new capability request is justified by expired credentials. Task 2's implementation and controlled checks are complete. Task 3 is the next bounded change: integrate GPT-OSS and the selected standard speech/output/assistant-context path, preserving the Task 1 failure/interruption reference. It has not started.

## Task 2 deployment follow-up

After the user completed Wrangler login and authorized deployment, application commit `61f589bf0740182562659e5a82c04870f5279e2f` was published to [pipecat-on-workers](https://pipecat-on-workers.korinne.workers.dev). The [deployment record](../evidence/deployment-task2-bc45735b.json) identifies version `bc45735b-7900-456c-b717-8096d9632456` at 100% traffic, its predecessor, source hashes, pinned tools and verification results.

The bundle built with Wrangler 4.139.0, workers-py 1.17.4 and workers-runtime-sdk 1.9.0. The deployed health endpoint reports Python 3.14.2, Pyodide 314.0.6 and the existing patched Pipecat 1.11.0. Both example pages returned 200; unauthenticated access and session creation returned 401. Test routes stayed disabled and the existing demo/SFU secrets remained configured. Wrangler reported a dashboard binding-configuration difference and applied the repository configuration; the Conversation binding and v1 migration were unchanged in source.

Before publishing, the [locked CPython 3.14.7 checks](../audit/results/task2-python314-verification.json) passed all 58 Python tests, provider checks, the restricted-core check and seven entry lifecycle cases using Pydantic 2.12.5 and the declared dependency versions. This extends the earlier CPython evidence; it does not resolve normal installation of the full upstream package.

The first HTTP verifier was rejected by Cloudflare with 403/error 1010 before reaching the Worker. Repeating the checks with the standard HTTP client passed without changing service security settings. This was a verification-client limitation, not an application failure.

Cloudflare CLI access is restored. Deployment and HTTP checks establish imports/startup and access protection only: no live model request, authenticated voice call, SFU media check or physical playback check was made. The existing demo key value was unavailable in the scoped test environment and was not reset. Nova/Smart Turn request acceptance, timestamp alignment and end-to-end behavior remain untested. The isolated local binding probe can now be run with the restored CLI access; Task 3 has not started.

## Keyless demo access

At the user's request, both voice pages and the developer transport page no longer ask for a shared demo key. Session creation is public, so anyone with the URL can start calls using this Worker's AI and SFU resources. Each new call still gets its own private capability token. Existing call access, diagnostics, SFU signaling and signed media callbacks retain their checks; production fixture routes remain disabled. The old `/api/access` endpoint returns success for client compatibility, and any configured `DEMO_ACCESS_KEY` is ignored.

The [offline verification](../audit/results/keyless-access-offline.json) passed 58 Python tests, 62 JavaScript tests, 48 creation/access cases, 10 SFU-entry checks and seven entry lifecycle cases. It covers starting both browser routes without a key, microphone denial and cancellation, malformed requests before allocation, unique call credentials, rejection of missing/wrong/cross-call credentials, and unchanged media authorization. These checks use fake SDK/provider/media I/O; they make no live voice or playback claim. Earlier reports retain their original access expectations.

Application commit `848576841bc32b4ebc08a9cba56861ffe624e03d` was deployed as version `bb1d9ecc-971d-4e2b-a1d8-7608df2aa7c9` at 100% traffic. The [deployment checks](../evidence/deployment-keyless-bb1d9ecc.json) verified public creation for both transports, unique credentials, authorized diagnostics, rejection of missing/wrong/cross-call tokens and disabled fixture routes. Both voice pages returned 200 without key controls. The open WebRTC page was refreshed and showed Start conversation enabled.

The [completed verification](../evidence/deployment-keyless-bb1d9ecc-verified.json) also confirms that the developer page matches the deployed source. Two verifier mistakes are preserved: the first attempt expected 200 before following the static page's 307 redirect; the [follow-up](../evidence/deployment-keyless-bb1d9ecc-followup.json) checked the wrong WAV control ID. Correcting those checks required no application change. This verification created two session records, which expire under the existing 24-hour alarm; it opened no voice connections and made no model requests. Live voice and physical playback remain untested.

## Nova startup HTTP 400

After the keyless deployment, the user reported a speech-recognition HTTP 400 immediately after Start. An [isolated live A/B probe](../audit/results/nova-startup-parameter-ab.json) reproduced the rejected request without sending audio. Workers AI reported that numeric `channels: 1` was invalid because it expected a string. Changing `channels`, `interim_results` and `vad_events` to string values produced HTTP 101 and a WebSocket, which the probe immediately closed. Cloudflare's [own Nova adapter](https://github.com/cloudflare/agents/blob/main/packages/voice/src/workers-ai-providers.ts) also uses string parameters for its WebSocket handshake.

This was an application request-format defect in Task 2. The fake binding accepted the earlier numeric/boolean values, so passing offline tests did not establish the live contract. The [corrected provider checks](../audit/results/nova-startup-offline.json) reject non-string handshake parameters and cover each old typed value; all 58 Python conversation tests and seven entry lifecycle cases also passed. The application and the isolated Task 2 probe now use the accepted string values. Task 3's GPT-OSS and assistant-context work is separate and was not started.

The first probe attempt used `--local`, which disabled the remote AI binding in Wrangler 4.139.0. Removing that flag enabled the configured remote binding. That first failure was a test limitation; it made no model request. The successful A/B test executes JavaScript locally with live Workers AI, sends zero audio bytes and establishes only the handshake contract. It does not establish Nova transcription, hosted Smart Turn decisions, LLM/TTS behavior or physical playback. Earlier evidence remains unchanged.

The correction in commit `195792064d61eb1f60a4817f87f42f506acd5437` is deployed as version `3f8bd208-8e4a-4f2d-96cb-d9a0e51f9542` at [100% traffic](../evidence/nova-startup-deployment-metadata.json). The [deployed Python startup checks](../evidence/nova-startup-deployed-3f8bd208.json) created one session per transport, opened each control WebSocket, observed `ready`, immediately sent `end`, and checked private diagnostics after closure. Both reached ready without an error, closed normally and reported zero remaining owned tasks, sockets, readers and pending work. No audio was sent and no SFU media signaling was performed. This verifies the actual Python connection fix; the full spoken turn still needs live validation.

## Recorded local results

The [original audit](../audit/results/current-audit.json) recorded 5 observations supported within its scope, 6 gaps, 10 untested entries, and no test errors. Those counts belong to its original checklist. They are not a release score. Its existing regression run passed 41 Python and 62 JavaScript tests. Some of those tests preserve the prototype's known limitations.

The [shared diagnostic baseline](../acceptance/results/local-baseline.json) recorded three gaps: missing generated replies without receipts on both routes, and SFU cleanup holding up the next turn. These are two application issues seen through three checks. Three controls passed: WebSocket history after simulated receipts and pending-generation cancellation on both routes. Seven entries were untested and none produced a test error under that runner's definitions.

Historical result files include retired checklist entries and a proposed custom delivery-state requirement. They remain unchanged so the observations can be traced. Current runners keep the same behavioral probes while removing retired requirements from their untested lists and identifying the new hosted-turn/reference-pipeline checks. The checklist contents differ; equal totals would not mean equal coverage. New reports need their own filenames, identities, and interpretation; old entries cannot expand the agreed release scope.

The audit classifier's 13 checks and the shared diagnostic runner's 12 checks passed in their recorded runs. They check the test tools, including distinctions between an expected gap, unrelated failure, incompatible test API, and disabled assertions. They do not provide additional voice or runtime acceptance.

The local audit used CPython 3.12.14, Pydantic 2.13.5, and typing-extensions 4.16.0. The application declares Python 3.14, Pydantic 2.12.5, and typing-extensions 4.15.0. Local results therefore apply to their recorded environment. A candidate still needs testing with its declared dependencies and on actual Workers.

The guard checks selected optional imports and `threading.Thread.start`; it does not cover every native dependency or every way to create threads. The audit removes inherited Python optimization from guarded subprocesses and verifies assertions are enabled. An unrelated error, a timeout, or a mismatch with the expected candidate API is a test error, not proof of a platform limitation.

## Checks after documentation consolidation

The 12 conversation-runner checks and 13 audit-tool checks passed again. The [new conversation diagnostic run](../acceptance/results/docs-consolidation-diagnostics.json) reproduced the same three gaps and three scoped passes, with seven current requirements untested. The [new package audit](../audit/results/docs-consolidation-audit.json) recorded three scoped passes, six gaps, and nine untested entries, with no test errors. That audit did not rerun the existing Python and JavaScript regression suites, so those entries are untested in that particular result.

Both runners exited 1 because unresolved requirements remain. Their probe logic is unchanged; only descriptive limits and outstanding-check lists changed. No application source or previous result file changed, and no service was deployed. The pre-consolidation handoff and standalone audit are preserved in the separate `outputs/archive/` folder supplied with this workspace. They are historical reference material, outside the current handoff's documentation.

## Resource observations

The local PCM experiment processed ten seconds of synthetic audio in both directions and measured about 83.9 ms of process CPU per second of simultaneous input/output audio on this Mac. It covers sample conversion and packet wrapping. It does not include Workers, network traffic, provider execution, language-boundary costs, or audio-quality acceptance. It cannot predict deployed call capacity.

No deployed resource threshold has passed. The [resource contract](../audit/acceptance/resource-contract.json) leaves thresholds undecided. Actual workload tests must measure bounded queues, whole-isolate memory, platform CPU, latency, cleanup, and cost for both supported routes.

## Existing live and runtime evidence

| Evidence | Observation | Limit |
| --- | --- | --- |
| [WebSocket recorded-speech check](../evidence/websocket-two-examples-smoke.json) | Real provider round trip and local owned-resource counters at End | Recorded input and software observations do not establish acoustic behavior |
| [SFU activation](../evidence/sfu-activation.json) | Two generated-speech inputs through real WebRTC and nonzero decoded response audio | Physical playback, context continuity, audible interruption, and complete remote cleanup were not established |
| [Historical duration run](../evidence/real-provider-soak-summary.json) | Two simultaneous calls over 600.011 seconds with repeated interruptions and recovery | Earlier revision and direct WebSocket only; no current two-route acceptance |
| [Historical provider startup failure](../evidence/real-provider-pending-capacity-429.json) | HTTP 429 prevented a case from reaching its cancellation check | A later retry does not turn that attempt into a pass |
| [Historical runtime lifecycle tests](../evidence/workerd-lifecycle-py314-sdk.json) | Controlled restart in the local Python 3.14 runtime | No claim of seamless recovery from arbitrary production eviction |

The earlier investigation observed sustained failures in the selected Python 3.13 runtime and later passing ten-minute local fixture runs on Python 3.14. Those records describe the named versions and inputs. They do not establish the interpreter-level cause or indefinite remote stability. Keep runtime identity explicit when reproducing or extending the work.

## Evidence still required

No supported Workers package candidate has been accepted. Task 1 adds a normal CPython speech/output reference and controlled compatibility probes. Task 2 adds a locally tested Nova/Smart Turn coordinator. Hosted Smart Turn, Nova-3, GPT-OSS-120B and that speech/output flow still have not passed the selected Workers integration checks. Both routes still need declared failure/reconnect behavior, physical audio tests, and workload acceptance on the exact revision proposed for support.

Every new result must identify its source, artifacts, deployment/runtime, models, transport, inputs, workload, raw observations, and agreed thresholds. Keep local simulation, actual runtime execution, live network tests, and physical audio measurements distinct. Preserve failures and mark missing evidence untested.

For a package claim, test the actual installed artifact. For an application claim, rerun the relevant observation against the changed code. For a platform claim, exercise the required platform operation. The [acceptance plan](ACCEPTANCE.md) defines these tests; the [implementation plan](IMPLEMENTATION-PLAN.md) limits each coding session's work. [DEVELOPMENT.md](DEVELOPMENT.md) explains how to repeat diagnostics without overwriting the original records.

## Continuation: selected providers and standard speech

The [live turn investigation](TURN-LIVE.md) preserves real Nova events, hosted float32 requests, an HTTP 429, the old deployed split-turn failure and the later empty-silence correction. One assembled pause/resume fixture still receives INCOMPLETE. No model decision was overridden.

[GPT-OSS](GPT-OSS.md) records real Python binding request and stream shapes, answer/reasoning separation, completion outcomes, the 2,048-token probe budget and active cancellation. [Speech/context](SPEECH-CONTEXT.md) connects the standard pinned service/output/assistant components on both adapters. [Storage handling](PERSISTENCE.md) defines acknowledged commits and fatal save failure. The [integration report](../audit/results/task3-speech-integration.json) separates controlled tests from a local Python Worker using live providers; the latter completed one spoken question and response after the silence correction. These precommit checks do not substitute for final revision verification.

[Transport extraction](TRANSPORT-ADAPTERS.md) and [cleanup ownership](CLEANUP.md) remain separately reviewable. [Published-package results](PACKAGE-CANDIDATE.md) leave B1 open. [Vendored speech provenance](VENDORED-SPEECH.md) identifies the code and compatibility changes actually loaded by this prototype.

The user approved testing on their laptop and explicitly deferred performance acceptance. Capacity, sustained load, platform CPU, whole-isolate memory, numeric latency targets and cost acceptance remain untested. Physical microphone and audible playback need the user's participation on the final deployment.

## Deployed candidate d2c0656

[Independent verification](FINAL-VERIFICATION.md) records passing offline checks and local Workers lifecycle checks, followed by actual provider checks on deployment `13762ff7-679d-477a-bafc-dcf37df2d5f4`. Ordinary direct speech, explicit interruption/recovery and one alternate context-dependent question pass. The original follow-up question and paused recording fail hosted turn completion. The user also reports inconsistent answers and the same turn error in a real direct-route call. This candidate is not accepted for normal conversation.

The strict adapter omitted the pinned reference's three-second max-silence completion path. A guarded draft is being tested separately pending the user's turn-policy decision. The current deployment still requires a positive hosted decision. Later fixes and checks need new source and evidence identities.

## Browser correction deployment and silence proposal

[Browser revision a5ddcae](../evidence/deployment-browser-4d75ac46.json) is deployed as `4d75ac46-5a71-4de4-aa12-26f8b026fc38`. Its independent checks pass 78 JavaScript and 18 SFU cleanup tests; all server files match the earlier deployment. Served assets and private access rules match the tested source. One short actual-provider direct call passes with timed software receipts. The deployed turn policy still rejects the known follow-up and pause cases; no supported-release claim follows from the browser fixes.

The [separate proposal](../audit/proposals/silence-fallback-20261005/PROPOSAL.md) restores a guarded maximum-pause path, using Pipecat's three-second reference as its starting point. It preserves the hosted model's INCOMPLETE result. Its controlled tests and real-provider checks pass the original follow-up in two isolated calls and the paused recording, but it remains unapproved and outside production. Historical failures, the failed physical laptop check and the draft's initially failed SFU fixture all remain available. [Current verification and remaining limits](FINAL-VERIFICATION.md).

## SFU gathering correction

[The SFU setup report and investigation](SFU-GATHERING.md) preserve the user's failed route-discovery attempt, a native STUN check, failing pre-fix fixtures, current primary-source contracts and passing controlled/independent checks. Commit `3f62b68` is deployed as `06111df0-88ce-4142-96e7-4158a9a6e7a9`. The served client hash matches and a direct provider regression passes. Chrome SFU verification after the fix remains pending. No TURN relay or silence-policy change was introduced.

## SFU laptop retry

The [later user report and screenshot](../evidence/sfu-laptop-20261005/user-report.json) show an SFU call with microphone activity and the recognized words “hello hello.” The user was not wearing headphones and reported no reply. This establishes observed speech input for that attempt, while a completed turn and audible output remain unverified. The active deployment was rechecked as `06111df0-88ce-4142-96e7-4158a9a6e7a9`; the screenshot does not identify its loaded assets. No specific cause is assigned without the call events or error details. The application and pending turn policy remain unchanged.

## Silent-input corrections

[The second SFU report](../evidence/silent-input-20261005/user-report.json) has no visible transcript. [Controlled reproductions and independent checks](SILENT-INPUT.md) now cover missing callback PCM, rejected Nova starts/results and preserved browser response state. Source `f8835e8` is deployed as `3b1dcd04-5515-4b83-9fe4-27511b619c03`; a direct actual-provider call produced a correct answer and nonzero audio, then ended with zero tracked local counters. Physical SFU acceptance remains open. Download measurements now includes allowlisted private diagnostics while a call is active. No transcript, audio or capability token is exported.

## Explicit SFU turn error

The [latest user report](../evidence/sfu-turn-error-20261005/user-report.json) shows the generic active-turn abort message with no transcript. A read-only review identified seven possible causes and four reason names missing from the browser export. The exact call cause remains unknown. The [debugging brief](DEBUGGING-BRIEF.md) prioritizes capturing that failure before another behavior change. No application change or deployment was made for this handoff.
