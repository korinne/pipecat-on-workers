# Pipecat compatibility investigation

The current architecture and completion decision are in
[ARCHITECTURE-REVIEW.md](ARCHITECTURE-REVIEW.md). The detailed runtime findings
and historical test versions below remain part of the investigation.

**Access-key update:** the application verifies access before opening the
microphone and supports loading the supplied key text file directly. Missing,
incorrect, and unconfigured keys now produce distinct messages. The delivered
key and the browser file-loading flow were both verified successfully. See
[evidence/access-key-fix.json](evidence/access-key-fix.json). The key stays in
memory and is sent only in authentication headers; it is not saved in browser
storage or included in the source archive.

**Microphone troubleshooting update:** Start requests AudioContext resume
synchronously before awaiting authentication; microphone access still follows
key verification. The browser now shows a live sound meter and separate capture,
upload, server-received, and provider-forwarded audio counters, offers Resume
audio, and gives plain guidance for fresh and restored conversations. The user
reported speaking while the page showed Listening without receiving a transcript.
After the microphone deployment, the user confirmed “okay this is working!”
This is user-reported basic live success. The earlier failure's cause remains
unconfirmed, and measured duration/acoustic acceptance remains incomplete.

**Capacity update:** the capacity fix adds bounded startup retries, clear
provider-capacity errors, and immediate startup cancellation. See
[CAPACITY-FIX.md](CAPACITY-FIX.md) for its tested version and checks. Earlier
long-duration results below remain tied to their recorded deployment.

**Deployed verdict: works with restrictions.** The earlier validated version
`69b1b14d-e27b-4ebc-8623-33dc94d5c186` passed a 600.011-second actual-provider
run with two simultaneous Python Durable Object calls, 20 recordings, ten
interruptions and ten recoveries, one history reconnect, no reported errors,
and zero retained live resources. See [duration evidence](evidence/real-provider-soak-summary.json).
Recorded one-second pause and idle-cleanup checks also passed on that same
duration-tested version. Physical
microphone/speaker acceptance remains outstanding; the health endpoint's
`voice_validated: false` deliberately preserves that distinction.
Pending-tool cancellation passed on the duration-tested revision. The later
capacity-fix version `a0fddf09-a341-4b07-bd50-84adcc2ffed1` also passed [pending-model cancellation and spoken recovery](evidence/real-provider-model-cancellation-capacity-fix.json),
with no canceled audio after clear and zero owned-resource counters at End.
The earlier startup HTTP 429 failures remain preserved separately.

The spike pins **Pipecat 1.11.0**, the current release inspected on 2026-09-24. The
GitHub tag points to `3dede06bec0b497bddfdcf047af7495ee9d0726e`. Source is taken from
the published PyPI source archive, whose SHA-256 is
`49e7532a1f035e8884c47448a06d998a1b7f0943d11eae9bc8600a9e749a2a04`.
`scripts/vendor_pipecat.py` verifies that checksum, retains the BSD license, checks
every patch against its expected source shape, and writes a manifest with hashes.
The vendored code is upstream Pipecat with the edits below; it is not a replacement
pipeline implementing Pipecat-like classes.

The default source allowlist contains **119 Python modules, 1,027,709 bytes** before
bytecode and runtime objects. It is recorded from the exercised import graph and
checked into `scripts/pipecat_modules.txt`. The full archive has 625 Python
modules totaling about 7.29 MB, plus large model/data assets; these unrelated
features are not bundled. This byte count is source size, not a memory measurement.

## Verified source findings

| Area | Finding in pinned source | Narrow configuration |
| --- | --- | --- |
| Packaging | The base distribution requires ONNX Runtime, NumPy, Numba, soundfile, SoXR, resampy, loudness, Pillow, aiohttp, OpenAI, and others. Installing `pipecat-ai` normally brings these requirements even when their features are unused. | Vendor a verified subset of actual Python source and declare only its exercised dependency subset. This is a compatibility spike, not the supported upstream installation. |
| Eager native imports | `frames` imports VAD parameters; VAD imports `audio.utils`, which imports native audio dependencies. Text context imports Pillow. | Move unused native imports into their point-of-use functions. |
| VAD threading | `VADAnalyzer` constructs `ThreadPoolExecutor`; `analyze_audio` calls `run_in_executor`. | Do not instantiate upstream VAD analyzers. Use external turn signals. |
| HTTP Smart Turn | `HttpSmartTurnAnalyzer` inherits `BaseSmartTurn.analyze_end_of_turn`, which runs `_process_speech_segment` in a thread pool. Its synchronous `_predict_endpoint` bridges to a coroutine through `run_coroutine_threadsafe` and blocks on `future.result()`. | Do not use it. An async hosted-turn adapter must directly await the hosted service, never inherit that execution path. |
| Startup threading | `PipelineWorker._setup` unconditionally calls `asyncio.to_thread(warm_deferred_imports)`, warming NLTK. | Add an explicit `enable_import_prewarm=False` option. Default remains upstream behavior. |
| Optional RTVI | Importing worker/RTVI frame definitions eagerly imports RTVI observers, native transports, and LLM services, even with RTVI disabled. | Lazy-load public RTVI exports; defer observer/processor imports unless enabled or explicitly present. |
| Runner ownership | `WorkerRunner` installs process signal handlers by default and invokes `asyncio.to_thread(gc.collect)` on cleanup. | Run the real `PipelineWorker` directly with `WorkerParams(task_manager=TaskManager())` on the host event loop. |
| Frame cancellation | `InterruptionFrame` is a system frame. `FrameProcessor` cancels its current ordinary frame processing task and rebuilds its processing queue, preserving only explicit uninterruptible frames. | Await provider requests inside the processor's frame handler; propagate `CancelledError`. Invalidate remote results and browser audio separately. |
| Context | Real user/assistant aggregators write `LLMContext`. Assistant text is committed on response end or interruption, but Pipecat cannot infer what the browser actually played. | Only forward acknowledged playback text to the assistant aggregator, or reconcile it against acknowledgements before the next inference. Report the acknowledgement resolution. |

These are verified source behaviors. A successful CPython test alone does not
prove Workers compatibility, and neither a Python DO import nor synthetic
pipeline frames prove a real voice conversation.

## Compatibility edits

All edits are generated reproducibly and listed with original/patched hashes in
`src/pipecat/VENDOR_MANIFEST.json`:

1. `pipecat/__init__.py`: use the pinned source version when no installed
   `pipecat-ai` distribution metadata exists.
2. `audio/utils.py`: defer `audioop`, NumPy, loudness, and SoXR imports until a
   function requiring them is called. PCM WAV wrapping and scalar smoothing stay
   available without these dependencies. Unsupported functions still fail
   honestly if their dependencies are absent.
3. `processors/aggregators/llm_context.py`: defer Pillow until raw-image encoding.
4. `pipeline/worker.py`: make import prewarming optional and defer RTVI class
   imports when disabled. Explicit RTVI subclasses are detected via their class
   ancestry so manual RTVI wiring retains its behavior.
5. `processors/frameworks/rtvi/__init__.py`: retain the same public exports through
   lazy attribute resolution, allowing lightweight frame/model imports.
6. `turns/user_start/__init__.py`: defer strategy exports, including optional Krisp,
   whose eager optional import otherwise attempts NumPy loading.

The research-only `--all-python` mode also defers Pillow in native output image
resize; that module is absent from the default allowlist. The allowlist is a
deliberate limitation: arbitrary Pipecat providers/transports are not supported.

Lazy optional imports and an optional startup-prewarm setting are plausible
upstream changes. Source vendoring and a reduced dependency manifest are
spike packaging decisions; an upstream `core`/remote-only installation extra
would be preferable for a supported product. No VAD inference, queue ordering,
interruption algorithm, context aggregation, or cancellation implementation is
replaced.

Regenerate the narrow vendored source with `python scripts/vendor_pipecat.py`.
An offline build can pass `--archive /path/to/pipecat_ai-1.11.0.tar.gz`. To research
an expanded configuration, first run with `--all-python`, run
`python scripts/check_pipecat_core.py --record-modules scripts/pipecat_modules.txt`,
and review any additions before regenerating the default narrow bundle.

## Actual application wiring

The deployed application uses two Pipecat processors. Recognition and speech
transport are custom async I/O adapters around this pipeline:

```python
# From ConversationSession in src/conversation.py; session setup owns context,
# the task manager, and the lifetime of the worker.run() coroutine.
turn_strategies = ExternalUserTurnStrategies()
turn_strategies.stop[0].wait_for_transcript = False
user = LLMUserAggregator(context, params=LLMUserAggregatorParams(
    user_turn_strategies=turn_strategies,
    vad_analyzer=None,
    audio_idle_timeout=0,
    user_turn_stop_timeout=30,
))
processor = GenerateResponse(session)
worker = PipelineWorker(
    Pipeline([user, processor]),
    params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=24000),
    enable_rtvi=False,
    enable_turn_tracking=False,
    enable_import_prewarm=False,
    idle_timeout_secs=None,
    cancel_timeout_secs=3,
)
await worker.run(WorkerParams(task_manager=manager))
```

Flux callbacks enqueue transcription and proposed turn frames. `GenerateResponse`
awaits model/tool/TTS work inside its frame handler. WebSocket played receipts
update assistant context explicitly. SFU output has no played receipts and
currently adds no assistant history. The separate guarded core harness uses an
`LLMContextAggregatorPair` to investigate upstream assistant aggregation; that
harness topology should not be confused with the deployed application.

`PipelineTask` still exists in this release as a deprecated subclass alias of
`PipelineWorker`; the latter is the current name. Use the host's already running
asyncio loop in a Durable Object; do not invoke `asyncio.run()` there.

Feed `ProposedUserStartedSpeakingFrame` / `ProposedUserStoppedSpeakingFrame` to
the user aggregator. Its external strategy resolves the proposal and lets
Pipecat broadcast interruption. In contrast, direct `UserStartedSpeakingFrame`
means the emitter already announced the turn and already performed interruption.
The upstream default stop strategy waits for transcription, guarding late
transcript arrival. This application supplies transcription before the normal
stop proposal and sets `wait_for_transcript=False`, allowing a discarded empty
turn to close without waiting for text that will never arrive.

Custom processors must `await super().process_frame(frame, direction)` and
forward lifecycle/system frames. Keep generation work in the processing task or
explicitly cancel separately created child tasks. Provider requests, queued
output, browser playback, and context acknowledgement each need their own stale
generation checks: cancelling Python's current coroutine does not retract audio
already sent to the client. Cancelled requests may still consume provider work.

The core test also waits for `on_assistant_turn_stopped` before injecting the next
completed user turn. This matters: cancellation has reached the upstream LLM
processor before the downstream assistant context flush is necessarily finished.
Without that coordination, synthetic immediate turn injection can record the
next user message before the interrupted assistant message. The demo's custom
playback-receipt context path must enforce its own ordering.

Do not use Pipecat's native transports, Silero/local Smart Turn, built-in HTTP
Smart Turn, image/audio attachments in LLMContext, NLTK-based TTS segmentation,
native resampling, DTMF playback, or its thread-backed signal-owning runner in
this configuration. Text tools can be represented through normal Pipecat context
and function frames; application code must cancel or invalidate in-flight tool
results on interruption.

## Core verification

`python scripts/check_pipecat_core.py` creates actual Pipecat pipeline, context,
and external turn strategies. It rejects native/unused-provider imports and
thread creation, injects synthetic turn/transcript frames, interrupts a pending
generation, checks that the old response does not finish, completes another
turn, checks context, and asserts no live Pipecat tasks after cancellation.
Its cancellation timing is server-side CPython scheduling time with synthetic
frames, not network latency or audible interruption-to-silence time.

The current review reran **41 Python tests with the existing ten additional
subtests**, **50 browser tests**, **ten SFU entry checks**, **seven entry lifecycle
checks**, **22 authentication checks**, and **12 harness tests**. See
[evidence/review-checks.json](evidence/review-checks.json) for the source revision. These include the
microphone diagnostics alongside turn recovery, cancellation, receipt behavior,
and access handling. No physical-voice or latest deployed-duration result is
inferred from these offline passes.

## Selected runtime and local duration validation

The delivered configuration selects **CPython 3.14.2 / Pyodide 314.0.6** inside
workerd 1.20260923.1, through `python_workers` and compatibility date
`2026-09-24`. The project requires Python >=3.14,<3.15 and pins Pydantic 2.12.5
(core 2.41.5 in the lockfile), workers-py 1.17.4, and workers-runtime-sdk 1.9.0.
Local CPython 3.14.7 and the Pyodide 314.0.7 package-building interpreter are
separate from the Worker runtime. Direct Worker observations are recorded in
[runtime evidence](evidence/runtime-versions-direct.json).

The [exact workerd runtime map](https://github.com/cloudflare/workerd/blob/v1.20260923.1/build/python_metadata.bzl)
selects bundle `314.0.6_2026-08-17_6` for `python_workers_314`, which
`python_workers` enables with compatibility date 2026-09-08 or later.
The [exact-tag patch selector](https://github.com/cloudflare/workerd/blob/v1.20260923.1/src/pyodide/tools/patch_pyodide_asm.ts)
applies the [upstream stack-switch GC fix](https://github.com/pyodide/pyodide/pull/6466)
to this Pyodide version. The earlier Python 3.13.2 runtime used Pyodide 0.28.2;
its separately installed 0.28.3 package-building interpreter did not identify
the Worker runtime.

Two ten-minute local trials passed with four actual Python 3.14 Durable Objects
and synthetic providers:

| Trial | Duration | Completed turns | Explicit playback interruptions | Cleanup after End |
| --- | --- | --- | --- | --- |
| Fresh process, no controlled restart | 600.098 s | 500 | 164 | Zero Pipecat tasks or pending audio receipts |
| Fresh process, public SDK restart lifecycle test, then duration trial in the same process | 600.000 s | 496 | 164 | Zero Pipecat tasks, live fixture generations/syntheses, or pending audio receipts |

See the [clean baseline](evidence/workerd-soak-py314-no-restart.json),
[SDK lifecycle results](evidence/workerd-lifecycle-py314-sdk.json), and
[duration trial after SDK restart](evidence/workerd-soak-py314-after-sdk-restart.json).
Neither trial used a debugger or source reload during the run. The promoted
application also passed all ten cases of the actual-DO
[pipeline probe](evidence/workerd-probe.json).

This is an empirically validated runtime mitigation for these local synthetic
trials. It does not prove that the GC hotfix alone caused the improvement:
Python, Pyodide, Emscripten, and dependency versions also changed. The earlier
deployed duration run is separate evidence tied to version
`69b1b14d-e27b-4ebc-8623-33dc94d5c186`, described above. Physical real-voice
acceptance, forced production eviction/deployment recovery, indefinite stability,
and full Pipecat compatibility remain unvalidated. The reduced source allowlist and vendored dependency setup remain
unsupported spike packaging, not a certified upstream Pipecat distribution.

## Deployed provider compatibility

The main demo is now deployed with authentication configured and fixture routes
off. Production health reports Python 3.14.2 / Pyodide 314.0.6. The initial
real-provider attempt reached a final Flux transcript from prerecorded input,
then failed when the LLM adapter put a raw `pyodide.ffi.JsProxy` stream reader
into a Python set: that proxy is unhashable.

The application adapter now owns readers in a dictionary keyed by `id(reader)`;
the values retain the proxies until generator cleanup finishes. Shutdown visits
the dictionary values. Nested cleanup ensures the reader lock is released and
the ownership entry removed even if cancellation interrupts reader cancellation.
This changes the application provider adapter, not vendored Pipecat.

Further actual-provider checks exposed three application compatibility details:

- Llama SSE can expose a numeric `response` field while its standard
  `choices[0].delta.content` still contains text. The adapter prefers the string
  delta and accepts the legacy `response` fallback only when it is a string.
- The Aura WebSocket binding requires the sample-rate option as a string;
  the adapter sends `"24000"` with mono linear16 output.
- Tiny provider PCM packets exhausted the receipt cap when each packet became
  a separate client chunk. The conversation output coalesces data into at most
  100 ms / 4,800-byte PCM chunks, retains the final sentence marker, and keeps
  the 256-receipt bound. Provider packet count no longer determines receipt count.

The fake-I/O adapter checks cover unhashable reader ownership, SSE text selection,
early generator close, concurrent readers, shutdown, and cancellation during
reader cleanup. Actual deployed prerecorded-input checks subsequently completed
Flux recognition, Llama generation, and nonzero Aura output. The earlier
duration-tested version `69b1b14d-e27b-4ebc-8623-33dc94d5c186` passed
[actual-provider duration](evidence/real-provider-soak-summary.json),
[strict recorded pause](evidence/real-provider-pause-1s.json), and
[idle cleanup](evidence/real-provider-abandon.json). The earlier
[smoke result](evidence/real-provider-smoke.json) belongs to its tested revision.
The [normal tool smoke](evidence/real-provider-tool.json) passed on that same
version and returned its fixed fictional Tuesday 10 AM / Thursday 2 PM availability.
Its combined pending check passed tool cancellation, then failed Flux
startup with HTTP 429 before input in the thinking session; cleanup reached zero.
[That capacity failure](evidence/real-provider-pending-capacity-429.json) remains
a failed combined run. The pending harness supports `--case all|tool|thinking`
(`all` by default), so a case can be retried independently without conflating its
result with the earlier failure. The
[isolated thinking check](evidence/real-provider-model-cancellation.json) also
failed STT startup with HTTP 429 before sending input and released every tracked
resource. These historical failures did not exercise model cancellation.
The later [capacity-fix revision passed that case](evidence/real-provider-model-cancellation-capacity-fix.json),
including a complete spoken recovery response and zero owned resources at End.
Neither recorded input nor elapsed playback receipts establishes physical microphone capture,
speaker output, or audible latency. Historical failures and
[deployment evidence](evidence/deployment.json) remain available for comparison.

## Turn finalization and provider liveness

The application uses a **1,200 ms production EndOfTurn grace**, while explicitly
selected fixture connections use **zero**. A provider StartOfTurn during the
grace cancels the pending commit and keeps the same Pipecat user turn open;
completed fragments are joined before one final transcription and stop signal
reach the aggregator. Pending text is bounded to 16 fragments and 8,192
characters, and provider recovery or session closure cancels and awaits the
commit task. Discarding an interrupted turn closes its turn state without
presenting previous context as a fresh request. This is application turn
adaptation, not a change to Pipecat's cancellation or aggregation algorithms.

The grace adds 1.2 seconds after the final provider EndOfTurn when speech does
not resume. The application's final-transcript-to-first-audio measurement starts
after that wait and therefore excludes it. Recorded-input-end-to-first-audio
includes it. Earlier zero-grace fixture timings must not be presented as
production response latency. The strict one-second recorded-pause check passed
on version `69b1b14d-e27b-4ebc-8623-33dc94d5c186` with one final user transcript
and no premature reply.
Other pause lengths, speakers, and natural speech still require testing.

Flux liveness uses silent PCM rather than a Nova-style KeepAlive control message.
The adapter checks once per second and sends silence after at least one second
without input. Provider liveness and the 30-second client abandonment timer are
separate: outbound provider traffic must not retain an abandoned application
call. The provider idle check on version
`69b1b14d-e27b-4ebc-8623-33dc94d5c186` passed with the expected abandonment
reason after about 30 seconds, no provider errors, and zero pipeline, provider,
playback, and pending-turn resources.

## Historical Python 3.13 failure and restart experiment

On the earlier Python 3.13.2 / Pyodide 0.28.2 runtime, calling workers-runtime-sdk 1.9.0
`self.ctx.abort()` on a separate DO caused a fatal WASM memory-access error
while four other objects handled WebSocket traffic. A minimal reproducer with
no Pipecat or model dependencies also failed. Its baseline processed 110,480
messages in 30 seconds; the standard SDK abort trial failed; a native-JavaScript
abort callback processed 110,080 messages in 30 seconds without errors.
See [the reproducer](repro/runtime-abort/README.md) and its findings.

The SDK queues a Python lambda that invokes the raw JS abort. Entering Python
immediately before V8 unwinds the object was a suspected boundary, not a proven
interpreter-level diagnosis. A historical test-only application revision used
`queueMicrotask(ctx.abort.bind(ctx, reason))`, where `ctx = self.ctx._ctx`.
That private-field experiment also failed the full Python 3.13 duration test.
It is retained in the minimal reproducer and historical evidence, and has been
removed from the delivered application. The current test-only restart route
uses public SDK `self.ctx.abort()` after persisting application state.

A fresh Python 3.13 trial subsequently crashed without a controlled restart,
debugger, reload, or source edit. All four WebSockets closed with code 1006
around 516.54 seconds after connection, after 424 completed turns; the harness
recorded failure at 526.998 seconds following its ten-second turn-clear timeout.
The fatal WASM memory-access fault surfaced in JSON decoding. This demonstrates
that abort and debugger attachment were not necessary for the sustained failure;
it does not identify the corruption's origin. See the
[no-restart results](evidence/workerd-soak-py313-no-restart.json) and
[crash output](evidence/workerd-py313-no-restart-crash.txt). Failed-runtime cleanup
cannot be established from reconstructed diagnostics. Full application results
are in [REPORT.md](REPORT.md).

## Primary sources

- [Pipecat 1.11.0 release](https://github.com/pipecat-ai/pipecat/releases/tag/v1.11.0)
- [Pinned package requirements](https://github.com/pipecat-ai/pipecat/blob/v1.11.0/pyproject.toml)
- [FrameProcessor interruption machinery](https://github.com/pipecat-ai/pipecat/blob/v1.11.0/src/pipecat/processors/frame_processor.py)
- [PipelineWorker lifecycle and startup](https://github.com/pipecat-ai/pipecat/blob/v1.11.0/src/pipecat/pipeline/worker.py)
- [VAD analyzer](https://github.com/pipecat-ai/pipecat/blob/v1.11.0/src/pipecat/audio/vad/vad_analyzer.py)
- [Base Smart Turn](https://github.com/pipecat-ai/pipecat/blob/v1.11.0/src/pipecat/audio/turn/smart_turn/base_smart_turn.py)
- [HTTP Smart Turn](https://github.com/pipecat-ai/pipecat/blob/v1.11.0/src/pipecat/audio/turn/smart_turn/http_smart_turn.py)
- [External turn strategies](https://github.com/pipecat-ai/pipecat/blob/v1.11.0/src/pipecat/turns/user_turn_strategies.py)
- [Context aggregators](https://github.com/pipecat-ai/pipecat/blob/v1.11.0/src/pipecat/processors/aggregators/llm_response_universal.py)
- [WorkerRunner](https://github.com/pipecat-ai/pipecat/blob/v1.11.0/src/pipecat/workers/runner.py)
