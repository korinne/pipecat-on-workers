# Two Pipecat transports on Workers

The home page links to two complete browser clients that share the same real
Pipecat pipeline in a Python Durable Object:

| Example | Browser audio path | Control messages |
| --- | --- | --- |
| `/websocket` | Browser ↔ Durable Object WebSocket; 16 kHz mono PCM in, 24 kHz mono PCM out | Same WebSocket |
| `/webrtc` | Browser ↔ Cloudflare Realtime SFU over WebRTC; SFU ↔ Durable Object over two WebSocket adapters | Separate browser–DO WebSocket plus authenticated HTTP signaling |

Both use Workers AI for Deepgram Flux recognition, Llama response generation,
and Aura speech. Both require the same demo access key. Pipecat does not move
into an SFU, container, or separate Python server.

## Read the code

- `public/index.html`: example chooser.
- `public/websocket.html`, `public/webrtc.html`: the two call pages.
- `public/app.js`: shared access, microphone, transcript, and call controls.
- `public/sfu-client.mjs`: browser peer connections and SFU signaling.
- `src/entry.py`: session creation, immutable transport choice, authenticated
  signaling, and scoped SFU media callback URLs.
- `src/conversation.py`: shared Pipecat turn processing, model, speech,
  interruption, and history. Optional `audio_transport` selects SFU output.
- `src/sfu_transport.py`: SFU REST requests, adapter ownership, pacing,
  per-generation output tracks, and cleanup.
- `src/sfu_codec.py`: the adapter protobuf envelope and streaming PCM resampling.
- `src/providers.py`: shared Workers AI provider connections.

## SFU flow

1. Create a session with `POST /api/session`, `X-Demo-Key`, and
   `{"transport":"webrtc"}`. Open its control WebSocket using the returned
   session capability, just as for the WebSocket example.
2. Once Pipecat/providers are ready, the browser publishes its microphone using
   a WebRTC offer. The DO creates an owned SFU session and publishes the track.
3. After that peer connects, `input_ready` creates an egress adapter. The SFU
   dials the DO's private-capability WebSocket and sends 48 kHz stereo PCM in
   protobuf packets. Python converts this to 16 kHz mono for Flux.
4. The first synthesized audio of a reply creates an ingest adapter and fresh
   SFU publication. The DO announces `sfu_track` over the control channel.
5. The browser creates a fresh receiving peer. `subscribe` returns an SFU offer;
   the browser gathers an answer and sends `renegotiate`. Once its track is
   attached and the peer is connected, `playback_ready` allows PCM submission.
6. The DO converts Aura's 24 kHz mono output to 48 kHz stereo and paces it in
   20 ms protobuf frames. The SFU delivers that audio to the browser via WebRTC.
7. Interruption clears browser playback immediately and retires the old output
   publication. A fresh receiving peer for each reply prevents late packets
   from an interrupted reply becoming audio for a later reply.
8. End closes peer connections, adapters, owned tracks, providers, and Pipecat.

SFU app credentials stay on the Worker. Browser signaling only selects actions
on this conversation's owned resources; it cannot supply arbitrary SFU session
IDs. SFU callback URLs have separate HMAC capabilities scoped to their exact
role/path/generation and live connection. They expire when the call disconnects.

## Configure the SFU example

Create an app in Cloudflare **Realtime → Serverless SFU**. Save its ID and
generated app secret as Worker secrets (enter each value at the private prompt):

```sh
npx wrangler secret put REALTIME_SFU_APP_ID
npx wrangler secret put REALTIME_SFU_APP_SECRET
uv run pywrangler deploy
```

`REALTIME_SFU_BEARER_TOKEN` is also accepted as an alias for the secret, matching
Cloudflare's example. The demo key is a separate `DEMO_ACCESS_KEY` secret.
Creating an app through the management API requires the **Calls Write** account
permission; an ordinary Workers-only Wrangler login is insufficient.

For local development, add the two SFU values to a private `.dev.vars` file.
The SFU must be able to reach a public HTTPS/WSS URL for the DO callbacks, so a
plain localhost deployment cannot exercise this path. Use a deployed Worker
for the complete WebRTC example. Never commit `.dev.vars` or app credentials.

## Limits of this spike

- `playback_ready` and submitted-byte counts are **not playback receipts**.
  The SFU path persists user history but omits unconfirmed assistant replies
  from restored/model history. The visible transcript shows generated text.
  The WebSocket path retains its sentence-level receipt behavior.
- A fresh output peer per reply adds negotiation latency. This is a simple
  stale-audio isolation baseline, not an optimized media-session design.
- The browser uses Cloudflare STUN; there is no separately provisioned TURN
  fallback in this spike. Test restrictive networks before wider use.
- Cloudflare's WebSocket media adapter is beta. Cleanup diagnostics distinguish
  known owned resources from uncertain allocations after a lost API response.
- Existing long-duration evidence is for the WebSocket implementation and its
  recorded version, not automatic validation of the new SFU path.

## Verification

On deployment `ae7676d0-f24e-49cc-98ab-1d79290f33ed`, both example pages were
verified against local source. The WebSocket example passed a real-provider
synthetic recorded-speech round trip and cleanup. See
[evidence/two-examples.json](evidence/two-examples.json). SFU activation on version `acc191fa-56ef-4fc7-a4fa-8545061a079d` then passed
two generated-speech turns through real WebRTC, with nonzero decoded response
audio in generations 3 and 5. The browser End flow completed. See
[evidence/sfu-activation.json](evidence/sfu-activation.json) for scope and limits.
No echo-cancellation or interruption behavior was changed during activation.

The unlinked `/transport-check.html` developer page accepts a key text file and
a prerecorded speech WAV. It publishes a synthetic WebAudio track via real
WebRTC, shows received RTP and nonzero decoded-audio counters, and supports
interrupt/replay/End without opening a physical microphone. It never sends
playback receipts. SFU credentials are now configured on the deployed Worker; counts alone do
not establish audible playback.

```sh
node --test public/*.test.mjs
uv run python -m unittest discover -s tests -v
uv run python scripts/check_sfu_entry.py
uv run python scripts/check_entry_lifecycle.py
uv run python scripts/check_access_auth.py
```

The SFU tests use fake REST/media boundaries and real Pipecat queues for the
conversation integration cases. They cover negotiation order, cleanup,
interruption, audio conversion, pacing, and capability isolation. They do not
by themselves prove live WebRTC negotiation or audible playback.

References: [Cloudflare AI audio example](https://developers.cloudflare.com/realtime/sfu/examples/ai-audio/),
[WebSocket media adapters](https://developers.cloudflare.com/realtime/sfu/features/media-transport-adapters/websocket-adapter/),
[SFU connection patterns](https://developers.cloudflare.com/realtime/sfu/get-started/connection-patterns/).
