# SFU disconnect while local capture continues

The user reports speaking throughout the failed call. The supplied browser export confirms that microphone samples continued to accumulate after the control connection dropped. It does not contain the server exception or turn trace needed to establish the cause. No conversation behavior has been changed.

## Actual observed sequence

The [original text-free export](../evidence/sfu-connection-drop-20261005/measurements.json) has SHA-256 `b82f33d93545e16601d400593b55ff386c231da60e454024e4d7d10686bda0fb`. Its timestamps below are relative to Start, not UTC or the server session clock.

| Time | Observation |
| --- | --- |
| 16.237 s | Browser reports ready after the SFU connection events. |
| 19.688 s | Final user transcript event. No transcript text is retained. |
| 22.870 s | Speaker detached for disconnect. |
| 22.871 s | First reconnect, close code 1013. |
| 24.610 s | Second reconnect, close code 1006. |
| 45.752 s | Third reconnect, close code 1006. |

Local capture accumulated 1,555,200 bytes of 16 kHz mono PCM16, or 48.6 seconds. The export shows a running audio context and a live, unmuted track. These aggregate counters cannot prove uninterrupted speech, but they establish capture continuing after the first disconnect. In `app.js`, closing the SFU transport leaves the local capture graph running during reconnect; End closes both. The microphone meter therefore cannot confirm that a disconnected call is receiving speech.

The last pong snapshot reports 125,440 received and forwarded bytes, or 3.92 seconds each. These are lower bounds, not the totals at disconnect. Forwarded means successful local submission to the Nova WebSocket, not provider acknowledgment. Zero browser `sentBytes` is expected for SFU because the microphone uses WebRTC instead of direct PCM control messages. The first-audio array is not populated for SFU and cannot establish absent playback.

On reviewed source `39fe5b0`, a final user transcript requires a current COMPLETE semantic decision and finalized nonempty transcript coverage. The export has no loaded-source identifier, so that implication remains conditional on the reviewed source. This capture does not show an INCOMPLETE Smart Turn rejection or a generic turn-abort event. The earlier screenshot with the generic abort remains a separate unexplained failure. The latest screenshot was inspected but not copied into shared evidence; its exact relation to the exported attempt cannot be established from their identifiers.

## Cause still missing

The first-party application has no explicit 1013 close path. Its recoverable shutdown uses 1012; terminal shutdown uses 1000. This excludes an explicit application close with that code, but does not establish a runtime or platform cause. [IANA](https://www.iana.org/assignments/websocket) registers 1013 as Try Again Later, without identifying a unique cause. Do not assign CPU exhaustion from the resampler cost or this code alone.

The browser diagnostic request returned only `serverDiagnosticsStatus: unavailable`. The old export collapsed HTTP rejection, network failure, a five-second timeout and invalid JSON/diagnostic shape into that value. Loss of in-memory events alone would not explain an unavailable endpoint: a healthy restarted owner can return an empty snapshot.

A read-only historical log query for this Worker and the broad failure window returned HTTP 403 / provider code 10000 with an unexpired CLI login. No stored call logs were retrieved. The permitted live log reader is attached and has received our health and disabled-route checks on both deployments, with no exception. As of the recorded check, no new call requests have arrived. Raw logs remain private. See the [investigation record](../evidence/sfu-connection-drop-20261005/investigation.json).

The observation still needed is the runtime outcome and exception for a real SFU connection that closes with code 1013, correlated with its private server trace: decoded/resampled input, forwarded cursor, Nova ranges/finality, hosted request, cancellation and exact abort reason if present. User participation is needed for the physical microphone attempt. No browser automation was attempted or substituted.

## Small diagnostic correction

Commit `cdd8a55051295401a5d7bdc3f33b1233ab7bc02d` retains a bounded HTTP status and one fixed category: `http_error`, `timeout`, `network_error` or `invalid_response`. Reconnect events also preserve boolean `wasClean` and a bounded numeric close code. Exception messages, URLs, response bodies and arbitrary close reasons remain excluded. Call flow, retry timing, microphone behavior, turn decisions, models, authorization and visible notices are unchanged.

Five new tests fail on the prior source. An independent archive of the exact correction passes all 111 browser and script tests. The pinned Python Workers build passes. The first deployment attempt uploaded the asset but failed with network code EPIPE; inspection confirmed the previous deployment remained active. The same tested source was retried successfully as `742a8942-84b6-4247-a3b0-e193af3855b4` at 100% traffic. Both diagnostic assets match the source. Health and the SFU page return 200; the checked fixture URL returns 404 and configuration still disables fixture routes. [Deployment and preserved failure](../evidence/sfu-connection-drop-20261005/deployment.json), [independent checks](../audit/results/sfu-export-status-independent-20261005.json).

Retain immediate previous version `425815d1-a965-47bc-b863-4dad470ad0a1`, requested rollback `06111df0-88ce-4142-96e7-4158a9a6e7a9` and original rollback `3f8bd208-8e4a-4f2d-96cb-d9a0e51f9542`. The silence fallback remains disabled. No new microphone, audible playback or follow-up pass has been obtained. B2 remains failed, B1 blocked and performance acceptance deferred.

For the next physical check, confirm the live reader is attached, end the old call, reload `/webrtc` in Chrome and start one fresh call. Speak as before for about 15 seconds after Listening appears, then End and report completion. If a server trace can be downloaded while the call is still active, preserve its sanitized export too. Establish the cause before any conversation fix; then reproduce and retest the affected behavior on the exact revision.
