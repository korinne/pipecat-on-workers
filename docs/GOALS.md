# Pipecat on Workers goals and scope

Run one defined Pipecat voice application on Cloudflare Workers, using Workers AI for speech recognition, hosted Smart Turn, GPT-OSS-120B language generation, and Aura-2 speech synthesis. Use Pipecat's existing conversation and interruption behavior, with the adapters needed for Workers and the selected audio transports.

The first supported configuration covers direct browser WebSocket audio and WebRTC through Cloudflare Realtime SFU. A developer chooses the transport when creating a call. The conversation code and model configuration stay shared.

The code remains a feasibility prototype with copied, modified Pipecat source. The Task 2 hosted Smart Turn adapter passes controlled tests and still needs live verification. Standard assistant history and supported package installation remain unimplemented.

## What runs where

Pipecat is the framework running in the Python Worker. It coordinates the services and the conversation. Workers AI hosts the models that the application calls; their weights and inference do not run inside the Worker process. A Durable Object owns each call's application state.

The name Pipecat appears in two roles: the framework coordinates the call, while the hosted Smart Turn model estimates whether a person has finished speaking.

| Job | Intended baseline | Existing prototype |
| --- | --- | --- |
| Speech to text | Workers AI Nova-3 streaming with separate Smart Turn; selected reference, live verification pending. | Task 2 selects Nova-3; live request acceptance remains untested. |
| End of user turn | Workers AI `@cf/pipecat-ai/smart-turn-v2`. | Task 2 connects an async hosted analyzer and guarded Pipecat stop strategy; controlled tests pass. |
| Generate an answer | Workers AI `@cf/openai/gpt-oss-120b`. | Uses `@cf/meta/llama-3.3-70b-instruct-fp8-fast`; GPT-OSS is not integrated or tested here yet. |
| Text to speech | Keep Workers AI `@cf/deepgram/aura-2-en` for the initial English voice configuration. | Already configured. |
| Conversation history | Pipecat's assistant aggregator, integrated with the selected speech and output path. | Custom browser completion reports write history; the SFU route omits assistant answers. |

Task 1 selects Nova-3 as the reference STT pairing. Its live Workers AI behavior remains untested because the available authentication could not be refreshed. [Request candidates and checked input schemas](../audit/reference/turn-sources.json) preserve that boundary. The selection below is an implementation target, not a compatibility pass. If a reproducible limitation prevents the selected LLM or TTS from working, record it and propose the smallest configuration change for review. Do not adopt a replacement automatically.

Sources: [current providers](../src/providers.py), [hosted Smart Turn](https://developers.cloudflare.com/workers-ai/models/smart-turn-v2/), [Nova-3](https://developers.cloudflare.com/workers-ai/models/nova-3/), [Flux](https://developers.cloudflare.com/workers-ai/models/flux/).

The selected LLM is Cloudflare-hosted GPT-OSS-120B. Its weights and inference stay on Workers AI. Verify the streaming binding response and map only user-facing answer text into speech and assistant context. Start by testing low reasoning effort; record the accepted setting and output budget, then measure answer quality and time to the first speakable answer. This selection does not establish compatibility with the prototype's current parser or validate its earlier Llama results for GPT-OSS. [Model documentation](https://developers.cloudflare.com/workers-ai/models/gpt-oss-120b/), [LLM acceptance](ACCEPTANCE.md#ai2-stream-gpt-oss-answers-into-speech)

## Task 1 reference configuration

The reference uses published Pipecat 1.11.0, corresponding to upstream `3dede06bec0b497bddfdcf047af7495ee9d0726e`. Its source archive SHA-256 is `49e7532a1f035e8884c47448a06d998a1b7f0943d11eae9bc8600a9e749a2a04`. Task 1 installed that artifact with normal dependency resolution in a separate CPython 3.12.14 environment. It did not use the application's vendored package. Python Workers 3.14 remains the deployment target and is untested with this full configuration.

```text
Workers input adapter → Workers AI STTService → LLMContextAggregatorPair.user()
 → Workers AI LLMService → Workers AI TTSService → BaseOutputTransport adapter
 → LLMContextAggregatorPair.assistant()
```

Names prefixed “Workers AI” describe adapters to implement against the named Pipecat interfaces. They are not existing upstream provider classes. The controlled speech reference uses upstream `DeepgramHttpTTSService` with a simulated HTTP response and a `BaseOutputTransport` sink. Its text/audio ordering is the target for the Aura binding adapter. No Deepgram-hosted inference or local model is selected.

| Part | Pinned reference choice | Evidence limit |
| --- | --- | --- |
| Call owner | One Python Durable Object, host-owned event loop, embedded `PipelineWorker` | Full upstream pipeline has not run in Workers. |
| Recognition | Workers AI `@cf/deepgram/nova-3`, streaming WebSocket; `encoding=linear16`, `sample_rate=16000`, `channels=1`, `language=en-US`, `interim_results=true`, `vad_events=true`, `endpointing=200` ms; no separate `UtteranceEnd` trigger | These are initial test settings. Workers acceptance of the complete request and observed event timing need a live check. |
| Speech onset | Nova `SpeechStarted` mapped to `VADUserStartedSpeakingFrame`; explicit `VADUserTurnStartStrategy` | No local Silero/ONNX VAD. Missing speech-start events are a failure to investigate, not permission to fall back to transcript-only turn completion. |
| Pause and transcript | `speech_final` marks a pause/finalized segment; accumulate distinct `is_final` segments by provider time range; interims replace interim display only | A pause never independently starts an answer. Deduplication, empty final messages, and finalization across pauses need adapter tests. |
| Semantic completion | Workers AI `@cf/pipecat-ai/smart-turn-v2`, through an asynchronous `BaseTurnAnalyzer` adapter; `TurnAnalyzerUserTurnStopStrategy(wait_for_transcript=True)` is the standard coordination reference | The stock HTTP analyzer and strategy require the narrow adaptations recorded below. Defaults that instantiate local Smart Turn v3 are excluded. |
| Answer generation | Workers AI `@cf/openai/gpt-oss-120b`, `LLMService` interface; request low reasoning effort initially | Exact request, stream and budget evidence is in [Evidence](EVIDENCE.md#task-1-gpt-oss-interface). Reasoning never enters speech or ordinary dialogue. |
| Synthesis | Workers AI `@cf/deepgram/aura-2-en`, `speaker=luna`, `encoding=linear16`, `container=none`, 24 kHz mono; `TTSService`, sentence aggregation, `push_start_frame=True`, `push_stop_frames=True`, `push_text_frames=True`, no word timestamps | Standard HTTP Aura reference uses `DeepgramHttpTTSService` / `aura-2-luna-en`. Binding parity and failure handling still need implementation. |
| Output | `BaseOutputTransport`, audio enabled, mono 24 kHz, 20 ms writes (`audio_out_10ms_chunks=2`), no video/mixer | Real adapters must implement pacing, backpressure and stale-output isolation. A successful write is not physical playback. |
| Context | `LLMContext`, `LLMContextAggregatorPair`, `LLMUserAggregator`, `LLMAssistantAggregator`; assistant after output | [Conversation](CONVERSATION.md#task-1-speech-reference) records the actual granularity and failure behavior. |

Use PCM16 little-endian mono at 16 kHz between input, STT and the turn buffer. The Smart Turn request candidate converts samples to little-endian float32 in `[-1, 1)`, then base64-encodes the bytes with `dtype="float32"`. Keep at most the latest eight seconds plus the chosen 200 ms onset pre-roll before taking an eight-second analysis snapshot. This follows the pinned model's 16 kHz processing and the base analyzer's eight-second window; the hosted model's accepted encoding/window still needs a live check. Its published schema does not specify a sample rate or maximum duration. Read `is_complete` as the decision and retain `probability` for diagnostics. Do not reinterpret a request failure as `false`.

Direct browser audio remains 16 kHz mono input and 24 kHz mono output. The SFU boundary remains 48 kHz stereo PCM with 20 ms packets, converted by the existing adapter. These formats describe one shared pipeline. No transport selects a separate history policy. [Nova-3 options](https://developers.cloudflare.com/workers-ai/models/nova-3/), [Smart Turn input schema](https://developers.cloudflare.com/workers-ai/models/smart-turn-v2/schema-input.json), [model preprocessing](https://huggingface.co/pipecat-ai/smart-turn-v2/blob/2f16664b2769b748c7b9b857d34a7a55f228064d/preprocessor_config.json), [Aura options](https://developers.cloudflare.com/workers-ai/models/aura-2-en/)

## How a user turn starts and ends

Three signals answer different questions:

| Signal | Meaning |
| --- | --- |
| Speech activity | The person has started speaking, which can interrupt the assistant, or has paused. |
| Smart Turn decision | The pause appears to end the person's thought. |
| Transcription readiness | The words needed for the next model call have arrived. |

Use the explicit strategy pair above. The standard strategy accepts either ordering of semantic completion and a finalized transcript. The [controlled probe](../audit/results/task1-turn-verified.json) produced one inference trigger and one completed turn in both cases; a repeated final transcript did not produce a second trigger after completion. This is controller evidence with a simulated analyzer, not a transcript or live microphone test.

The adapter contract for Task 2 is:

1. On `SpeechStarted`, invalidate any pending pause decision, interrupt obsolete output, and mark speech active before delivering transcript frames. Track a call/utterance revision independently of the response generation.
2. A Nova pause snapshots audio ending at its transcript-range cursor, rather than the ring buffer tail when the event arrives. This cursor is not a guaranteed processed-audio or trailing-silence watermark. Align `SpeechStarted` using the provider timestamp and retained pre-roll. Its final text belongs to that pause's transcript range. This event/audio alignment remains unverified; delayed onset must not let resumed speech enter an older snapshot. An `is_final` segment alone must not mean the complete user turn is ready. Empty `speech_final` messages may finalize earlier accumulated text without adding duplicate words.
3. Submit one hosted Smart Turn check for that pause. Resume, End and connection replacement invalidate its revision. An old result cannot alter the current completion state or emit inference, even if a Python task could not cancel the remote work.
4. Start one answer only when the current pause is semantically complete, its transcript coverage is finalized and nonempty, and speech has not resumed. Mark the revision consumed before emitting the context frame. An incomplete decision keeps the accumulated transcript for resumed speech.
5. Bound provider and transcript waits. For initial Task 2 tests, use a two-second detector deadline and a five-second finalization/recovery deadline from the pause. Failure abandons the pending user turn, reports the error and requires repetition or a fresh turn. Invalidate any pending watchdog and transcript callback so neither can later emit context for that turn. It does not answer a partial transcript. These are proposed recovery settings, not measured latency targets.

The stock strategy's p99 timer may release non-finalized text, and the controller's five-second watchdog can finalize a turn even after an INCOMPLETE result. A controller-level concurrency probe also emitted inference from a stale detector result while speech was active; the later stop event was rejected. Task 2 addresses these behaviors with revision-aware completion and explicit timeout handling through a narrow stop strategy. Its controlled tests exercise actual Pipecat frame scheduling. Preserve the standard user/assistant aggregators and their event interfaces. Merely swapping in an asynchronous analyzer would leave these cases unresolved. The [Task 2 results](EVIDENCE.md#task-2-turn-integration) extend that evidence to local frame queues; neither probe establishes that a deployed call has hit the race.

There must be one coordinated decision to start the next answer. Flux and Smart Turn must not independently trigger responses. If Flux is retained, its transcript and turn events need explicit integration with Smart Turn; merely adding a second detector is insufficient. Delayed detector results must not end a newer turn, and detector failure must have a bounded recovery outcome. [Pipecat turn detection](https://docs.pipecat.ai/pipecat/learn/speech-input)

## Required behavior

These outcomes define the release target for the selected configuration. Each result must identify the source revision, package, runtime, models, transport, and test conditions.

| ID | Behavior | Evidence needed |
| --- | --- | --- |
| B1 | A developer can install and start it | A supported package installs normally and runs the selected pipeline on actual Workers without copied or locally patched Pipecat source taking precedence. |
| B2 | A person can have a conversation | Real microphone audio reaches Workers AI STT and hosted Smart Turn; one intended model response reaches the browser through TTS. Test pauses, resumed speech, delayed transcripts, and detector failure. |
| B3 | Follow-ups retain conversation context | The next model input and saved context match the selected Pipecat speech/output behavior for completed, interrupted, and failed turns. Ordinary completed answers remain available through both transports. |
| B4 | A person can interrupt and continue | Old work and audio stop within the agreed delay; stale output stays stopped; the next turn can proceed once old output is safely isolated, even while remote cleanup is delayed. |
| B5 | Calls end and recover predictably | Calls do not mix state. Reconnect follows the agreed recovery behavior, restores committed context, and creates fresh live resources. Cleanup finishes or reports owned resources still needing reconciliation. |
| B6 | The workload is practical | Agreed duration and concurrency meet measured CPU, memory, queue, latency, provider-capacity, and cost limits on the intended deployment. |

The baseline has partial evidence and known application gaps. It has no complete acceptance result for this target. [Evidence](EVIDENCE.md) distinguishes observations from requirements.

## Production requirements and optional features

Production requires enough state to identify the current response, reject canceled work, handle errors, and diagnose failures. Reuse Pipecat state and events where they suffice. Preserve committed conversation context for the promised reconnect behavior.

The initial scope does not require a separate permanent record of every generation, audio submission, and playback state. It also does not require routine playback-uncertainty notes in model prompts, retention of every generated word after an interruption, or exact-word browser playback receipts. Those are additional product features, not prerequisites for using Pipecat. [Conversation behavior](CONVERSATION.md) explains the distinction and required recovery tests.

## Limits on the initial work

- Support one declared English voice configuration, with models hosted on Workers AI.
- Implement the two existing transport routes using Pipecat interfaces where practical. Extract only the shared adapter code needed by those routes.
- Do not build a general transport plugin system, switch transports during a call, or promise exact-position audio resumption.
- Do not add local model inference, video, support for every provider SDK, or model comparisons beyond the bounded STT selection.
- Reusing persistent SFU tracks is an optional optimization. It is not required to fix cancellation and cleanup ordering.

If a selected component cannot run in Workers, record the smallest reproducible failure and the capability needed. A substitute or workaround must remain visible as a deviation; it cannot quietly become evidence that the original target works.

## Decisions still needed before their dependent work

| Decision | When it must be settled |
| --- | --- |
| Nova-3 event/encoding verification and the proposed detector/finalization deadlines | At the start of Task 2, before its live integration is called working. |
| Observable TTS/output text granularity and failure/reconnect behavior | Before writing context acceptance expectations. |
| Supported devices and network conditions, including headphones versus speakerphone | Before live voice and interruption acceptance. |
| Latency, call duration, concurrency, resource, queue, cleanup, and cost targets | Before performance acceptance. Nulls in the [resource contract](../audit/acceptance/resource-contract.json) mean undecided. |
| Supported Pipecat candidate artifact and version | Before claiming B1 or accepting the final release revision. |

The [implementation plan](IMPLEMENTATION-PLAN.md) gives each coding session an outcome and stopping point. Investigate only the decisions needed for that task. Historical reports and old tests do not add release requirements to this scope.
