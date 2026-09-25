# Evidence and verdict — 24 September 2026

**Access-key update:** the application verifies access before opening the
microphone and supports loading the supplied key text file directly. Missing,
incorrect, and unconfigured keys now produce distinct messages. The delivered
key and the browser file-loading flow were both verified successfully. See
[evidence/access-key-fix.json](evidence/access-key-fix.json). The key stays in
memory and is sent only in authentication headers; it is not saved in browser
storage or included in the source archive.

**Capacity update:** the application includes bounded startup retries, clear
provider-capacity errors, and immediate startup cancellation. See
[CAPACITY-FIX.md](CAPACITY-FIX.md) for its tested version and checks. Earlier
long-duration results below remain tied to their recorded deployment.

**Microphone investigation:** a user reported speaking while the browser showed
Listening, with no user or assistant transcript. Physical capture and playback
remain unvalidated. The updated source adds gesture-time audio-engine resume,
live input and delivery diagnostics, and a Resume audio action; a successful
physical microphone retest has not established that this issue is fixed.

**Runtime verdict: works with restrictions, including deployed Workers.** Actual
Pipecat 1.11.0 runs inside Python 3.14.2 / Pyodide 314.0.6 Cloudflare Durable
Objects. The earlier frozen deployment passed a ten-minute, two-session recorded-input
run through real Flux, Llama, and Aura providers: 20 input clips, ten output
interruptions, ten recoveries, one reconnect/history check, no reported errors,
and zero owned resources after cleanup. Two earlier local ten-minute four-DO
synthetic runs also passed, including one after a public-SDK controlled restart.
The delivered configuration removes the experimental private restart shim. No
container, agent server, Voice Agents package, local VAD, or thread pool
participates in the conversation pipeline.

**Overall voice-demo verdict: deployed; real-voice acceptance remains incomplete.**
Cloudflare OAuth was renewed and the [protected demo](https://pipecat-on-workers.korinne.workers.dev)
is live. The access-key flow was verified on version
`33a5c3ed-17ba-47a7-9c77-db90cfbf3114`; the latest deployment is recorded in
[deployment evidence](evidence/deployment.json). The duration-tested prior version is
`69b1b14d-e27b-4ebc-8623-33dc94d5c186`. Production health reports
Python 3.14.2 / Pyodide 314.0.6, the demo key is configured, unauthorized session
creation returns HTTP 401, and fixture routes are off. That prior revision passed
the strict recorded one-second-pause check and 30-second idle cleanup check.
The duration run, ordinary fictional-tool response, and pending-tool
cancellation also passed on the duration-tested revision. Capacity-fix version
`a0fddf09-a341-4b07-bd50-84adcc2ffed1` **passed pending-model cancellation and a complete
spoken recovery response**. Three earlier attempts failed at STT startup with
HTTP 429 before input; their failed artifacts remain unchanged. Physical microphone/speaker acceptance,
natural turn quality, and audible interruption
timing remain unverified.

## What was built

- Start/Mute/End browser interface, microphone resampling, streamed PCM playback,
  transcript, reconnect, and scheduling measurements.
- Capability-protected routing to one Python Durable Object per conversation.
- Actual Pipecat queues, external turn strategies, user aggregation, context, and
  frame-task cancellation. Hosted Flux turn events supply candidate boundaries;
  production waits 1.2 seconds before committing a final user turn.
- Async Workers AI adapters for Flux recognition, Llama 3.3 generation, and Aura-2
  speech, with bounded queues, connection errors, limited STT reconnect, and
  late-response cleanup. No Python provider networking SDK is used.
- A cancellable fictional appointment lookup. Dispatch uses an explicit keyword
  rule; this does not establish generic model-selected tool calling.
- Persisted conversation text, generation, capability hash, and status. Live
  pipeline tasks, provider connections, and PCM are recreated after reconnect.

Assistant context includes a sentence-sized segment only after every PCM chunk
in that segment receives a browser playback acknowledgment. Interrupted partial
segments are omitted conservatively, even if some words were heard. Web Audio
completion is a scheduling receipt, not physical proof of speaker output.
Generation IDs reject stale audio and receipts; Python cancellation alone cannot
retract audio already sent to the browser. Reconnect replaces the visible
transcript with the persisted, acknowledged history.

## Validation performed

| Requirement | Evidence and limit |
| --- | --- |
| Pipecat inside a Python DO | Ten scenarios passed in actual local `workerd`, Python 3.14.2/Emscripten. The duration-tested deployed Python DO also passed the real-provider duration run. See `evidence/workerd-probe.json` and `evidence/real-provider-soak.json`. |
| Pending model/tool interruption | Actual Pipecat cancellation passed against controllable fixtures. The duration-tested revision passed the tool case twice. After three earlier startup failures, capacity-fix version `a0fddf09-a341-4b07-bd50-84adcc2ffed1` passed model-only cancellation before audio and a complete spoken recovery response, with no canceled audio after clear and zero owned resources at End. See `evidence/real-provider-model-cancellation-capacity-fix.json`. Remote compute cancellation is unproven. |
| Fictional tool result | The ordinary tool request passed on the duration-tested deployment: the private transcript contains Tuesday at 10 AM and Thursday at 2 PM, matching the read-only application lookup. One final user turn and one complete nonzero-audio response were observed; cleanup reached zero. See `evidence/real-provider-tool.json`. |
| Playback interruptions | The duration-tested deployed run passed ten explicit client interruptions while output was queued, each followed by successful recovery. Three targeted synthetic cases and 328 explicit interruptions across two local sustained runs also passed. Physical sound was not measured. |
| Mid-utterance pause | Earlier recorded-input checks split incorrectly at Flux thresholds 0.7 and 0.9. With the 1.2-second EOT grace, the strict check passed on the duration-tested deployment: one final user turn, no assistant output before input ended, nonzero TTS, and clean shutdown. This validates that recording; natural microphone speech remains untested. |
| Concurrent sessions and routing | Four local DOs per synthetic run kept identifying output/context separate. In the duration-tested two-session real-provider run, all 20 final user transcripts matched their own input topic and none matched the other recording's topic. This limited routing observation does not establish exhaustive semantic/context isolation. |
| Disconnect/reconnect | The duration-tested deployed run verified one reconnect restored acknowledged history and completed another turn. Local fixture lifecycle tests also restored history with fresh pipeline/provider objects. |
| Controlled restart | In a local actual-DO test on the earlier Python 3.14 revision, public `ctx.abort()` closed the call; reconnect restored history and completed another turn. That runtime then passed a ten-minute local soak. This is not a deployed forced-eviction test; seamless continuity is not claimed. |
| End/abandon cleanup | Both duration-tested deployed sessions ended with zero owned resources, as did the same-revision pause test and earlier local soaks. The idle gate passed at 29.975 seconds after ready with no client audio, no provider errors, one STT connection, and zero owned resources including pending-turn state. |
| Browser | 29 tests passed with fake browser I/O and real resampling/playback calculations, including microphone diagnostics and startup ownership. Earlier layout was visually inspected; physical microphone capture remains under investigation. |
| Local Python regressions | 23 tests plus ten subtests passed on Python 3.14.7, including audio input counters, connection guidance, turn grace, recovery, packetization, and the plural appointment request. Native-import/thread-denial core check and provider-adapter checks passed. Seven entry-lifecycle and 22 access-authentication cases also passed. |
| Node validation harness | 12 tests passed for the recorded-input validation tools. These test harness behavior, not provider or physical voice acceptance. |
| Real provider response | A deployed recorded-input smoke completed Flux recognition, Llama generation, and nonzero Aura PCM on an earlier revision. Its 88 chunks represent 8.8 seconds of emulated playback receipts for one complete sentence. See `evidence/real-provider-smoke.json`; this was an integration pass, not a pause pass. |
| Deployment and access | Dependency synchronization, deployment dry-run, and actual upload/deployment completed. The configured demo key rejects unauthorized session creation with HTTP 401; fixture routes are off. See `evidence/deployment.json`. |
| Deployed ten-minute provider session | **Passed 600.011 seconds**, two concurrent sessions, 20 prerecorded clips, ten interruptions and ten recoveries, one reconnect/history check, no reported errors, and verified cleanup. The run used actual providers and emulated playback receipts. No physical ten-minute microphone/speaker session has been run. |

`README.md` contains exact commands and the architecture diagram.
`VALIDATION.md` contains the remaining real-voice acceptance procedure.
`COMPATIBILITY.md` lists import/thread findings, six Pipecat source edits, and
unsupported features. `pipecat-compat.patch` is the reviewable upstream diff.

## Physical microphone report and diagnostics

The user reported speaking without a transcript while the browser showed
Listening, a 48,000 → 16,000 Hz conversion, zero reconnects, and no console
errors. Those observations did not distinguish a stopped audio context, silent
or incorrect input device, capture callbacks, upload failure, or speech-service
behavior. Earlier prerecorded-provider checks bypassed browser microphone
capture and cannot resolve this report.

The updated Start handler creates the audio context and requests resume before its
authentication await; it still requests microphone access only after the key
is verified. The interface now shows a live sound meter, context state and
clock, track state, capture/upload totals, and **Resume audio**. Safe heartbeat
responses add server input and provider-forwarded byte counters. The latter
increments only when the provider send returns success; provider keepalive
silence is excluded. Counters contain no audio or transcripts, and advancing
byte totals can still represent silence rather than recognized speech.

On deployment `76eb1ebf-32de-4291-9364-466953c444a1`, the actual browser
showed an advancing audio clock, live track, and growing capture, upload,
server-received, and provider-forwarded totals. Peak input stayed at `0.0000`
and no transcript appeared. This narrows that observation to no detectable
input sound; it does not establish the cause or prove a spoken retest failed,
because the request to speak during this new call received no response.
The call closed at its 15-minute limit while a native settings inspection was
blocked by unavailable computer-use permissions. See
[the visible browser observations](evidence/browser-microphone-observation.json).

Fresh and restored calls now receive plain connection guidance instead of an
internal pipeline-reset notice. These changes improve startup and diagnosis;
they are not evidence that the user's physical microphone issue is fixed.
The next gate is a physical-device retest using
[the troubleshooting steps](VALIDATION.md#microphone-troubleshooting), followed
by audible-response and interruption acceptance. Historical duration/provider
results remain tied to their recorded versions below.

## What the deployed provider trials changed

The live trials exercised the actual Workers AI binding and its Pyodide proxy
types. They exposed integration assumptions that the original fake-I/O tests
did not cover. Each failed trial remains in `evidence/`; subsequent passes apply
to their tested source revision and do not retroactively validate earlier code.

| Observed failure | Delivered correction and regression coverage | Historical evidence |
| --- | --- | --- |
| Flux produced a final transcript, then adding a JavaScript stream reader to a Python set failed because the proxy is unhashable. | Readers are owned by `id(reader)` in a dictionary. Shutdown iterates its values; cancellation and lock release retain ownership until cleanup finishes. Fake readers are now unhashable, with concurrent ownership and interrupted-cleanup checks. | `real-provider-smoke-initial-failure.json` |
| A Llama SSE event contained numeric `response: 1.5` alongside string `choices[0].delta.content: "1.5"`; appending the numeric field raised a type error. | Prefer the exact string delta, including `"0"` and formatting such as `"1.50"`. Fall back only to string `response`; ignore metadata-only and non-string chunks. Regressions cover numeric fields, zero, and final metadata. | `real-provider-smoke-numeric-chunk-failure.json`, `llm-sse-format.json` |
| TTS reached assistant text but the upgrade returned a falsey Pyodide `JsNull`, which passed an `is None` check and failed at listener registration. | Check socket truthiness, report upgrade status, and cancel late response bodies without a socket. Send Aura's sample rate as string `"24000"`, matching the binding's query schema. The string parameter enabled the subsequent live handshake; a direct REST WebSocket probe had already returned 101 for the same model/account. | `real-provider-smoke-tts-upgrade-failure.json` |
| Aura's 40 ms PCM packets exhausted the 256-chunk receipt bound after 10.24 seconds of received audio, before the first sentence's receipts completed. | Coalesce provider packets into up-to-100 ms / 4,800-byte frames. Keep one held frame and a bounded remainder, preserving byte order and the final sentence receipt marker. Packetization tests cover 300 and 301 small packets. The eight-second unacknowledged-audio and 256-receipt limits remain unchanged. | `real-provider-smoke-packet-bound-failure.json` |
| The exact transcript “What fictional appointments are available?” missed the singular keyword rule, so the model answered without the fictional lookup. | Match `appointments?`; a real-Pipecat regression checks the actual asynchronous lookup result and its insertion into model context. The later pending-tool live test reached and canceled this tool. | `real-provider-pending-plural-failure.json`, `real-provider-pending.json` |

The recorded-input smoke that subsequently passed still contained two final
user transcripts. Its test asserted provider integration, not a single semantic
turn. Its first-audio delay was corrected to **1,433.26 ms after the latest
preceding final user transcript**, using the retained private transcript capture;
the original first-final anchoring was incorrect. This is one earlier-revision
network measurement, not a current latency distribution or acoustic delay.

### Pause handling and its latency tradeoff

The one-second pause recording failed at both Flux EOT thresholds. At 0.7,
assistant audio arrived about 1.763 seconds before the complete input finished.
At 0.9, the first EOT occurred at server elapsed 2,881 ms and resumed speech
started 917 ms later; an assistant text response had already been generated,
although the resumed start canceled that generation. Both recordings produced
two final user transcripts. These remain failed pause checks despite their
successful provider integration:
`real-provider-pause-1s-threshold07.json` and
`real-provider-pause-1s-threshold09.json`.

Production now retains bounded EOT fragments and starts an owned 1,200 ms timer.
A resumed `StartOfTurn` cancels that timer, retains the fragment, and avoids a
second Pipecat start while the user turn is still open. The next EOT joins the
fragments; only after its grace expires does the app deliver one final
transcription and a proposed stop to the actual Pipecat pipeline. Duplicate
provider ends remain deduplicated. The threshold stays at 0.9 and the provider
EOT timeout stays at 5,000 ms. Fixture mode and default test sessions retain a
zero grace for their existing deterministic timing.

This adds **1.2 seconds after the last provider EOT** to an ordinary completed
turn, in exchange for joining resumed speech within that interval. It does not
establish that every natural pause will be handled correctly. The new tests
cover resume/join, true completion, duplicate ends, close cancellation, provider
failure, text/fragment limits, and recovery. On a discarded turn, Pipecat's
external stop strategy closes without waiting for text; an empty turn does not
replay earlier user context or start a model response. Diagnostics expose the
owned timer, pending fragments, and pending character count, all of which must
reach zero at cleanup. The recorded-pause check additionally requires
exactly one final user transcript and no assistant text/audio before input ends.

That strict check **passed** on duration-tested version `69b1b14d-e27b-4ebc-8623-33dc94d5c186` for the 4.68-second prerecorded input
with its one-second internal pause. It produced one final user transcript and
one complete assistant sentence, with 77 PCM chunks totaling 7.68 seconds and
nonzero audio. First audio arrived **1,463.47 ms after the final user transcript**
and **3,129.18 ms after the paced input ended**. The latter includes the grace
period and provider/network delays. This is one recording and one latency sample;
receipts were emulated by elapsed PCM duration without a speaker. See
`evidence/real-provider-pause-1s.json`.

### Idle provider connection

Before the heartbeat change in the duration-tested revision, a socket with no client traffic encountered
three Flux errors about unsupported text `KeepAlive` messages and closed after
about 23 seconds, ahead of the intended 30-second abandonment cleanup. Local
resource cleanup still reached zero. The application itself sends silence, not
text `KeepAlive`; the origin of the reported text message is not established.
The earlier three-second check with a strict `> 3` condition could leave a gap
of roughly six seconds, so a proxy heartbeat is a hypothesis, not a proven cause.
See `real-provider-abandon-before-heartbeat-fix.json`.

The application checks once per second and sends 80 ms of silent PCM when no
audio has been sent for at least one second; active audio resets the timestamp.
The idle gate must verify that the provider remains connected until the app's
30-second abandonment timeout, then releases every owned resource. The gate on the duration-tested revision
**passed** at 29.975 seconds after ready: close code 1012 with the expected
abandonment reason, no provider errors or STT reconnect, and all owned tasks,
sockets, readers, requests, queued bytes, playback receipts, and pending-turn
state at zero. See `evidence/real-provider-abandon.json`.

### Tool response and pending-work interruption

Two combined interruption checks on the duration-tested revision each passed their pending-tool
case: explicit client interruption arrived before audio, no canceled packets
appeared during the following second, and a new request completed with nonzero
audio and clean End. Both combined checks nevertheless **failed overall** when
their second, pending-model case failed at STT startup with **HTTP 429 before
any input**, then cleaned up to zero. This reports the provider response without
attributing its cause to a specific account quota, concurrency limit, or billing
condition. The artifacts are `real-provider-pending-capacity-429.json` and
`real-provider-pending.json`.

A third attempt selected only the pending-model case through a harness-only
option, with runtime source and deployment unchanged. It also failed at STT
startup with HTTP 429 before any input, and all owned resource counters reached
zero. See `evidence/real-provider-model-cancellation.json`. No further attempts
were made on that revision. Those startup failures did not exercise model
cancellation. After the capacity-handling fix, the separately retained
`real-provider-model-cancellation-capacity-fix.json` **passed** on capacity-fix
version `a0fddf09-a341-4b07-bd50-84adcc2ffed1`, including a complete spoken recovery response and zero resource
counters at End. The successful
output interruptions in the duration run and the pending-tool results remain
separate evidence tied to their own tested revisions.

The ordinary tool-response smoke also **passed** on the duration-tested revision. The private transcript
confirms Tuesday at 10 AM and Thursday at 2 PM, the actual fictional lookup
result. It produced one final user transcript and one completed assistant
sentence with nonzero audio, followed by clean End and zero owned resources.
This establishes the narrow keyword-dispatched, read-only example, not a booking
or general model-selected tool system. See `evidence/real-provider-tool.json`.

## Recorded-input deployed duration run

The source frozen in deployment `69b1b14d-e27b-4ebc-8623-33dc94d5c186` passed
**600,011.29 ms of active time** with two simultaneous conversation sessions.
Each session received ten prerecorded speech clips and used actual hosted Flux,
Llama, and Aura services. Across both sessions the test interrupted queued
output ten times and verified a successful following response ten times. One
session also reconnected and verified acknowledged history. There were no
reported errors. Both End operations were acknowledged and every owned resource
counter reached zero, including the grace timer and pending fragments/characters.
Source hashes for that revision are in `evidence/deployment-validated-69b1b14d.json`; results and derived summaries
are in `evidence/real-provider-soak.json` and
`evidence/real-provider-soak-summary.json`.

All ten final user transcripts in each session matched its recording's expected
topic; none of the 20 matched the other recording's topic. That is a limited
observed routing check, not exhaustive proof of cross-session context isolation.
Playback acknowledgments followed serial elapsed PCM duration with no audio
device. Interruptions were explicit client controls during queued output, not
acoustic barge-in. Zero owned resources does not establish that remote compute
or billing stopped.

| Deployed measurement | Samples | Result | Meaning and limit |
| --- | --- | --- | --- |
| Input start to first received audio | 20 | median 7,674.57 ms; p95 13,094.65 ms; range 6,776.04–13,570.74 ms | Includes each 3.32- or 4.56-second input recording, EOT detection, the 1.2-second grace, providers, and network. It is **not response latency after input completion**. |
| Approximate client interruption to observed clear | 10 | median 62.45 ms; p95/max 183.43 ms; minimum 40.55 ms | Derived from per-turn timing; includes network and 20 ms harness polling. It measures neither pure server cancellation nor acoustic silence. |

Percentiles use nearest rank. These measurements describe one bounded recorded
input run on this frozen deployment, not a production latency guarantee.

## Sustained runtime comparison

| Runtime / conditions | Result | Evidence |
| --- | --- | --- |
| Python 3.13, prior SDK restart | Fatal WASM memory-access error after the last successful 506.7-second sample. Inspector activity was a confounder. | `workerd-soak-py313-failed.json` |
| Python 3.13, private JS restart experiment | Same fatal error after the last successful 507.270-second sample, without inspector attachment. The private shim was not a fix. | `workerd-soak-py313-shim-failed.json` |
| Python 3.13, fresh process with no restart or debugger | Failed: all four sockets closed with code 1006 at about 516.54 seconds since connection; 424 turns completed. The harness recorded failure at 526.998 seconds after its timeout. | `workerd-soak-py313-no-restart.json` |
| Python 3.14, fresh process | **Passed 600.098 seconds, 500 turns, 164 explicit interruptions**, zero reported application errors or detected cross-session output, clean shutdown. | `workerd-soak-py314-no-restart.json` |
| Python 3.14, after public SDK restart/recovery | **Passed 600.000 seconds, 496 turns, 164 explicit interruptions**, zero reported application errors or detected cross-session output, clean shutdown. | `workerd-soak-py314-after-sdk-restart.json` |

Each row's JSON is under `evidence/`. Comparison processes ran concurrently on
this computer. Source was frozen and no inspector attached during those duration
runs. They preceded the live-provider integration fixes and final turn-grace
revision, so they establish runtime evidence for their tested revisions. The
no-restart Python 3.13 failure shows that abort is not necessary to
trigger the sustained crash. Post-crash reconstructed diagnostics do not establish
orderly cleanup of the failed live pipelines.

A minimal reproducer also demonstrates a Python 3.13 SDK-abort failure without
Pipecat or providers. Its baseline processed 110,480 messages in 30 seconds;
the SDK-abort variant failed; a private native-JS callback passed a short
comparison but failed the full application test. Those historical experiments
remain in `repro/runtime-abort/`. The delivered application uses the public SDK
on Python 3.14 and contains no private abort workaround.

**Corrected runtime attribution:** earlier investigation inferred the runtime
from build tooling/wheel URLs. Direct `/api/health` measurement now confirms
Python 3.13 uses Pyodide **0.28.2**, and Python 3.14 uses **314.0.6**. See
`evidence/runtime-versions-direct.json`. Cloudflare's pinned runtime map selects
314.0.6 and its workerd patch targets that version's suspended-stack GC behavior.
The fix is a plausible explanation for the observed improvement, not proof of
the exact cause of this spike's crash. Sources: [runtime bundle map](https://github.com/cloudflare/workerd/blob/v1.20260923.1/build/python_metadata.bzl),
[workerd fix](https://github.com/cloudflare/workerd/pull/7422),
[upstream Pyodide explanation](https://github.com/pyodide/pyodide/pull/6466).

The older CPython-only soak also passed 600.001 seconds with four pipelines,
476 turns, 316 interruptions, and no remaining tasks. It used an earlier
application revision and is retained as supplemental evidence; it does not
substitute for either actual-DO test or real voice.

## Measurements and limits

The following summarize the two passing Python 3.14 runs. The per-session metric
ring retains only its last 500 events, so response/clear summaries omit early
turns. Full data is in `evidence/workerd-soak-py314-metrics.json`.

| Measurement | Samples | Result | Limit |
| --- | --- | --- | --- |
| WebSocket connect to ready | 8 | median 66.4 ms; range 35.5–79.4 ms | Excludes session creation, cold runtime boot, microphone permission, and real providers. |
| Fixture generation to first audio | 924 retained events | median 0 ms; p95 1 ms | Integer clock resolution and silent fixtures; not a voice response estimate. |
| Server interruption to clear dispatch | 1,228 retained events | median 1 ms; p95 3 ms; max 34 ms | Excludes delivery to browser and audible silence. |
| Local DevTools heap after a passing run and cleanup | 1 | used 41.51 MB; capacity 78.92 MB; backing storage 24.60 MB | Not whole Python/WASM/isolate memory or per-DO attribution. |
| Physical voice latency / audible stop | 0 | Not measured | Requires microphone/speaker testing and audio-device measurement. |

The heap sample was taken after the duration test, not during it. The earlier
CPython trace measured 14.85→15.40 MB of Python allocations, peak 17.66 MB, but
excludes native/WASM memory and is not directly comparable to the DevTools sample.
Total deployed isolate memory and production resource limits remain unmeasured.

Application policy bounds unacknowledged server PCM to eight seconds, receipt
metadata to 256 chunks, provider receive queues to 1 MiB, browser buffering to
12 seconds, pending user text to 16 fragments / 8,192 characters, and context to
80 recent messages plus the system instruction.
These are buffer bounds, not proof of bounded whole-runtime memory. Test fixtures
also retain generation records; they are not selected by the browser client.

## Compatibility and remaining work

The delivered configuration pins Pipecat 1.11.0, workers-py 1.17.4,
workers-runtime-sdk 1.9.0, Wrangler 4.139.0/workerd 1.20260923.1, compatibility date
2026-09-24, and Pydantic 2.12.5/core 2.41.5. Local build/test Python is 3.14.7;
Workers supplies 3.14.2. Build tools/wheels reference Pyodide 314.0.7, which is
not the actual Worker interpreter. Lockfiles and source hashes are included.

The 119-module upstream source subset and six lazy-import/prewarm edits preserve
Pipecat's frame queues, interruption implementation, and external turn machinery.
This remains unsupported spike packaging, not full upstream Pipecat compatibility.
Native VAD, existing executor-backed HTTP Smart Turn, arbitrary providers/transports,
RTVI, video, hibernating pipelines, and general model-selected tools are untested
or intentionally unavailable. The currently documented hosted Smart Turn is v2;
this spike instead uses Flux turn events with the application grace described
above. Source details are in `COMPATIBILITY.md` and `RUNTIME-RESEARCH.md`.

The supplied [Cloudflare AI-audio example](https://developers.cloudflare.com/realtime/sfu/examples/ai-audio/)
informed transport selection. Browser WebSockets avoided SFU provisioning and
48 kHz stereo conversion while testing Python conversation machinery. This spike
does not claim SFU integration or WebRTC media guarantees.

Before a supported customer offering:

1. Resolve the reported microphone capture/transcription failure, complete
   physical real-voice acceptance, and repeat current-version provider
   duration/load checks. Broaden pause/latency/interruption/error
   distributions with sample sizes. Recorded-input integration does not validate
   natural conversation.
2. Repeat sustained/restart/load tests in deployed Workers and across runtime
   releases. One deployed and two local passes are not a production stability
   guarantee. Measure isolate memory, CPU, quotas, cost, forced eviction, and
   deployment during calls.
3. Upstream the small compatibility changes and provide a supported remote-only
   Pipecat package with a tested Python/WASM dependency matrix.
4. Improve played-word alignment, media backpressure, device/network behavior,
   provider recovery, and general schema-based tool cancellation.
5. Add customer tenancy/authentication, quotas, retention/deletion, secret
   provisioning, observability, and operational recovery. The demo key and
   per-session capability are a protected spike interface.

Cloudflare access and deployment are now complete. No separate provider key is
required by this implementation. Physical microphone/playback acceptance and
current-version repeated duration/load coverage remain outstanding.
Historical expired-login and local binding failures are retained in the evidence
rather than treated as current blockers.
