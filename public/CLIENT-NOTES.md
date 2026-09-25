# Browser client contract and evidence

The client uses no third-party libraries. `index.html` loads `app.js` and the
AudioWorklet on the same origin. HTTPS or localhost is required for microphone
access. An optional access key is sent as `X-Demo-Key` only when creating a
session; the returned conversation capability stays in memory.

## Wire contract

- `POST /api/session` → `{id, token}`.
- `WS /api/session/{id}?token={token}`.
- Client audio: `{type: "audio", data: <base64 little-endian PCM16>, sample_rate: 16000}`.
- Server audio: `{type: "audio", data, sample_rate: 24000, generation, chunk_id}`.
- Server `ready` and `status` include `generation`, including while a model or
  tool is pending. This lets local speech onset invalidate audio before its
  first packet arrives.
- Server `clear.generation` is the **minimum new generation accepted**, after
  the server increments its generation. Lower generations are discarded.
- Client `played` contains `generation` and `chunk_id`; sent only after the
  entire chunk finishes playing naturally. Interrupted chunks are never acked.
- Client `interrupt` is a latency optimization on 60 ms of microphone speech;
  the remote provider owns turn completion. Microphone energy threshold is
  fixed at RMS 0.018, so headset/noisy-environment testing is still needed.
- Transcript packets contain `role`, cumulative `text`, and `final`. A final
  packet completes the current transcript row. A reset may contain `history`
  with `text` or `content` and begins a new ordered stream epoch.
- Other packets: `status`, `error`, `end`/`ended`, `ping`/`pong`.

## Audio and lifecycle bounds

Capture uses the actual context sample rate, including 44.1/48 kHz, and emits
20 ms frames after fractional-window resampling to 16 kHz. This is a small
box-filter resampler, not a studio-quality anti-aliasing filter. Muting sends
silence so server turn detection can finish an utterance.

Playback schedules a 300 ms horizon in at most 100 ms stoppable slices. Total
audio queued is capped at 12 seconds; exceeding it visibly interrupts instead
of silently dropping arbitrary chunks. The scheduler can extend at most one
slice beyond its horizon. Disconnect stops playback immediately; reconnect
reuses the capability and tries at most four times (0.5/1/2/4 seconds). Heartbeat
timeout is 45 seconds, checked every 15 seconds. End closes media tracks, audio
nodes/context, timers, and the socket. Page exit also attempts End; delivery on
page exit is best-effort and requires server abandonment cleanup.

## Verification

`node --test public/*.test.mjs`: **20 passed**, covering audio generation
invalidation, exact whole-chunk ack behavior, late first audio during pending
generation, duplicate packets, bounded queues, PCM endian conversion,
16/44.1/48 kHz resampling, Start/Mute/End cleanup, bounded reconnect, stale socket
messages, reset history replacement, restored-turn metric exclusion, terminal
server closure, session time-limit cleanup, failed creation, and access-key headers. Tests use fake browser
I/O and do not establish microphone permissions or real audible playback.

The initial layout and expanded measurements panel were inspected in the Codex
in-app browser using a local static server. Backend integration and physical
audio measurements require a running configured backend.

## Measurement limits

The optional panel exports startup time, final-transcript-to-first-scheduled-
audio time, local source-stop scheduling duration, reconnects, and bounded event
logs. Startup includes microphone permission/setup. Response timing excludes
recognition latency. Source stop and `onended` describe Web Audio scheduling,
not physical speaker silence; output device latency remains unmeasured. No
audio, transcript, access key, session token, or identifier is exported. The
transcript can include generated words that never became audible; authoritative
context must use the server's played-ack accounting.
