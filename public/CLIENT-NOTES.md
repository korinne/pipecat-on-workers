# Browser client contract and evidence

The client uses no third-party libraries. `index.html` is an example chooser.
`websocket.html` and `webrtc.html` load the shared `app.js`; the body’s
`data-transport` selects the audio path. Both load the AudioWorklet on the same origin. HTTPS or localhost is required for microphone
access. The access key is sent as `X-Demo-Key` when verifying access or creating a
session; the returned conversation capability stays in memory.

## Shared control and WebSocket audio contract

- `POST /api/session` with `{transport:"websocket"|"webrtc"}` → `{id, token}`.
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
timeout is 45 seconds, checked every 3 seconds. End closes media tracks, audio
nodes/context, timers, and the socket. Page exit also attempts End; delivery on
page exit is best-effort and requires server abandonment cleanup.

## Verification

`node --test public/*.test.mjs`: **50 passed**, covering audio generation
invalidation, exact whole-chunk ack behavior, late first audio during pending
generation, duplicate packets, bounded queues, PCM endian conversion,
16/44.1/48 kHz resampling, Start/Mute/End cleanup, bounded reconnect, stale socket
messages, reset history replacement, restored-turn metric exclusion, terminal
server closure, session time-limit cleanup, failed creation, access-key headers, audio startup/resume, and SFU signaling lifecycle. Tests use fake browser
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


## SFU / WebRTC audio contract

`webrtc.html` uses `sfu-client.mjs`. The browser publishes its microphone track
through a send-only `RTCPeerConnection`. The local AudioWorklet remains connected
for the input meter and fast speech-onset detection; its PCM is **not** uploaded
on the application WebSocket. Muting disables the published microphone track.

All signaling uses `POST /api/session/{id}/sfu` with `X-Session-Token` containing
the in-memory conversation capability. The browser never receives an SFU app
secret, chooses arbitrary SFU session IDs, or sends the demo key to SFU APIs.

- On control `ready`, publish with `{action:"publish", sessionDescription:offer,
  mid}` after gathering the microphone offer’s ICE candidates. Apply the returned
  answer and wait for the input peer to become connected, then send
  `{action:"input_ready"}`. The server creates its input adapter only after this
  readiness message. The UI waits for that request before reporting Listening.
- On `{type:"sfu_track",generation}`, detach the previous remote audio and create
  a fresh peer without adding a transceiver or creating a local offer. Send only
  `{action:"subscribe",generation}`. The SFU initiates this receiver’s negotiation
  with an offer. One receiver per response prevents a retired response’s jitter
  buffer being reused by the next response.
- Apply the SFU’s output offer, create a local answer, and wait for ICE gathering
  to complete. Send `{action:"renegotiate",role:"output",generation,
  sessionDescription:answer}` and await its completion. Starting a local offer
  before applying the SFU offer would collide in `have-local-offer` state.
  Operations for a peer are awaited in order; duplicate generation announcements
  do not start a second negotiation. Replaced peers reject late results.
- Once the output peer is connected and has a remote track, send
  `{action:"playback_ready",generation}`. This is readiness to receive, **not** a
  playback receipt. Waiting for `audio.play()` to resolve before readiness could
  deadlock a sender waiting for readiness before sending media.
- Incoming audio uses a `MediaStream` on the hidden audio element. Rejected
  autoplay displays the existing Resume audio action. The AudioContext is still
  created/resumed synchronously in the Start click, before authentication waits.
- Interrupt locally mutes and detaches the output track, closes that receiver,
  raises its accepted generation floor, and sends the existing `interrupt`
  control packet. Late signaling and track events are rejected by peer identity
  and generation. The backend must retire the interrupted SFU track/adapter.
- Control disconnect closes both peers; bounded control reconnect creates a new
  publisher after `ready`. End aborts active requests, cancels waits, closes both
  peers, and releases the normal capture resources. A terminal media failure
  ends the call with a visible error.

There is no browser-generated `played` packet in SFU mode. Assistant previews can
appear in the transcript, but the backend does not treat sending audio into the
SFU as proof of playback, and does not restore that assistant text as confirmed
played history. Thus this example deliberately has weaker assistant history
continuity than the WebSocket version. This is disclosed in Session details.

The SFU example reports local speaker-detachment time; it does not claim that
this is acoustic silence, measure per-turn first-audio time, or compare the
browser’s internal WebRTC jitter buffer to the WebSocket queue. No SDP,
credentials, conversation identifiers, transcript, or raw audio is exported in
measurements. The thirteen direct SFU transport tests use fake peers and exercise
separate negotiation, input readiness, receiver ICE completion, serialized
renegotiation, duplicate and stale generations, interruption, late track
rejection, cancellation, input ownership, autoplay recovery, and readiness that
does not wait for media playback. Deployed ICE/SFU behavior and
physical audio still require an end-to-end run.

## Developer transport acceptance page

`/transport-check.html` is deliberately not linked from the homepage. It uses
normal authenticated conversation and signaling endpoints; no test-only backend
route or embedded recording is required. Choose the access-key text file and a
prerecorded RIFF/WAVE speech clip (at most 20 MB and 60 seconds), then select
**Start check**. The page never requests physical microphone access.

The page creates a WebAudio `MediaStreamAudioDestinationNode`, publishes its
track through the same `SfuAudioTransport`, and plays the selected recording into
that track only after `publish()` has completed its `input_ready` request. A
silent source keeps the track alive before and between recordings. The recording
itself is not played through the local speakers; the returning agent audio is.

The visible counters show scheduled input duration, server/provider input
bytes expressed as milliseconds, current generation, received RTP audio bytes
and packets when available, and decoded-sample windows with nonzero RMS. Output
RTP and sample counters are per receiver/generation; they reset for the next
valid response track. A silent analyser observes the incoming MediaStream
independently of the audio element. Nonzero samples establish decoded remote
media, not acoustic playback or playback receipts. No `played` packets are sent.

**Interrupt** detaches the current receiver immediately and sends the normal
interrupt control packet. **Replay input**, enabled after the clip finishes,
plays the same recording again to exercise the following response generation.
**End** stops the source, closes peers/control socket, stops the synthetic media
track, disconnects monitoring nodes, and closes the AudioContext. Late startup
completion is discarded; a session created after End is explicitly ended.
Unlike the ordinary demo, a disconnected acceptance check stops rather than
silently reconnecting, so a failed run remains visible.

Four fake-browser harness tests cover readiness-gated input, incoming sample/RTP
observation without receipts, interruption/replay, End during media negotiation,
and late session creation. These tests do not replace running the page against
the deployed SFU. The full public test suite currently has **50 passing tests**.
