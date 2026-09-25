# Provider and physical-voice acceptance

**Current scope decision:** [ARCHITECTURE-REVIEW.md](ARCHITECTURE-REVIEW.md) proposes
a smaller closeout for the original spike. The broader device matrix below is
available for follow-up research; its full sample counts are not required by the
original brief. The user has not yet agreed the final acceptance scope.
The speaker-echo issue remains open. SFU currently has only a two-turn recorded
browser round-trip pass, with incomplete assistant memory and server-cleanup
validation; it must not inherit WebSocket acceptance results.

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

**Microphone status:** a user reported speaking while the demo showed Listening
without receiving a transcript, then confirmed “okay this is working!” after
the microphone deployment. This is user-reported basic live success, not a
measured duration or acoustic acceptance pass. The earlier cause remains
unconfirmed; the physical-device procedure below is still outstanding.

The [protected demo](https://pipecat-on-workers.korinne.workers.dev) works
with restrictions in deployed Python 3.14.2 / Pyodide 314.0.6 Durable Objects,
with fixture routes off. The earlier validated version `69b1b14d-e27b-4ebc-8623-33dc94d5c186`
passed a [600.011-second actual-provider run](evidence/real-provider-soak-summary.json)
with two simultaneous calls, 20 recorded inputs, ten interruptions and ten
successful recoveries, one verified history reconnect, zero retained resources,
and no reported errors. The [strict one-second pause](evidence/real-provider-pause-1s.json)
and [30-second idle cleanup](evidence/real-provider-abandon.json) checks also passed.
The [normal tool smoke](evidence/real-provider-tool.json) also passed, returning
the fixed fictional Tuesday 10 AM / Thursday 2 PM availability. The combined
pending-work run passed tool cancellation but failed at Flux startup with HTTP
429 before input in the thinking session; both sessions released their resources.
Keep [that failed combined run](evidence/real-provider-pending-capacity-429.json)
distinct from the [isolated thinking retry](evidence/real-provider-model-cancellation.json),
which also failed STT startup with HTTP 429 before any input and cleaned up all
resources. A subsequent [capacity-fix model-cancellation check](evidence/real-provider-model-cancellation-capacity-fix.json)
passed on version `a0fddf09-a341-4b07-bd50-84adcc2ffed1` with a complete spoken
recovery response and zero owned resources. These startup failures
neither exercise model cancellation nor invalidate the passing tool-cancellation
case. [REPORT.md](REPORT.md) records the full evidence verdict.

These are recorded-input provider checks, not physical microphone/speaker
acceptance. `/api/health` intentionally reports `voice_validated: false` until
that physical acceptance is established. The updated source also passed
**41 Python tests and the existing ten additional subtests, 50 browser tests,
12 harness tests, ten SFU entry cases, seven entry-lifecycle cases, and 22
access-authentication cases** in the current offline review. See
[evidence/review-checks.json](evidence/review-checks.json).
Record the deployed version for future runs; these results do not
validate later edits, indefinite runtime stability, or general speech behavior.
The concrete outstanding acceptance check is the physical microphone/speaker
procedure below, including natural pauses and acoustic interruption timing.
The earlier ten-minute trial has not been repeated on the capacity-fix or
microphone-diagnostics revisions.

## Automated checks with recorded speech

These scripts exercise actual Flux, Llama, and Aura providers over the deployed
application. They never inject fixture events. Inputs are **headerless PCM16 LE,
mono, 16,000 Hz**, 0.2–15 seconds each. Prepare `normal.pcm` and `second.pcm`
with distinct short neutral questions, `tool.pcm` asking about appointment
availability, and `pause-1s.pcm` containing one utterance with an internal
one-second silence. Container files such as WAV are rejected. The source label
below assumes prerecorded TTS; use `prerecorded-human` for an actual recording.

From the project directory, set the recording directory and enter the demo key
privately. `DEMO_ACCESS_KEY` is environment-only; never put its value in command
arguments, source, documentation, or the archive. `--capture-root` must be
outside `outputs/`; the temporary directory below satisfies that restriction.
Captures contain private transcripts, provider error details, and output PCM.
Evidence JSON contains allowlisted observations, without transcript or credentials.

```sh
printf 'Demo access key: '
read -r -s DEMO_ACCESS_KEY
printf '\n'
export DEMO_ACCESS_KEY
export VOICE_BASE="https://pipecat-on-workers.korinne.workers.dev"
export VOICE_CHECK_WORK="$(mktemp -d "${TMPDIR:-/tmp}/pipecat-voice-check.XXXXXX")"
export VOICE_PCM_DIR="/absolute/path/to/your/recordings"

node scripts/smoke_real_voice.mjs --base "$VOICE_BASE" --pcm "$VOICE_PCM_DIR/normal.pcm" --speech-source prerecorded-tts --capture-root "$VOICE_CHECK_WORK" --evidence "$VOICE_CHECK_WORK/smoke.json"

node scripts/smoke_real_voice.mjs --base "$VOICE_BASE" --pcm "$VOICE_PCM_DIR/pause-1s.pcm" --speech-source prerecorded-tts --expect-single-user-turn --capture-root "$VOICE_CHECK_WORK" --evidence "$VOICE_CHECK_WORK/pause-1s.json"

node scripts/smoke_real_voice.mjs --base "$VOICE_BASE" --pcm "$VOICE_PCM_DIR/tool.pcm" --speech-source prerecorded-tts --capture-root "$VOICE_CHECK_WORK" --evidence "$VOICE_CHECK_WORK/tool.json"

node scripts/check_real_pending.mjs --base "$VOICE_BASE" --pcm "$VOICE_PCM_DIR/normal.pcm" --tool-pcm "$VOICE_PCM_DIR/tool.pcm" --case all --capture-root "$VOICE_CHECK_WORK" --evidence "$VOICE_CHECK_WORK/pending.json"

node scripts/check_real_voice.mjs --base "$VOICE_BASE" --pcm "$VOICE_PCM_DIR/normal.pcm" --pcm-second "$VOICE_PCM_DIR/second.pcm" --duration-ms 600000 --sessions 2 --capture-root "$VOICE_CHECK_WORK" --evidence "$VOICE_CHECK_WORK/duration.json"

node scripts/check_real_idle.mjs "$VOICE_BASE" "$VOICE_CHECK_WORK/idle.json" "$VOICE_CHECK_WORK/idle-errors.private.json"
unset DEMO_ACCESS_KEY
```

Run each against unchanged deployed source. The smoke, pending, and duration
scripts support `--validate-input` to inspect their PCM inputs without network
requests. The idle script uses the three positional arguments shown above.
For a separate pending-work retry, replace `--case all` with `--case tool` or
`--case thinking` and use a new evidence filename. A single case uses one fresh
session and two recorded inputs. `all` is the default and uses two sessions / four
inputs. A startup HTTP 429 means that case never exercised cancellation; do not
count it as a passing case or merge it away when a later retry succeeds.

| Check | Required observations | Limit |
| --- | --- | --- |
| Smoke | Final user transcript, assistant sentence, nonzero speech PCM, elapsed full-sentence receipts, normal End and zero resources | Generic integration only; it may pass even if the provider split the recorded utterance. |
| Normal tool | Smoke with the appointment recording, plus inspection of the private assistant transcript for the fixed fictional availability | A deterministic demonstration, not general model-selected tools or real booking. |
| Pause | Smoke requirements plus exactly one final user transcript and no assistant transcript/audio before paced input finishes | Applies only to the supplied recording. `unexpected_user_turn_split` or `premature_reply_before_input_end` is a failed pause check. A generic smoke pass is insufficient. |
| Pending work | Two fresh sessions, four recordings total; interrupt on `tool` and `thinking` before audio, observe clear and one second without canceled-generation audio, then complete a new response | Explicit client control, not acoustic interruption; does not establish that remote model compute or billing stopped. |
| Duration | Two sessions for 600,000 ms; 20 recorded inputs, ten explicit interruptions, later completed responses, one reconnect with restored acknowledged history, clean End | Time-paced receipts emulate playback. Distinct questions do not establish semantic cross-session isolation. |
| Idle | Ready followed by no client traffic; abandonment after about 30 seconds with the expected close reason and zero resources | Provider heartbeat behavior is separate from client liveness; provider errors must be investigated. |

Cleanup requires zero `pipecat_tasks`, `provider_tasks`, `provider_sockets`,
`provider_readers`, `pending_provider_requests`, `queued_provider_bytes`,
`unacked_audio_bytes`, `pending_playback_chunks`, `pending_turn_tasks`,
`pending_user_fragments`, and `pending_user_chars`. Record `turn_end_grace_ms`
alongside the measured latency. Production uses **1,200 ms**; fixture routes use
**zero**. Production waits after the latest provider EndOfTurn, canceling that
wait if speech resumes and joining at most 16 fragments / 8,192 characters.
The added wait favors a complete utterance at the cost of response delay.
Final-transcript-to-first-audio timing excludes that wait because the application
emits the final transcript after it expires. Input-end-to-first-audio timing
includes the grace, recognition, and generation delays. Do not compare the
zero-grace synthetic timing numbers directly with production response latency.

Provider recovery cancels the pending finalization task, drops uncommitted text,
and closes the interrupted turn without replaying prior context as a new
request. Repeat the lost utterance when testing recovery. The provider adapter
checks for missing input every second and supplies silent PCM to Flux; this
outbound traffic must not postpone the separate 30-second client idle timeout.
No harness opens a microphone or speaker. Neither timed receipts nor nonzero
PCM proves intelligibility, acoustic delivery, or audible interruption latency.

## Microphone troubleshooting

Start now creates the audio engine and requests resume synchronously in the
button handler, before awaiting access verification. Microphone permission is
still requested only after the key is accepted. A live sound meter and **Resume
audio** control expose paused or inactive capture. Fresh calls say “Connected.
Say hello to start.”; restored calls explicitly mention restored history.

If the interface reaches Listening without a transcript, record the browser,
device, deployment version, and these **Session details** while speaking:

- **Audio engine:** state and advancing clock. Use **Resume audio** when shown.
- **Microphone input**, the live meter, and **Peak input level:** whether the
  track is live or paused and whether samples contain measurable sound.
- **Captured audio** and **Audio sent:** whether capture callbacks run and client
  audio packets are submitted to the WebSocket.
- **Server received** and **Speech service sent:** client audio bytes accepted by
  the conversation and bytes whose provider send returned success. Provider
  keepalive silence is excluded from these server counters.

The counters are displayed as PCM duration; silence also increments them.
Compare their movement, allowing for heartbeat updates and reconnects, rather
than interpreting equal totals as successful recognition. No samples suggests
an audio-engine/capture problem; samples without sound suggests the input device
or capture processing needs investigation. Continued forwarding without a
transcript narrows the next check but does not establish a provider fault.
Export measurements without audio, transcripts, or credentials. The user has
since reported basic live success; retain these steps for recurrence and
structured physical-device acceptance. That confirmation supplies no measured
duration or acoustic timing result.

## Physical microphone/speaker procedure

No step below is passed until real provider audio and browser playback are
observed on an audio device. Record date, deployment version, browser, device,
network, and sample counts. Use headphones to reduce echo-induced interruptions.

1. Open the deployed demo and enter its demo access key. For a new deployment or
   local remote-AI session, follow the setup in README. Confirm Start obtains microphone permission,
   reaches ready, recognizes speech, streams a sensible spoken answer, and End
   releases the microphone. Record at least ten startup samples, distinguishing
   cold and warm sessions.
2. Ask a five-turn conversation involving a name and a previous answer. Confirm
   history stays consistent. Repeat in a separate simultaneous browser session
   with a different name; verify neither session learns the other's context.
3. Say “Please tell me about…” then pause for 0.5, 1, and 2 seconds before
   completing the same thought. Repeat five times per duration. Capture Flux
   events and response onset; measure premature-end and missed-end rates.
4. Interrupt the assistant at least ten times, including near the first audio
   packet, mid-sentence, and with several sentences queued. Record both server
   clear/cancellation events and browser audio scheduling. For audible silence,
   use an external loopback/audio recording with aligned speech-onset and output
   channels. The browser's clear() duration alone is NOT audible stop latency.
5. Interrupt while the language-model request is pending. Ask about fictional
   appointment availability and interrupt during its pending tool delay. Confirm
   old model/tool results cannot produce new playback or mutate played history.
6. Continue a real ten-minute voice call with at least twenty turns and ten
   interruptions. Export browser metrics and collect authenticated DO diagnostics.
   Record provider failures and reconnect gaps. Run at least two simultaneous
   real calls, preferably four, using distinct identifying conversation text.
7. Disconnect the browser network and reconnect. Confirm fresh providers/audio
   and restored acknowledged history; the interrupted utterance may need repeat.
   On a separate test deployment only, enable test routes, issue authenticated
   POST /api/session/<id>/restart?token=<capability>, and confirm the call closes
   and the next connection reconstructs history. A failed restart HTTP response
   is intentional because the object is aborted; this is not seamless recovery.
8. End calls, abandon one without sending messages, and confirm diagnostics show
   zero pipeline tasks, provider sockets/readers, and queued audio. Account for
   late provider requests separately: cancellation of the Python waiter does not
   prove cancellation of remote compute or billing.

The browser Session details export reports scheduling and connection observations.
Authenticated GET /api/session/<id>/diagnostics?token=<capability> returns server
metrics and resource counts; do not publish capabilities. Request query strings
are redacted in the checked-in Worker observability configuration.

To obtain the active browser call's `id` and capability, open browser developer
tools before Start, select Network, and inspect the response to
`POST /api/session`. Its JSON contains `id` and `token`. Use only your own
session's values in the diagnostics/restart URLs above on that same application
origin. Do not paste or share the capability; save diagnostic response bodies
without their request URLs. The browser
metrics export intentionally omits these credentials.

Record whole-isolate memory only if the available Cloudflare runtime metrics or
profiling expose it; otherwise mark it unavailable. Python allocation tracing and
DevTools heap values do not by themselves establish whole-isolate memory or the
amount attributable to a single DO.

The hosted model IDs are pinned by name, not immutable model artifacts. Record
actual provider/model versions when available. Any unsupported Aura-2 WebSocket
response, API-schema mismatch, or missing account entitlement is a failed
integration check, not permission to substitute fixtures and mark voice passed.
