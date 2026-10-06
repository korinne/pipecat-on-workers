# Standard speech output and committed context

The shared pipeline is `HostedUserAggregator → WorkersLLMService → WorkersTTSService → WorkersOutputTransport → PersistedAssistantAggregator`. The application uses the pinned Pipecat 1.11.0 service bases, sentence aggregator, output queues and assistant aggregator. Workers AI supplies GPT-OSS-120B, Aura-2 Luna, Nova-3 and hosted Smart Turn v2. The direct WebSocket and SFU adapters carry the same speech frames and context behavior.

This remains a vendored prototype. The dependency and compatibility changes are disclosed separately; the [published-package investigation](PACKAGE-CANDIDATE.md) leaves B1 open.

## Speech and interruption

Aura receives sentence text through `TTSService` with start, stop and text frames enabled. It produces PCM16 at 24 kHz mono. `BaseOutputTransport` writes 20 ms chunks with no added end silence, followed by the normal text and lifecycle frames. The assistant aggregator commits the text that reaches it. It receives no raw model-token shortcut and no playback receipt.

The controlled tests preserve the Task 1 outcomes: completed sentences enter context; interruption during a held first write or before the sentence text reaches output retains no first sentence; interruption during the next sentence retains the first. The reference's 10 ms tail behavior also remains: its text can enter context before its padded output write completes. This is sentence/text-frame progress, not a claim about words heard.

Direct browser receipts release bounded queue credit. SFU receiver readiness controls media setup. Neither event writes assistant history. Generation and TTS context identifiers reject obsolete text and audio, including callbacks arriving after a newer response starts.

## Failure policy

A model error, empty answer, exhausted token budget or unexpected stream end aborts the response visibly. Reasoning and protocol markers never enter speech or ordinary assistant context. See [GPT-OSS](GPT-OSS.md) for actual stream evidence and cancellation limits.

Synthesis failure aborts before the TTS base forwards the failed sentence's text. Earlier text already progressed through output remains eligible for the standard assistant commit. This is an explicit service-adapter change from the reference's nonfatal failure default. Empty or malformed PCM is a synthesis failure. Output write rejection, timeout or completion failure also aborts visibly. They cannot produce a successful listening event for that response.

The next user commit waits for the standard interruption commit and persistence barrier. Persisted context uses schema 2 and the canonical system prompt. Old sessions without that schema start with fresh dialogue because missing SFU answers cannot be reconstructed. Valid schema-2 sessions restore only user and assistant messages, bounded to the last 80; obsolete saved system prompts are discarded. Call tokens and cleanup ownership are retained independently of dialogue migration.

Persistence failures are tested separately. Context must be acknowledged by storage before another model request uses it; exhausted or unresolved writes end the call visibly and restore the last acknowledged context. Cleanup still runs when the final save fails.

## Bounds and evidence

The application reserves at most 384,000 bytes of queued 24 kHz PCM, limits answer text to 16,384 characters and bounds direct receipt waits and output completion at 12 seconds. The base output write timeout is 40 seconds to cover the SFU adapter's bounded setup. These are implementation safety bounds, not approved performance targets.

`tests/test_speech_pipeline.py` uses the real standard components and both real application adapters with controlled provider/media leaves. It covers completed context without playback receipts, exact follow-up input, Task 1 interruption points, model and synthesis failures, old-session migration, stale TTS contexts, failed output completion and output timeouts. Packetization, failure and SFU conversation regression tests now assert that same behavior. Local Python Workers lifecycle checks have also exercised the pipeline with fixture providers. Live provider and physical playback results are recorded separately in the final verification evidence.

The user requested laptop testing and deferred performance acceptance. No capacity, soak, latency-budget or cost acceptance is inferred from these bounded checks.

The follow-up review reproduced retired model, synthesis and output-completion failures that emitted another error and advanced the active generation. Each failure callback now checks the generation it belongs to. A model stream's cleanup error during cancellation also preserves the interruption. Ten added cases fail against the preceding application and pass after the fix on both adapters; the full speech suite passes 48 checks. The before and after reports are `audit/results/task3-stale-failures-before.json` and `audit/results/task3-stale-failures-fixed.json`.
