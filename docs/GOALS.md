# Pipecat on Workers goals and scope

Run one defined Pipecat voice application on Cloudflare Workers, using Workers AI for speech recognition, hosted Smart Turn, GPT-OSS-120B language generation, and Aura-2 speech synthesis. Use Pipecat's existing conversation and interruption behavior, with the adapters needed for Workers and the selected audio transports.

The first supported configuration covers direct browser WebSocket audio and WebRTC through Cloudflare Realtime SFU. A developer chooses the transport when creating a call. The conversation code and model configuration stay shared.

The code remains a feasibility prototype with copied, modified Pipecat source. Hosted Smart Turn, standard assistant history, and supported package installation still need implementation and verification.

## What runs where

Pipecat is the framework running in the Python Worker. It coordinates the services and the conversation. Workers AI hosts the models that the application calls; their weights and inference do not run inside the Worker process. A Durable Object owns each call's application state.

The name Pipecat appears in two roles: the framework coordinates the call, while the hosted Smart Turn model estimates whether a person has finished speaking.

| Job | Intended baseline | Existing prototype |
| --- | --- | --- |
| Speech to text | One streaming Workers AI STT model. Evaluate Nova-3 first for use with separate Smart Turn. | Workers AI Deepgram Flux. |
| End of user turn | Workers AI `@cf/pipecat-ai/smart-turn-v2`. | Flux supplies turn events; hosted Smart Turn is not connected. |
| Generate an answer | Workers AI `@cf/openai/gpt-oss-120b`. | Uses `@cf/meta/llama-3.3-70b-instruct-fp8-fast`; GPT-OSS is not integrated or tested here yet. |
| Text to speech | Keep Workers AI `@cf/deepgram/aura-2-en` for the initial English voice configuration. | Already configured. |
| Conversation history | Pipecat's assistant aggregator, integrated with the selected speech and output path. | Custom browser completion reports write history; the SFU route omits assistant answers. |

Nova-3 is an evaluation candidate, not a completed selection or compatibility result. Record one STT choice, model identifiers, voice, audio formats, and Pipecat version before implementation depends on them. If a reproducible limitation prevents the selected LLM or TTS from working, record it and propose the smallest configuration change for review. Do not adopt a replacement automatically.

Sources: [current providers](../src/providers.py), [hosted Smart Turn](https://developers.cloudflare.com/workers-ai/models/smart-turn-v2/), [Nova-3](https://developers.cloudflare.com/workers-ai/models/nova-3/), [Flux](https://developers.cloudflare.com/workers-ai/models/flux/).

The selected LLM is Cloudflare-hosted GPT-OSS-120B. Its weights and inference stay on Workers AI. Verify the streaming binding response and map only user-facing answer text into speech and assistant context. Start by testing low reasoning effort; record the accepted setting and output budget, then measure answer quality and time to the first speakable answer. This selection does not establish compatibility with the prototype's current parser or validate its earlier Llama results for GPT-OSS. [Model documentation](https://developers.cloudflare.com/workers-ai/models/gpt-oss-120b/), [LLM acceptance](ACCEPTANCE.md#ai2-stream-gpt-oss-answers-into-speech)

## How a user turn starts and ends

Three signals answer different questions:

| Signal | Meaning |
| --- | --- |
| Speech activity | The person has started speaking, which can interrupt the assistant, or has paused. |
| Smart Turn decision | The pause appears to end the person's thought. |
| Transcription readiness | The words needed for the next model call have arrived. |

Pipecat should coordinate these signals through its existing turn strategies where compatible. Choose a speech-start signal supported by the selected STT or another verified mechanism. Hosted Smart Turn does not by itself establish that speech-start detection is solved.

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
| STT choice, speech-start signal, exact model options, and standard Pipecat reference configuration | Before the model and turn integration is treated as fixed. |
| Observable TTS/output text granularity and failure/reconnect behavior | Before writing context acceptance expectations. |
| Supported devices and network conditions, including headphones versus speakerphone | Before live voice and interruption acceptance. |
| Latency, call duration, concurrency, resource, queue, cleanup, and cost targets | Before performance acceptance. Nulls in the [resource contract](../audit/acceptance/resource-contract.json) mean undecided. |
| Supported Pipecat candidate artifact and version | Before claiming B1 or accepting the final release revision. |

The [implementation plan](IMPLEMENTATION-PLAN.md) gives each coding session an outcome and stopping point. Investigate only the decisions needed for that task. Historical reports and old tests do not add release requirements to this scope.
