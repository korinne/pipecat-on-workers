# Bounded SFU browser check

Open `/transport-check.html?once=1` in Chrome on the deployed Worker. This developer page sends a selected WAV as a synthetic WebRTC track through the normal public session and private per-call authorization flow. Ordinary demo pages are unchanged.

Choose a speech WAV no longer than 15 seconds and click **Start check**. Speaker volume stays at zero. The page opens no microphone and sends no playback receipts. It stops after one completed response or 90 seconds. Replay and Interrupt are disabled in this mode. The ordinary developer page retains those controls for manual checks.

A completed response requires one final user transcript, assistant text, the matching generation’s listening event, incoming RTP bytes, and nonzero decoded remote samples. The page waits another second for queued remote audio before sending End. The visible JSON reports the observations separately from cleanup. A `completed` response with `unresolved` cleanup is not a lifecycle pass.

The remote track is captured through the existing audio worklet as PCM16 mono at 16 kHz. **Save received audio WAV** exports that browser-decoded audio. Capture stays in memory, has a 30-second limit, and is discarded on the next check or navigation. It does not establish physical playback, exact words heard, or output timing at the device.

After End, the page makes at most eight private diagnostics requests, each with a two-second timeout and a half-second interval. It reports `released` only when all expected resource counts are present and zero, the session is closed, and cleanup uncertainty is false. Missing diagnostics remain unavailable or unresolved. A new check is disabled until this bounded polling finishes. Tokens, session identifiers, resource identifiers and provider error details are excluded from the JSON.

For evidence, add `&revision=<40-character commit>&deployment=<version UUID>`. These are recorded as supplied metadata; verify them against the deployment record before treating the result as evidence of that revision. Save the visible JSON separately, record the browser/network and input source, and inspect the displayed transcript. Keep historical results intact. Fixture tests for the page run with `node --test public/transport-check.test.mjs`; they do not establish live SFU behavior.

A browser check with prerecorded speech leaves real microphone use, physical playback and performance acceptance untested. Numeric performance limits must be agreed before making those claims.
