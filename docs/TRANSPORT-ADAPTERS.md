# Audio delivery adapters

`WebSocketAudioTransport` in `src/audio_transport.py` owns direct-browser PCM encoding, chunk identifiers and bounded playback credit. Its internal interface matches the existing SFU adapter: `send_audio`, `finish_generation`, `clear`, `played`, `close` and `diagnostics`. The shared speech/output pipeline supplies 24 kHz mono PCM16 and controls assistant context. The adapter has no history writer.

The WebSocket adapter sends base64 PCM with its sample rate, generation and chunk identifier. Browser acknowledgements release queue credit. It rejects duplicate, unknown and stale acknowledgements. A successful send reports submission, not physical playback. Clearing advances the generation floor, releases obsolete credit and wakes a blocked sender; closing also prevents later sends.

Pending output is bounded by 384,000 bytes and 256 chunks. A blocked sender waits up to 12 seconds for credit before failing. With 20 ms output writes, the chunk bound limits pending audio to 5.12 seconds. The output pipeline owns pacing and text/audio ordering. These are application limits, not approved latency or capacity targets.

The SFU adapter owns signaling, authenticated media callbacks, 48 kHz stereo packet conversion, generation-specific resources and output pacing. Both adapters expose delivery and cancellation to the same speech pipeline. Neither chooses a separate assistant-history policy.

The [focused adapter checks](../audit/results/task4-direct-adapter.json) exercise PCM format, acknowledgement validation, both queue bounds, interruption, timeout, End, send failure and a fresh connection. They use local Python and captured events. The adapter is added independently of the speech-pipeline wiring so the transport and conversation changes remain reviewable. Live browser delivery and physical playback require separate evidence.
