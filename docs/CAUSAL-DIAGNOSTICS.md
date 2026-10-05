# Trace the failed laptop call

The exact cause of the reported SFU failure remains unknown. The preserved screenshot and user report contain no server events, call capability, or measurements. On the reviewed deployed source, the visible error requires an active Nova turn but does not identify which of seven abort reasons fired. No conversation behavior was changed in this investigation.

One diagnostic defect is established and fixed: the browser export removed `transcript_beyond_pause`, `pause_audio_unavailable`, `detector_busy`, and `smart_turn_failed`. New regression tests fail on the starting source and preserve all seven reasons after the correction. The failure logs and [investigation record](../evidence/causal-trace-20261005/investigation.json) are retained.

## Deployed diagnostic revision

Source `39fe5b0d78770ba78d9f087c751907b8e0c37cef` is deployed as `425815d1-a965-47bc-b863-4dad470ad0a1` at 100% traffic. Both changed browser assets match the source. The immediately previous version is `3b1dcd04-5515-4b83-9fe4-27511b619c03`; the requested rollback `06111df0-88ce-4142-96e7-4158a9a6e7a9` and original rollback remain recorded. [Deployment](../evidence/causal-trace-20261005/deployment.json)

Commit `b85efeb` corrects the shared export and redacts free browser error messages. Commit `39fe5b0` adds bounded server tracing. The model configuration, public session creation, private call/media authorization, standard Pipecat speech/context path, and both transports are unchanged. Fixture routes and the unapproved three-second fallback remain disabled.

The measurements now preserve:

- Existing browser microphone counters and activity, plus SFU accepted packet counts, payload bytes, resampled PCM bytes, callback count, first callback/PCM timing, and the last adapter sequence/timestamp.
- Sampled input/forwarding totals and the per-Nova-connection audio cursor. Progress samples occur at the first packet and roughly once per audio second.
- Sanitized Nova event types, onset/result timestamps, first/last word boundaries, final flags, text lengths, and connection/revision identity. Rejected events carry a fixed validation label; invalid word ordering includes the offending numeric boundary.
- Snapshot boundaries, transcript coverage, detector request/outcome, cancellation, and the exact abort reason with the state before discard.
- Hosted binding request sequence, actual submission, local cancellation/timeout, still-pending requests, busy/capacity rejection, and eventual settlement. Local cancellation does not establish remote cancellation or stopped billing.

Only the latest 500 server events survive. New browser exports use `measurementSchema: 2`; the existing browser arrays retain at most 1,000 entries. No transcript text, recordings, capabilities, remote resource identifiers, or free provider exception text is added to shared evidence. Browser error messages become fixed codes in the exported copy; visible errors are unchanged.

Server event `elapsed_ms` uses the session clock. Nova timing fields use provider seconds; cursor/snapshot fields count 16 kHz mono samples. SFU first-callback/PCM offsets use the transport creation clock, and the packet timestamp retains the adapter's raw value. Browser and server snapshots occur at different times. Do not subtract these separate clock origins or infer that a missing retained event never happened.

## Verification

An independent complete archive of exact commit `39fe5b0` passed 175 Python tests, 106 JavaScript tests, provider checks, 48 access cases, seven lifecycle cases, and 16 SFU-entry tests. All archived source files stayed unchanged. Configuration, lockfiles, vendored Pipecat, and the compatibility patch match the starting revision. The pinned deployment dry run passed. [Independent record](../audit/results/causal-diagnostics-independent-20261005.json)

One [post-deployment direct provider call](../evidence/causal-trace-20261005/direct-provider.json) passed with the existing 1.834-second synthetic fixture. Its trace records Nova onset at 0.09 provider seconds, a finalized 0–2.13-second transcript range, a detector snapshot ending at the 1.68-second word boundary, one submitted/completed hosted request, one completed user turn, and 98 nonzero output chunks acknowledged by software. End released the tracked local resources. The probe's small diagnostic-only export patch is preserved beside the result.

This call used no microphone or speaker and did not exercise SFU. It does not explain the user's failure, verify audible output, or establish follow-up behavior. B2 remains failed from the user reports. B1 remains blocked by published-package requirements; performance acceptance remains deferred.

The first browser test harness omitted the new sanitizer import and failed; its corrected run passes. The independent archive harness initially used an unsupported system-Python API and was repeated with Python 3.14.7 before tests ran. An initial Python-urllib asset read returned 403; ordinary Node requests returned 200 and exact asset hashes. These failures are preserved with their separate scopes. No browser automation was used.

## Missing observation and next check

Reload `/webrtc` in Chrome, start a new call, and speak one short question using the laptop setup that failed. At the first error, immediately use Session details → Download measurements while the call is still active, then End. Supply the saved file path and whether a transcript appeared or speech was audible. If it answers, try one short follow-up before exporting. Avoid personal speech during this functional check.

The needed observation is the failed call's `turn_discarded.reason`, together with the preceding Nova event/range, audio cursor, SFU decoded/forwarded counts, finality/coverage, and hosted request/cancellation sequence. Inspect those before changing behavior. Reproduce the identified defect in a bounded test, make the smallest fix, test the exact resulting revision, deploy, and repeat the failing laptop case on SFU and then WebSocket. A successful automated direct call cannot replace those observations.
