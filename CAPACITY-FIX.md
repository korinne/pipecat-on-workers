# Speech startup capacity handling — 24 September 2026

The reported `stt did not return a WebSocket (HTTP 429)` was traced to Workers AI
error **3040: Capacity temporarily exceeded** by a direct authenticated Flux
upgrade request. The provider response did not report quota exhaustion (3036).
The demo access key had already succeeded at session creation.
[Cloudflare documents these distinct error codes](https://developers.cloudflare.com/workers-ai/platform/errors/).
The sanitized observation is in [provider-capacity-3040.json](evidence/provider-capacity-3040.json).

Deployed fix: `a0fddf09-a341-4b07-bd50-84adcc2ffed1`.

- Only a known HTTP 429 / error 3040 retries: at most three sequential speech
  recognition connection attempts within an 18-second budget. Default waits are
  one and two seconds. A numeric Retry-After is respected if it fits; otherwise
  the connection fails clearly. Quota, unknown 429, and request timeout failures
  are not retried automatically.
- Rejected response bodies are bounded to 8 KiB and one second of reading,
  with cancellation and reader release. Only allowlisted error codes influence
  the displayed message; raw provider text is never sent to the browser.
- Exhaustion ends the call and releases microphone/playback resources instead
  of multiplying requests through the browser reconnect loop.
- End/disconnect cancels the owned startup wait. Repeated stop events do not
  cancel cleanup a second time. Late JavaScript results retain disposal ownership.

Validation: 21 Python tests plus eight subtests, 21 browser tests, 12 harness
checks, provider-adapter regressions, seven isolated startup lifecycle cases,
and the actual local Python-DO lifecycle suite passed. Local checks cover
restart, reconnect, two-session separation, End, and abandonment.

The final deployed recorded-speech check **passed**: interrupt during pending
model generation before audio, observe a new generation clear, then complete a
new spoken response. It received 87 nonzero PCM chunks, acknowledged 8.64 seconds
of emulated playback, observed no canceled-generation audio after clear, and
closed with every owned-resource counter at zero. This is one two-input trial;
remote compute cancellation and physical acoustic playback are not established.
See [current model cancellation and recovery evidence](evidence/real-provider-model-cancellation-capacity-fix.json).
The earlier startup-only probe also connected successfully; it is tagged with
its intermediate deployment version in [capacity-startup-check.json](evidence/capacity-startup-check.json).

The ten-minute provider trial remains evidence for the earlier deployment
`69b1b14d-e27b-4ebc-8623-33dc94d5c186`; it has not been rerun on this fix.
Its deployment manifest is preserved in
[deployment-validated-69b1b14d.json](evidence/deployment-validated-69b1b14d.json).
The capacity-fix source manifest is preserved in
[deployment-capacity-a0fddf09.json](evidence/deployment-capacity-a0fddf09.json).
The subsequent authentication-only update keeps the same conversation/provider
implementation. Current source hashes and deployment status are in
[deployment.json](evidence/deployment.json).

Cloudflare capacity can still be unavailable after these bounded retries.
Physical microphone/speaker acceptance remains outstanding.
