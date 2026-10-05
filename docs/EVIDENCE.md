# Evidence and remaining unknowns

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

## Selected LLM still needs verification

The target LLM is now Workers AI `@cf/openai/gpt-oss-120b`. The source and all recorded provider runs still use Llama. Cloudflare documents a streaming binding interface, but this handoff has not called GPT-OSS or tested its response format, reasoning controls, cancellation, latency, or answer quality. [Model documentation](https://developers.cloudflare.com/workers-ai/models/gpt-oss-120b/)

The existing LLM parser yields chat-completion text deltas or a string `response` directly into the application's speech path. It does not independently identify reasoning or inspect a completion reason. This is a compatibility check to perform, not a reproduced GPT-OSS failure. [Provider parser](../src/providers.py), [AI2 acceptance](ACCEPTANCE.md#ai2-stream-gpt-oss-answers-into-speech)

## Hosted turn integration still needs verification

Nova-3 plus hosted Smart Turn is the first STT pairing to evaluate. Flux already supplies turn decisions, so adding Smart Turn without choosing their roles would leave two possible ways to trigger an answer.

The model and its client have different execution requirements. In pinned Pipecat source, `HttpSmartTurnAnalyzer` inherits a thread-backed analysis path. Its synchronous endpoint method bridges to a coroutine. Hosting inference on Workers AI therefore does not establish that this existing analyzer runs unchanged in Workers. [HTTP analyzer](https://github.com/pipecat-ai/pipecat/blob/3dede06bec0b497bddfdcf047af7495ee9d0726e/src/pipecat/audio/turn/smart_turn/http_smart_turn.py), [base analyzer](https://github.com/pipecat-ai/pipecat/blob/3dede06bec0b497bddfdcf047af7495ee9d0726e/src/pipecat/audio/turn/smart_turn/base_smart_turn.py).

The bounded reference investigation must identify the actual service/output components, speech-start signal, transcript timing, and asynchronous hosted connection. It must then record any demonstrated Workers incompatibilities. These are open integration checks, not evidence that an entire subsystem needs to be rebuilt.

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

No supported upstream candidate package has been accepted. Hosted Smart Turn, the selected STT pairing, GPT-OSS-120B, and the conventional Pipecat speech/output flow have not passed this release's integration checks. Both routes still need declared failure/reconnect behavior, physical audio tests, and workload acceptance on the exact revision proposed for support.

Every new result must identify its source, artifacts, deployment/runtime, models, transport, inputs, workload, raw observations, and agreed thresholds. Keep local simulation, actual runtime execution, live network tests, and physical audio measurements distinct. Preserve failures and mark missing evidence untested.

For a package claim, test the actual installed artifact. For an application claim, rerun the relevant observation against the changed code. For a platform claim, exercise the required platform operation. The [acceptance plan](ACCEPTANCE.md) defines these tests; the [implementation plan](IMPLEMENTATION-PLAN.md) limits each coding session's work. [DEVELOPMENT.md](DEVELOPMENT.md) explains how to repeat diagnostics without overwriting the original records.
