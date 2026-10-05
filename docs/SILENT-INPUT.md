# SFU call with no visible transcript

## Diagnostic update, 5 October 2026

The diagnostic-only revision `39fe5b0` is now deployed as `425815d1-a965-47bc-b863-4dad470ad0a1`. The export retains all seven abort reasons and bounded SFU/Nova/provider timing. Independent checks and one direct synthetic provider call pass; the failed laptop call still lacks its actual trace. Conversation behavior and the unapproved silence fallback are unchanged. See [causal diagnostics and next capture](CAUSAL-DIAGNOSTICS.md). The earlier deployment and findings below are preserved as history.


The user reported speaking without any visible response. The [second screenshot](../evidence/silent-input-20261005/user-report.json) shows Listening at 00:06, no transcript and a local microphone activity message. The earlier screenshot containing “hello hello” is a separate attempt. The new image establishes local sound detection, but does not establish SFU delivery or Nova recognition. The exact cause of this call remains unknown without its counters or events.

Controlled tests reproduced two ways the application could fail without a useful explanation:

- The application accepted SFU allocation metadata without verifying the current authenticated input callback or PCM. A controlled successful allocation response with no callback let the browser report Listening with no audio at the Worker. This does not establish that the real provider returned such a response in the user’s call. Input readiness now has an 18-second total budget for allocation and the first nonempty decoded PCM delivered through the current callback. Silent PCM counts; recognized speech is not required. End, failure and replacement invalidate readiness. Late allocations remain owned by cleanup. This is an operational setup deadline, not an accepted performance target.
- Nova Results without an accepted speech-start event were discarded without a user error. An invalid initial onset also produced only an internal diagnostic. Those cases now report one recoverable speech-start error until a fresh valid onset or connection. The rejected audio range cannot later revive a transcript or trigger inference. Empty results and old connection/range events remain quiet. An input warning leaves an existing assistant response available for interruption.

The browser now preserves the current response label if setup completes while a reply is already underway. Its microphone hint explicitly describes local sound. Download measurements obtains the call's existing private diagnostics with a five-second deadline and exports only known counters and selected events. It includes SFU callback/PCM readiness, dropped input, hosted decisions and response-failure stages. Transcripts, audio, private tokens and remote resource identifiers are excluded. The browser snapshot is frozen before the request, and a new call starts fresh measurements. A failed diagnostics request still downloads the browser snapshot.

These changes do not enable the pending three-second silence proposal or change the selected models. A valid INCOMPLETE hosted decision still cannot trigger a reply. B1 remains blocked by the published-package dependency requirements; B2 remains failed until a real conversation succeeds. Performance acceptance remains deferred.

The native Chrome inspection timed out. A separate in-app browser check was denied because the browser security system could not verify its admin-enforced policy. No alternate browser automation was used. Browser SFU media and audible playback remain unverified on this revision. The user was asked whether Captured audio, Server received and Speech service sent advance while speaking.

## Verification and deployment

[Independent verification](../audit/results/silent-input-independent-20261005.json) passed on source `f8835e8`: 168 Python tests, 96 JavaScript tests, 16 SFU entry checks, seven lifecycle cases and 48 access cases. The exported source stayed unchanged throughout. Vendored code and dependency/Worker configuration match `3f62b68`. The initial verification harness omitted a committed fixture; that failure and the corrected complete-export run are both preserved.

The [SFU fixtures](../audit/results/sfu-input-readiness-fixed.json) cover 33 input, output and cleanup cases, including missing callbacks, silent PCM, bad packets, replacement at the readiness boundary, End and late allocation. An initial new-test expectation of zero resources was too strong: when a held allocation exhausts the session's cleanup retries, the late adapter closes but the session remains explicitly unresolved. The final check requires that checkpoint to remain visible and verifies that the retry budget does not grow. This does not prove remote cleanup.

The [Nova before/after reproductions](../evidence/silent-input-20261005/) preserve silent rejection and the corrected recoverable error. The initial failing draft regression log and later passing runs are retained alongside them. The [browser integration checks](../audit/results/input-error-browser-20261005.json) cover private diagnostic filtering, call isolation and current-response preservation. A pinned-tool build dry run passed.

Source `f8835e8623040c0d0e8b264c1ac28486255f5f63` is deployed as `3b1dcd04-5515-4b83-9fe4-27511b619c03`, confirmed at 100% traffic. Rollback is `06111df0-88ce-4142-96e7-4158a9a6e7a9`. [Deployment record](../evidence/deployment-silent-input-3b1dcd04.json).

One [post-deployment provider check](../evidence/silent-input-20261005/direct-provider-regression.json) passed through the direct WebSocket route. Real Nova recognized a synthetic question about France's capital, the hosted turn completed, GPT-OSS answered Paris, and Aura returned 106 chunks of nonzero PCM. The response completed with timed software receipts, and End reached zero tracked local resource counters. No microphone or speaker was used. This does not verify the SFU input path or the user's failure, and it does not clear the known follow-up/pause failures or performance acceptance.
