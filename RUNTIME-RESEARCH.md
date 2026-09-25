# Cloudflare Python DO / audio runtime research

Checked 2026-09-24 against current official documentation and Cloudflare source. Documentation support is distinguished from runtime testing; this note does not claim a successful deployment or provider connection.

## Practical narrow approach

Run Pipecat inside a Python `DurableObject`, entered through a Python `WorkerEntrypoint` routing one random session identifier to one named DO. Accept the browser WebSocket with `WebSocketPair.new().object_values()`, `server.accept()`, and `Response(None, status=101, web_socket=client)`. Hold asyncio pipeline tasks on the object. Use `create_proxy` for JS callbacks, retain them, and remove/destroy them during socket cleanup. `server.binaryType = "arraybuffer"` avoids incoming Blob conversion. Cloudflare's official [Python WebSocket example](https://developers.cloudflare.com/durable-objects/examples/websocket-server/) provides the basic supported shape. This live pipeline deliberately uses the standard API rather than attempting to hibernate active Python tasks.

The provider implementation is `src/providers.py`. It uses no provider SDK, local VAD, local turn model, requests, aiohttp, Python networking sockets, worker threads, or Voice Agents package.

## Packages and event loop

The delivered project requires Python >=3.14,<3.15, pins `workers-py` 1.17.4 and `workers-runtime-sdk` 1.9.0 as development dependencies, and uses compatibility date `2026-09-24` with the `python_workers` flag. It pins Pydantic 2.12.5, with core 2.41.5 resolved in the lockfile. Commands are `uv run pywrangler dev` and `uv run pywrangler deploy`. The local CPython interpreter is 3.14.7; the Worker runs CPython 3.14.2. Pure Python, PyEmscripten wheels, and bundled Pyodide packages are supported; ordinary native Linux/macOS wheels are not interchangeable with WebAssembly packages. Sources: [Python Workers](https://developers.cloudflare.com/workers/languages/python/), [packages](https://developers.cloudflare.com/workers/languages/python/packages/).

Python runs in Pyodide/CPython compiled to WebAssembly inside a V8 isolate. The runtime supplies its event loop; the handler should create/await asyncio tasks rather than call `asyncio.run()` or start another loop. The Cloudflare ASGI adapter itself uses asyncio queues/tasks around Worker WebSockets. Python `threading` and `multiprocessing` import but are explicitly nonfunctional. `resource` is unavailable, and the filesystem is ephemeral. Sources: [runtime implementation](https://developers.cloudflare.com/workers/languages/python/how-python-workers-work/), [standard library restrictions](https://developers.cloudflare.com/workers/languages/python/stdlib/), [official ASGI source](https://github.com/cloudflare/workers-py/blob/main/packages/runtime-sdk/src/asgi.py).

The runtime SDK automatically wraps AI bindings and converts results. Returned JS `Response` objects become Python Response wrappers; access their `js_object.webSocket` to extract a WebSocket. Generic streams can pass through as JS objects. The implementation allows this with `_raw(...)`. Sources: [RPC conversion source](https://raw.githubusercontent.com/cloudflare/workers-py/main/packages/runtime-sdk/src/workers/rpc.py), [Response wrapper source](https://raw.githubusercontent.com/cloudflare/workers-py/main/packages/runtime-sdk/src/workers/response.py), [FFI docs](https://developers.cloudflare.com/workers/languages/python/ffi/).

## Turn detection

The currently published Workers AI Smart Turn model identifier is `@cf/pipecat-ai/smart-turn-v2`; public catalog evidence does not establish hosted v3. It returns `is_complete` and `probability`. The generated Cloudflare types describe base64 audio input or an audio body/contentType object and optional dtype. This verifies documentation/catalog availability only; an actual authenticated inference is required to verify account/model availability. Sources: [Smart Turn model](https://developers.cloudflare.com/workers-ai/models/smart-turn-v2/), [Cloudflare generated model types](https://github.com/cloudflare/vite-react-template-brayden/blob/main/worker-configuration.d.ts).

The simpler asynchronous strategy is hosted Deepgram Flux STT through Workers AI. The official binding invocation is `env.AI.run("@cf/deepgram/flux", {encoding:"linear16",sample_rate:"16000"}, {websocket:true})`. Cloudflare exposes `eot_threshold`, `eot_timeout_ms`, and optional `eager_eot_threshold` as strings. Use provider `StartOfTurn` to interrupt and `EndOfTurn` to commit the transcript; no local inference/executor is necessary. Eager turn generation is intentionally disabled in this spike. Sources: [Cloudflare Flux release](https://developers.cloudflare.com/changelog/post/2025-10-02-deepgram-flux/), [model and parameters](https://developers.cloudflare.com/workers-ai/models/flux/).

Deepgram recommends 80 ms input chunks. Provider transcripts are turn-level; `turn_index` increments following EndOfTurn. StartOfTurn carries recognized speech, so barge-in begins after provider speech recognition rather than a zero-latency local energy check. Pauses can be handled with semantic EOT plus a 5,000 ms fallback timeout, but actual pause behavior requires audio testing. Sources: [Flux quickstart](https://developers.deepgram.com/docs/flux/quickstart), [Flux state machine](https://developers.deepgram.com/docs/flux/state).

## LLM and speech output

`@cf/meta/llama-3.3-70b-instruct-fp8-fast` is currently documented with incremental SSE output when `stream:true`. The adapter decodes fragmented UTF-8 and SSE lines; it cancels its reader on exit. This cancels delivery and does not establish that remote inference or billing stopped. Source: [Llama model page](https://developers.cloudflare.com/workers-ai/models/llama-3.3-70b-instruct-fp8-fast/).

`@cf/deepgram/aura-2-en` is currently listed for realtime speech output with linear16 encoding, sample rate, speaker, and container options. Its model page also describes a ReadableStream binding result for HTTP. The adapter requests WebSocket mode, 24 kHz mono PCM, speaker luna, container none. The official SFU example demonstrates the Aura WebSocket Speak/Flush/Flushed protocol for its configured Aura model. Aura-2 WebSocket acceptance must be authenticated-tested; catalog realtime labeling alone is insufficient. The adapter fails explicitly if no WebSocket is returned. Sources: [Aura-2 model](https://developers.cloudflare.com/workers-ai/models/aura-2-en/), [Cloudflare example TTS adapter](https://github.com/cloudflare/realtime-examples/blob/main/ai-tts-stt/src/tts-adapter.ts).

Each synthesis opens a fresh socket, receives streaming PCM, and closes on completion/cancellation. This costs connection setup but creates an unambiguous generation boundary. Browser playback must still clear queued audio on interruption and reject stale generation identifiers. Provider reconnect cannot restore partial upstream audio; it is reported as an audio gap and asks the caller to repeat any affected utterance.

## Lifecycle and measurement implications

Hibernation loses in-memory application state. Nonhibernatable idle objects can be evicted after 70–140 seconds without incoming events. Active outbound WebSockets can postpone eviction for up to 15 minutes per connection; ordinary fetch response streaming is not the same guarantee. Deployments/runtime relocation may restart DOs and terminate WebSockets. There are no reliable shutdown hooks: persist conversation state incrementally. A ten-minute audio call with continuous incoming frames is consistent with the documented lifetime, but this is not proof of sustained Pipecat operation. Source: [DO lifecycle](https://developers.cloudflare.com/durable-objects/concepts/durable-object-lifecycle/).

Persist only reconstructible application data: confirmed conversation messages, acknowledged played assistant text, configuration, sequence/generation counters, and timestamps. Live asyncio tasks, provider sockets, unplayed PCM, and Python proxy objects are not persisted. Reconnect should restore history, start fresh providers/pipeline, and explicitly announce interrupted recovery. Accurate audible interruption needs browser clock/playback instrumentation; server cancellation time is distinct.

Memory measurement must distinguish Python traced allocations, Python/WASM linear memory, JavaScript buffers, and the whole isolate. `resource.getrusage` is unavailable. Bounded application byte counters demonstrate queue bounds but do not measure interpreter/dependency cost or total resident memory. Full runtime profiling must accompany any production claim.

## User-provided SFU resource

The [AI audio pipelines example](https://developers.cloudflare.com/realtime/sfu/examples/ai-audio/) verifies a Worker/DO/STT/TTS/SFU composition with separate WebSocket adapters. SFU audio is 48 kHz stereo PCM, requiring conversion for the chosen 16 kHz mono STT. It needs a public callback endpoint, SFU app and credentials, and explicitly leaves conversation turn logic/interruption/context to integrators. For this runtime experiment, a browser PCM WebSocket avoids extra media conversion and SFU provisioning while still testing a real Python DO pipeline. This choice can be revisited after conversation-runtime compatibility is established.


## Reproduced restart boundary and runtime version limits

The standard SDK controlled-abort path caused a local Python/WASM failure in a
Python 3.13 minimal reproducer without Pipecat. The experimental native-JS
microtask path passed a short comparison, but the full Python 3.13 application
subsequently crashed during its sustained test too; this was not a validated
repair. The private-field shim is retained only in historical evidence and the
minimal reproducer. The delivered application's test-only restart route now
uses the public SDK `self.ctx.abort()` on Python 3.14. See
[the findings](repro/runtime-abort/FINDINGS.md), [compatibility note](COMPATIBILITY.md),
and [application results](REPORT.md).

A fresh Python 3.13 baseline also failed without any controlled restart,
debugger attachment, reload, or source edit. All four WebSockets closed with
code 1006 around 516.54 seconds after connection, after 424 completed turns;
the harness recorded failure at 526.998 seconds following its ten-second wait
for the next turn clear. The runtime reported another fatal WASM memory-access
fault, this time surfacing in JSON decoding. Thus neither a prior controlled
abort nor debugger attachment is necessary for the sustained Python 3.13
failure. This does not identify the original source of memory corruption.
See [fresh baseline results](evidence/workerd-soak-py313-no-restart.json) and
[crash output](evidence/workerd-py313-no-restart-crash.txt). Post-crash diagnostics
do not establish orderly cleanup of the failed pipelines.

Direct `/api/health` observations now identify the actual local Worker runtimes:

| Python inside Worker | Pyodide inside Worker | workerd bundle selected by the exact-tag metadata |
| --- | --- | --- |
| 3.13.2 | 0.28.2 | `0.28.2_2025-01-16_18` |
| 3.14.2 | 314.0.6 | `314.0.6_2026-08-17_6` |

The Python and Pyodide versions are observed in
[direct runtime evidence](evidence/runtime-versions-direct.json); bundle identifiers
come from the [workerd v1.20260923.1 runtime map](https://github.com/cloudflare/workerd/blob/v1.20260923.1/build/python_metadata.bzl).
The separately installed Pyodide `0.28.3` and `314.0.7` interpreters used for
package building do not identify the Worker runtime. Neither is selected by that
workerd runtime map.

Python 3.14 is the selected runtime mitigation, using supported compatibility
configuration and validated by the two local duration trials below. In
this workerd release, the `python_workers_314` flag selects the 314.0.6 bundle;
`python_workers` also enables it automatically with compatibility date
`2026-09-08` or later. The earlier `python_workers_20250116` selection uses 0.28.2.
See the [exact-tag compatibility flags](https://github.com/cloudflare/workerd/blob/v1.20260923.1/src/workerd/io/compatibility-date.capnp).
The [exact-tag patch selector](https://github.com/cloudflare/workerd/blob/v1.20260923.1/src/pyodide/tools/patch_pyodide_asm.ts)
applies the stack-switch GC hotfix specifically to 314.0.6, so the observed
Python 3.14 runtime selects the version covered by that fix. It does not apply
that patch to the observed 0.28.2 runtime.

The fresh Python 3.14 baseline completed one **600.098-second local run** across
four actual Durable Objects, with 500 completed turns, 164 explicit playback
interruptions, no reported errors, and zero remaining Pipecat tasks or pending
audio receipts after End. It used synthetic provider events and silent fixture
audio, with no controlled restart or debugger attachment. See
[Python 3.14 baseline results](evidence/workerd-soak-py314-no-restart.json).
Separately, a fresh Python 3.14 process completed the public SDK controlled-
restart lifecycle test, then a **600.000-second local run in the same process**:
496 turns, 164 explicit playback interruptions, no reported errors, and zero
remaining Pipecat tasks, live fixture generations/syntheses, or pending audio
receipts after End. It used no private shim, debugger attachment, reload, or
source changes during the trial. See [SDK lifecycle results](evidence/workerd-lifecycle-py314-sdk.json)
and [duration results after SDK restart](evidence/workerd-soak-py314-after-sdk-restart.json).
The promoted application also passed all ten cases of the actual-DO
[pipeline probe](evidence/workerd-probe.json). These are local synthetic runtime
and lifecycle results, not real-voice or deployed-platform validation.

The [upstream Pyodide fix](https://github.com/pyodide/pyodide/pull/6466) prevents
garbage collection from traversing frame pointers into suspended stack memory;
[workerd's backport](https://github.com/cloudflare/workerd/pull/7422) applies it
through JavaScript. The runtime comparison supports the Python 3.14 mitigation,
but does not isolate this particular patch
as the cause of the different outcomes: the Python, Pyodide, and Emscripten
versions also differ. The Python 3.13 SDK-abort reproducer remains a separate
observed boundary failure. Two ten-minute local passes establish the tested
scope; they do not certify indefinite stability, arbitrary Pipecat features,
or production restart behavior. The reduced Pipecat source vendoring remains
unsupported spike packaging even though the runtime selection and abort API
are public platform mechanisms.
