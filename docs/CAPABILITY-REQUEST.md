# Pipecat on Workers capability request

Baseline audit prepared 3 October 2026. Handoff requirements updated 4 October 2026.

[Goals and scope](GOALS.md) is the current behavior contract. This audit supplies its package and runtime evidence. The [implementation plan](IMPLEMENTATION-PLAN.md) separates changes to this application from platform and package requests.

The first release should run a defined Pipecat voice pipeline in a Python Durable Object, using Workers AI for hosted Smart Turn, STT, GPT-OSS-120B, and Aura-2 TTS. Direct WebSocket and SFU should preserve that pipeline's conversation and interruption behavior. The prototype supplies a useful baseline, but it still copies Pipecat source and bypasses its normal assistant-history path.

This request separates package support from the application changes and deployed tests still needed. It does not require a particular package layout.

## Scope and evidence

The baseline is [pipecat-on-workers at 6c17c080](https://github.com/korinne/pipecat-on-workers/tree/6c17c0805f13f7609ba0a93ea8bf4c945797de18), using selected Pipecat 1.11.0 source.

The supported configuration has one voice conversation per Python Durable Object. Workers AI supplies all four model tasks. The application must coordinate speech onset, hosted Smart Turn completion, and transcript readiness; maintain context through Pipecat's assistant aggregator and speech/output flow; cancel obsolete work; and bound queued media. Video, local model inference, arbitrary provider SDKs, and support for every upstream transport are outside this release. A custom delivery ledger, routine playback-uncertainty prompt notes, and exact-word browser receipts are also outside the agreed scope.

The runnable audit keeps the application at the baseline; it does not change application code to make a test pass. Results use four categories: `supported_in_scope`, `observed_gap`, `untested`, and `test_error`. Earlier live results remain evidence of those earlier runs. Local tests with simulated providers can establish application behavior, but they cannot establish live audio quality or the deployed system's capacity.

## What exists and what is needed

| Area | Existing implementation | Work or question that remains | Proposed owner |
| --- | --- | --- | --- |
| Installation | The repo contains copies of 119 Python modules, a reduced dependency list, and six source edits | Supply a supported installable package for the agreed core, without requiring unused native dependencies | Pipecat |
| Imports | Local edits defer loading native audio, image, RTVI, and strategy code | Give the upstream core the same independence; explicitly fail if an unsupported feature is selected | Pipecat |
| Startup | A local option disables import prewarming, which loads code early using a thread | Support startup that does not create a thread | Pipecat |
| Host event loop | The prototype already embeds PipelineWorker directly in the host's event loop, which schedules its work | Document and test starting, cancelling, closing, and restarting while the host owns that loop | Pipecat and Workers |
| Provider connections | Custom adapters call Workers AI for Flux STT, Llama 3.3, and Aura-2 TTS | Integrate GPT-OSS-120B through Workers AI and test the provider adapters with the selected Pipecat service and output components | Integration and Workers |
| Turn detection | The application uses Flux start/end events; hosted Smart Turn is absent | Integrate hosted Pipecat Smart Turn, coordinate transcript readiness, and establish one authority for ending a user turn | Integration and Workers |
| SFU media | Cloudflare's managed WebSocket adapters and the application's transport and format conversion already exist | Verify the existing integration with the selected Pipecat interfaces, media behavior, and connection lifetime | Integration and SFU |
| Conversation history | Custom code requires browser receipts to save an assistant answer; the SFU route omits answers | Use Pipecat's assistant aggregator through the specified TTS/output flow; compare completed and interrupted context against that configuration on both routes | Integration |
| SFU interruption | Local clearing exists, but the invalidation operation waits for remote cleanup | Stop treating the old response as valid immediately, while keeping responsibility for bounded cleanup | Integration first, SFU for API behavior |
| SFU cleanup | Some failures retain resource IDs, but final cleanup is best effort | Demonstrate that resources are eventually released or report remote state that remains unresolved; clarify the APIs for checking and reconciling that state where needed | Integration and SFU |
| Resource limits | Deployed workload acceptance is incomplete | Define and measure latency, bounded queues, CPU, total isolate memory, concurrency, and cost for the selected pipeline | Workers and integration |

The [compatibility patch](../pipecat-compat.patch), [source manifest](../src/pipecat/VENDOR_MANIFEST.json), and [evidence map](EVIDENCE.md) provide the baseline evidence. The copied source's hard-coded version is packaging bookkeeping, so it does not need to become an upstream feature. Pipecat's default runner owns the process; it does not need porting if the supported approach of running within the host's event loop meets this scope.

## Package and pipeline requirements

Provide an installable candidate for the agreed core. Optional dependencies must be absent from both mandatory installation requirements and unrelated import paths. Support a startup option that skips work that creates a thread, while preserving the existing pipeline, turns, conversation context, and cancellation behavior.

Prewarming means loading code before it is needed. The inspected startup path sends that work to a Python thread so it can run separately from the event loop. The restricted Workers configuration cannot use that thread path. It can still use asynchronous tasks: while one task waits for network input, another can proceed on the same event loop. Skipping prewarming removes this particular startup requirement; it does not establish that every optional Pipecat feature can run without threads.

Separate packages and a lightweight base package with optional extras are both acceptable. Adding an optional extra alone cannot remove dependencies that the base package still requires.

Document how an application embeds, starts, and closes Pipecat. Identify the supported places to connect Workers AI provider adapters and the two selected media routes. Include the standard TTS/output components needed for the agreed context and interruption behavior; the current copied subset does not include them. A raw-text aggregator test alone cannot establish that voice behavior.

Completion requires an actual candidate package with its resolved dependencies, proof that the tests import that package rather than the repo's copied source, a pass on the guarded lifecycle test, and the same behavior on the selected Workers runtime. Install declared dependencies normally before testing. The test runner must not add undeclared dependencies, patch package files, or insert fake native modules to obtain a pass.

## Task 1 additions to the request

The request now covers the [specified reference](GOALS.md#task-1-reference-configuration), including `STTService`, `LLMService`, `TTSService` with sentence aggregation, `BaseOutputTransport`, `LLMContextAggregatorPair`, explicit turn strategies and an async hosted analyzer. The [expanded reproducer](../audit/reference/runtime_probe.py) imports those modules from a normal Pipecat 1.11.0 installation, verifies source hashes, then runs separate import and thread guards. [Results](../audit/results/task1-runtime.json)

Normal installation and imports succeeded on CPython 3.12.14. That installation resolves substantially more dependencies than the vendored prototype. Python 3.14 also activates a Pydantic `>=2.13` requirement that conflicts with the application's `2.12.5` pin. A [normal resolver dry run](../audit/results/task1-runtime-constraints.json) reproduces that application pin/package metadata conflict. Resolve it in a reviewed dependency update; it does not require a new Workers capability. Do not present the local install, `--no-deps`, or a reduced patched package as B1 evidence.

The import guard reproduces eager audio dependencies in services/strategies and Pillow in audio output. Standard sentence aggregation needs NLTK and its `punkt_tab` data. A supported package must make unused features optional and supply a declared tokenizer-data/startup path. Individual dependencies still need actual Workers checks; a local import-denial policy cannot establish their platform availability.

Thread probes identify prewarming and the HTTP Smart Turn analyzer's executor. Audio output's video executor is lazy and is not evidence that audio-only output starts a thread. Support host-loop startup without prewarming, and an asynchronous hosted analyzer path. The latter can use `BaseTurnAnalyzer`; it does not require shipping local model weights or enabling general-purpose threads.

The stock `HttpSmartTurnAnalyzer` also fails on ordinary CPython before any HTTP attempt: its executor callback asks for a running event loop, catches `no running event loop`, and returns INCOMPLETE. Record this as an upstream client defect. Its `.npy` request and `prediction` response contract differ from the Workers AI schema's audio/dtype and `is_complete` fields. These findings justify the small binding adapter; they do not establish a Cloudflare inference failure.

The [obstacle table](EVIDENCE.md#task-1-obstacles-and-ownership) separates these package/client issues from application defects, controlled-test limits and unanswered live questions. Parser completion handling, stale turn results, standard TTS nonfatal-error continuation and custom history are integration work. Current Cloudflare binding source exposes `AbortSignal`, so do not request that as a missing capability based on the old provider comment. Verify it in the selected Python runtime first.

## Workers AI turn and provider integration

The [current provider code](../src/providers.py) calls Workers AI through `env.AI.run`. Select `@cf/openai/gpt-oss-120b` for the LLM and keep Aura-2 for TTS. The current source and recorded provider runs still use Llama. Task 1 selects Nova-3 plus hosted Smart Turn as the reference pairing; live verification remains open. The existing Flux integration already supplies turn decisions, so combining Flux with Smart Turn requires explicit ownership of that decision. [Nova-3](https://developers.cloudflare.com/workers-ai/models/nova-3/), [Flux](https://developers.cloudflare.com/workers-ai/models/flux/)

Cloudflare documents GPT-OSS streaming through the Workers AI binding. Verify its actual request options, response events, and cancellation from Python before adapting the selected Pipecat LLM service. Task 1 fixtures confirm the current parser reads `choices[0].delta.content` or a string `response` and does not expose completion reasons; anything it yields becomes speech input. Establish which events contain answer text and exclude reasoning or metadata from that path. Select an output budget that leaves room for a useful spoken answer, and measure latency and cost. A new transport or local model runtime is not needed merely to select this hosted LLM. [GPT-OSS model documentation](https://developers.cloudflare.com/workers-ai/models/gpt-oss-120b/)

Cloudflare hosts `@cf/pipecat-ai/smart-turn-v2`. The Worker calls the hosted model; loading a local turn model in Python is outside scope. Model availability does not prove that the existing Pipecat HTTP analyzer works unchanged in Workers. The pinned [HTTP analyzer](https://github.com/pipecat-ai/pipecat/blob/3dede06bec0b497bddfdcf047af7495ee9d0726e/src/pipecat/audio/turn/smart_turn/http_smart_turn.py) inherits a thread-backed analysis path from its [base analyzer](https://github.com/pipecat-ai/pipecat/blob/3dede06bec0b497bddfdcf047af7495ee9d0726e/src/pipecat/audio/turn/smart_turn/base_smart_turn.py), and its synchronous endpoint method bridges to a coroutine. Hosting inference does not remove those client execution requirements. Reuse the framework's turn strategies and add the smallest provider connection needed for the hosted API. [Hosted Smart Turn](https://developers.cloudflare.com/workers-ai/models/smart-turn-v2/), [Pipecat speech input](https://docs.pipecat.ai/pipecat/learn/speech-input)

Test speech onset, a pause followed by resumed speech, semantic completion, and final transcript readiness as distinct events. Each completed user turn must cause one intended response. Late detector results must not end a newer turn. Define and test bounded behavior for detector failures and missing transcripts before live validation.

## Runtime requirements

Define and support the Python runtime and input/output behavior for this integration: asynchronous streaming, cancellation, the lifetime of objects shared between Python and JavaScript, restart behavior, and resource accounting. The current remote providers use custom integrations. Successfully importing a provider package would not, by itself, establish that the provider works.

Record the runtime's CPU accounting and when its allowance resets. Include long bot responses while the microphone is muted or uses discontinuous transmission, which leaves gaps in incoming audio. Current Durable Object documentation allows 30 seconds of active CPU per invocation by default, configurable to 300 seconds, and describes resets on incoming HTTP/WebSocket events. The duration of a call and the time actively spent computing are different measurements. [Durable Object limits](https://developers.cloudflare.com/durable-objects/platform/limits/)

Measure total memory for the isolate, the runtime environment in which the code runs. The published Workers limit is 128 MB, including JavaScript and Wasm allocations, shared by work in an isolate. Assigning one Durable Object to each call does not establish a separate memory allowance for each call. For this baseline, include Python, sample conversion, copies of data, and queued audio. [Workers limits](https://developers.cloudflare.com/workers/platform/limits/)

## Existing SFU integration

Confirm and document the supported behavior of the existing WebSocket media adapters for voice agents. This includes the PCM audio format and sending rate, canceling queued output, reconnecting, and checking that remote resources have been released. Cloudflare already supplies the bridge. [WebSocket adapters](https://developers.cloudflare.com/realtime/sfu/features/media-transport-adapters/websocket-adapter/)

The managed adapter also handles Opus conversion. Incoming browser audio reaches the SFU as compressed Opus and reaches the Worker as raw PCM. In the other direction, the adapter converts the Worker's PCM output to Opus for the browser. The Worker still converts between the adapter's PCM format and the speech providers' formats.

The application's custom history rule waits for browser completion reports. Its SFU path lacks equivalent reports and omits assistant answers. Replace that rule with the agreed Pipecat speech/output and assistant-aggregation path. Compare completed and interrupted answers with the selected Pipecat configuration. No new SFU playback-report API is required by this scope. The [conversation behavior](CONVERSATION.md) defines the cases to test.

Cleanup raises a separate question. The application's invalidation operation waits for remote cleanup. That is observable application behavior, as is the history omission. Neither finding establishes that the SFU is missing a capability.

Keep the repo's response isolation until tests identify a reason to change it. Persistent-track reuse or a new queue-flush API would require a separate demonstrated need; neither is an initial requirement.

Agree whether Pipecat should maintain the supported transport upstream or Cloudflare should maintain it as a package. The media and connection behavior need agreement before choosing where the package belongs.

## Existing PCM conversion and workload

The current SFU input converter uses a 63-tap filter for 16,000 output samples per second. That is approximately one million tap operations per audio second before loops and copies. Its output converter also runs synchronously in Python. These counts justify a measurement; they do not establish that Workers CPU use will exceed a limit. [Conversion code](../src/sfu_codec.py)

The [local benchmark](../audit/results/local-media-costs.json) measures only that PCM path. It includes no network, provider execution, language-boundary calls, or deployed Worker. Its result cannot estimate how many calls a Worker can handle.

Raw provider audio at 16 kHz mono input plus 24 kHz mono output uses 80,000 bytes per second combined. The SFU adapter's 48 kHz stereo PCM uses 384,000 bytes per second across both directions. These figures exclude framing and describe traffic, not retained memory. Queue limits must cover both bytes and the duration of waiting audio.

With 20 ms packets, each direction supplies 50 frames per second. Conversion, copying, provider work, and conversation tasks must keep pace while cancellation stays responsive. Measure long answers with muted or silent input under the actual runtime accounting rules. A higher CPU allowance cannot make a pipeline keep pace if processing is slower than arriving audio.

## Completion criteria

Follow the [implementation plan](IMPLEMENTATION-PLAN.md): establish the reference configuration, integrate hosted turn detection, restore Pipecat context behavior, and verify both transports and cleanup. Request the supported package once its required components are known. Deployment acceptance still requires failure/recovery, physical audio, and workload evidence.

The [acceptance plan](ACCEPTANCE.md) specifies the observations needed to change each conclusion. Agree performance limits before judging results. Passing an existing regression test or importing a package cannot complete a live capability check. A missing capability claim must identify the required operation and the reproducible failure; application defects should not become platform requests without that evidence.
