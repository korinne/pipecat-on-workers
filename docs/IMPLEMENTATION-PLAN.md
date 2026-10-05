# Implementation order and coding session scope

Use the [goals](GOALS.md) as the scope boundary. The target is one Workers AI voice configuration using GPT-OSS-120B and Pipecat's existing conversation behavior through direct WebSocket and SFU. The plan has six tasks. Task 1 established the reference; Task 2 now has an implementation candidate for Nova-3 and hosted Smart Turn. The selected GPT-OSS and standard assistant-output/context path remain Task 3 work. No complete Workers support claim has been established.

## Before starting a coding session

Review GOALS, [conversation behavior](CONVERSATION.md), and [transport integration](TRANSPORTS.md) together. Then review the relevant scenarios in the [acceptance plan](ACCEPTANCE.md). Preserve the current handoff on a reviewed branch or commit so every session starts from an identifiable state; the original application baseline is `6c17c0805f13f7609ba0a93ea8bf4c945797de18`.

Each fresh session gets a short brief naming:

- The exact checkout and starting revision, including any intended uncommitted changes.
- One numbered task below, its allowed changes, and its dependencies.
- The relevant documents and tests, with the intended observations.
- Its completion condition and any decisions still needed.

Finish that task and report the changed behavior, test evidence, and remaining limitations. Do not automatically start the next task. A necessary local implementation detail is within scope; changing the supported configuration or adding a product feature requires a separate decision.

## 1 Establish the reference configuration

This is a bounded compatibility investigation. Select one Workers AI STT configuration to pair with hosted Smart Turn, using Nova-3 as the first candidate. Use GPT-OSS-120B for the LLM and keep Aura-2 for TTS. If evidence prevents their use, propose a configuration change for review before adopting a replacement. Identify a speech-start signal, audio formats, and the point at which transcript text is ready for the next model request.

Inspect the chosen Pipecat version's normal TTS, output transport, and assistant aggregator flow. Record what enters context on a complete answer, an interruption, and a synthesis failure. A controlled reference test should exercise the same relevant text/audio events and ordering as the intended integration. A synthetic test that sends model tokens directly to an aggregator cannot establish normal speech-output behavior.

Verify the selected GPT-OSS binding request and streamed response, including answer/reasoning separation, output budget, completion, and cancellation. Record whether the existing LLM parser needs adaptation; do not assume changing its model identifier is enough. Use the focused [AI2 checks](ACCEPTANCE.md#ai2-stream-gpt-oss-answers-into-speech), without starting a wider model comparison.

Check the dependency and startup requirements of those components and the hosted Smart Turn client. The old audit covers a smaller module set. Do not assume its package request is the complete dependency list for this configuration, or that hosting Smart Turn removes all local VAD/client requirements.

Done when: one reference configuration, its supported behavior, and its reproducible Workers compatibility gaps are recorded. If a required component is blocked, report the gap and the proposed narrow adapter or upstream change. Do not build a replacement history policy, conduct a broad model comparison, or silently fall back to Flux-only turn detection.

Task 1 investigation recorded on 4 October 2026 (results use UTC timestamps on 5 October). The [reference configuration](GOALS.md#task-1-reference-configuration) selects Pipecat 1.11.0, Nova-3 with hosted Smart Turn v2, GPT-OSS-120B with low effort as the initial probe setting, and Aura-2 Luna. Controlled upstream speech/output and turn probes, parser fixtures and an expanded runtime audit are recorded in [Evidence](EVIDENCE.md#task-1-reference-investigation). Task 1 changed no production behavior. Authentication prevented live inference checks, and no Workers package candidate has passed.

Task 1 identified the async hosted-turn connection, Nova event mapping and a revision-aware stop strategy implemented in Task 2. Live request verification still needs restored test access; a failed authentication check cannot establish model or runtime incompatibility.

## 2 Connect hosted Smart Turn to Pipecat

Use Workers AI for the chosen STT and Smart Turn. Feed the model the agreed audio and route its decisions through Pipecat's turn strategy. Reuse existing strategy behavior for coordinating speech activity, completion, and transcription; add only the Workers-compatible asynchronous client or adapter that the reference check shows is needed.

Replace the existing Flux-controlled turn path as required by the selected configuration. Avoid two independent end-of-turn triggers or a hidden requirement to wait for Flux after Smart Turn has finished. Review the prototype's extra turn-end grace period rather than carrying it forward without evidence.

Done when: tests cover a mid-thought pause, completed speech, speech resuming while a decision is pending, late transcription, stale detector results, and bounded detector failure. A completed user turn triggers one intended response with the correct transcript. Record live Workers AI evidence separately from fixtures.

Outside this task: assistant-history redesign, speech models beyond task 1's recorded selection, local inference, and a general provider abstraction. Keep the existing response path until the next task changes it.

Task 2 implementation candidate: [the turn adapter](../src/smart_turn.py) maps Nova speech onset and pause events into Pipecat, calls hosted Smart Turn through `BaseTurnAnalyzer`, and commits a finalized transcript through the standard user aggregator only while the candidate revision remains current. Flux completion and the 1,200 ms grace period have been removed. The existing Llama/Aura response path and receipt-dependent assistant history remain unchanged.

The implementation has a five-second pause-to-readiness deadline, a two-second hosted binding wait, and at most one unresolved hosted request per provider instance. An incomplete decision waits for resumed speech; failure or missing readiness discards the pending turn and asks the user to repeat it. These are bounded test settings, not latency targets. The transcript-range audio snapshot and final-transcript coverage rule have the timing limits described in [Conversation](CONVERSATION.md#task-2-user-turn-coordination).

Task 2 implementation and controlled verification are complete: 58 Python tests, 62 JavaScript tests and the supporting offline checks passed. A final focused rerun covers the retained audio window and decision diagnostics. See [the scoped results](EVIDENCE.md#task-2-turn-integration). The [fresh access check](../audit/results/task2-live-access.json) could not refresh existing authentication, so actual Nova events, hosted input acceptance, Python Workers execution and physical audio remain untested. The next bounded implementation is Task 3. Starting it requires a separate task instruction.

## 3 Connect GPT-OSS and standard assistant context

Implement the GPT-OSS service connection verified in task 1 and connect the existing Pipecat assistant aggregator to the selected TTS/output flow. Keep reasoning and other non-answer events out of spoken output and ordinary assistant dialogue. Reuse standard components where they work; adapt Workers AI provider calls and output events only where necessary. Preserve the reference behavior established in task 1, including text granularity and ordering during interruption.

Replace the prototype's browser-receipt history writer, remove conflicting prompt instructions, and persist committed context before the next model request uses it. Browser receipts may still be needed for flow control and stale-report checks. Remove duplicate history writes without discarding those controls.

Choose an explicit handling rule for old saved sessions. Missing historical SFU answers cannot be reconstructed. Tests must cover the chosen migration or fresh-session behavior, including removal of obsolete system instructions.

Done when: the selected LLM passes AI2 request/stream, answer separation, completion/error, and cancellation checks, with live Workers AI evidence distinguished from fixtures; complete, interrupted, and failed turns produce the agreed reference context through both existing routes; old output cannot enter the next reply; reconnect restores committed context without duplicates. Any difference from the reference must be explained and accepted before it is called supported.

Outside this task: a permanent delivery ledger, retaining all generated tokens regardless of speech progress, routine playback-unknown prompt notes, and exact-word browser receipts. Add none of these to make an obsolete acceptance expectation pass.

## 4 Complete transport and cleanup integration

Keep the conversation behavior shared across direct WebSocket and SFU, using Pipecat transport interfaces where practical. Extract only what those two paths need. Connection setup, audio conversion, and media resource ownership belong in the appropriate adapter.

Resolve the observed SFU cleanup delay after old output is safely isolated. Own cleanup tasks and resource identifiers; bound waiting and retries; handle late allocations and failed closes. A timeout must not be reported as successful remote cleanup. Preserve the separation between old and new output while making the next turn able to proceed.

Transport extraction and cleanup ordering can share one session because they affect the same ownership boundaries, but keep the changes separately reviewable.

Done when: held-cleanup tests permit the next turn without stale output, repeated interruptions stay isolated, queues remain bounded, and reconnect/End release resources or expose unresolved cleanup. Browser stop timing still needs live verification in task 6.

Outside this task: a public transport plugin API, persistent-track optimization, and transport switching during a call.

## 5 Integrate a supported package candidate

The package request can proceed alongside tasks 2 through 4 once task 1 identifies the required components. Package layout is an upstream implementation choice. The requirements are a supported installation, imports limited to the selected features, and startup compatible with Workers execution. Optional extras alone cannot remove mandatory base dependencies.

When a candidate exists, verify normal dependency resolution and the package actually loaded. Follow the [candidate audit instructions](DEVELOPMENT.md), extend the exercised component set to the selected pipeline, and validate it on actual Workers. Do not inject fake modules or let vendored source mask a broken candidate.

Done when: the selected application runs from the supported artifact without copied or locally edited Pipecat source. If no candidate is available, B1 remains open. Prototype improvements may continue, but the release cannot be called supported.

## 6 Verify the release configuration independently

Use a fresh verification session against the exact application/package revision being proposed for support. Run B1 through B6 for both transport routes. Fixes found during verification need a bounded implementation change and the relevant checks rerun against the resulting revision.

Agree the device/network conditions and numeric limits before judging measurements. Exercise real microphones, browser output, hosted Smart Turn, interruptions, follow-ups, failures, reconnect, and End. Inspect model inputs as well as conversation outcomes. Test call duration, concurrency, provider capacity, queue growth, cleanup, latency, and total cost. Use platform observations for remote media resources where available.

Verification task done when: each requirement has a result for the declared configuration, including explicit failure/untested status where applicable. The release earns support only when B1 through B6 pass. A mock, a passing import, or a server clear event cannot substitute for its corresponding live check. Unmet release criteria remain visible; optional features are not added as a substitute for evidence.

## How to use the existing evidence

The [local runner](DEVELOPMENT.md) retains the baseline diagnostic probes. Its older generated-context checks expose receipt-dependent history; they do not prescribe retaining every generated word after interruption. The original results retain their old checklist entries; the current runner lists outstanding checks for the agreed scope. Preserve those result files and add new results for the agreed behavior when implementing it.

Use [the evidence map](EVIDENCE.md) to separate application bugs from package/runtime gaps. Report a capability as required only when the selected configuration needs it and a reproducible check identifies the missing behavior.
