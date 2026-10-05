# Debug the failing laptop call

## Latest laptop evidence, 5 October 2026

The new browser export records 48.6 seconds of local capture, a final user transcript event, then a control close 1013 and two failed reconnects with code 1006. The last pong confirms at least 3.92 seconds of server-received and locally forwarded audio. Server diagnostics were unavailable, so the cause remains unknown; this is not evidence of an INCOMPLETE Smart Turn rejection. Diagnostic-only source `cdd8a55` is deployed as `742a8942-84b6-4247-a3b0-e193af3855b4` and now preserves diagnostic HTTP failures and close cleanliness. All 111 JavaScript checks pass in an independent archive, and served assets match. No fresh physical conversation pass exists. See [the failed call, limits and next observation](SFU-CONNECTION-DROP.md). Conversation policy and the unapproved silence fallback are unchanged. Earlier findings below retain their historical scope.


Investigate one failed real browser call and establish its cause before making another conversation-behavior change. The user reports inconsistent WebSocket replies and missed follow-ups, an earlier SFU setup error, then SFU calls with no reply or transcript. An earlier screenshot explicitly shows “Speech turn could not be completed; please repeat your request.” Prior controlled fixes and one recorded WebSocket round trip have not established that the user's SFU conversation works.

## Starting state

Repository: `/Users/korinne/Documents/Codex/2026-10-03/alri/outputs/pipecat-on-workers`. Branch: `codex/transport-contract`. This investigation began clean at `8d3f1964093372a21b581d5aa9a594ddd92024f9`. Current application source is `cdd8a55051295401a5d7bdc3f33b1233ab7bc02d`, deployed as `742a8942-84b6-4247-a3b0-e193af3855b4`; later commits record evidence and handoff only. Verify the checkout and preserve unrelated work. The previous deployment is `425815d1-a965-47bc-b863-4dad470ad0a1`. Retain rollback `06111df0-88ce-4142-96e7-4158a9a6e7a9`. Do not restart implementation from the original `58ccef5` commit.

Read [Continuation handoff](CONTINUATION-HANDOFF.md), [Silent input](SILENT-INPUT.md), [latest report](../evidence/sfu-connection-drop-20261005/user-report.json), and the original GOALS, IMPLEMENTATION-PLAN, CONVERSATION, CAPABILITY-REQUEST, ACCEPTANCE and EVIDENCE documents. Use BASELINE, TRANSPORTS and DEVELOPMENT for reproduction. Apply Humanizer to written explanations.

Keep Python Workers with one Durable Object per call, Workers AI Nova-3, hosted Smart Turn v2, GPT-OSS-120B low/2048, Aura-2 Luna, direct WebSocket and Cloudflare SFU, and the selected standard Pipecat speech/output/assistant-context behavior. Public session creation, private per-call tokens and signed media authorization remain required. Fixture routes stay disabled in production. Deployment of tested changes to this existing Worker remains authorized. Make small coherent commits and preserve failures.

## What this error establishes

On `f8835e8`, the exact message has one emitter: `NovaTurnCoordinator.abort()` in `src/smart_turn.py`, when the turn was active, notifications are enabled and the coordinator is not closed. Active state requires an accepted Nova speech-start event. This is stronger evidence than the earlier screenshot containing only a local microphone hint. It still does not establish valid transcription, a hosted completion decision or successful output.

Seven reasons can reach this message: `invalid_nova_event`, `transcript_beyond_pause`, `pause_audio_unavailable`, `detector_busy`, `smart_turn_failed`, `turn_readiness_timeout`, and `pipecat_turn_closed`. Do not assume an INCOMPLETE Smart Turn decision caused this particular call. The visible Listening label is reset by the error handler; the 00:01 clock begins at browser readiness, not at server turn onset.

The private diagnostics endpoint retains the abort reason. The old `f8835e8` browser export dropped four valid reasons. Revision `39fe5b0` corrects that allowlist and adds sanitized Nova, SFU, forwarded-cursor, snapshot and hosted-request tracing, with privacy regressions. See [current field meanings and verification](CAUSAL-DIAGNOSTICS.md). The new laptop export contains browser counters and disconnect events, but no server trace. The exact earlier abort and newer disconnect causes both remain unknown. Capture the runtime failure and server sequence before changing behavior; the passing direct probe does not explain either failure.

## Investigation sequence

1. Capture one failing call on the exact current build. While it is still active, use Session details → Download measurements. After End, private call credentials are cleared and the download cannot fetch server diagnostics. Browser counters, server input/forwarded bytes, SFU callback/PCM readiness and turn events are the first split. A screenshot or a lone healthy endpoint cannot replace this capture.
2. Identify the exact abort reason and the event order. If necessary, trace the current media connection, decoded/resampled input, forwarded audio cursor, Nova onset/result ranges and final flags, snapshot boundaries, hosted request/outcome, transcript coverage and cancellation revision. Distinguish missing audio, rejected timestamps, unavailable snapshot audio, a busy detector, provider failure and incomplete turn readiness.
3. Reproduce the identified defect with a bounded test that fails on the deployed source. Compare the same controlled input across both adapters when it helps separate shared turn logic from SFU conversion/ownership. Change behavior only after the trace supports a cause; diagnostic-only changes may be needed first.
4. Implement the smallest fix, commit it separately, rerun affected tests on the exact revision, deploy and repeat the failing case. Verify a real laptop reply and follow-up on both routes before treating basic conversation as working. Keep latency, workload and cost acceptance untested until the user supplies limits.

The current browser-control limits are explicit: native Chrome inspection timed out; a separate in-app browser attempt was denied because its admin-enforced policy could not be verified. Do not bypass that restriction with another browser-control mechanism. Use permitted access or the user's exported measurements and physical participation. Chrome on the user's laptop is the agreed target; later attempts used no headphones. Network/VPN conditions have not been supplied. Lack of headphones is not an established cause.

## Separate open issues

A recorded follow-up and paused sentence were rejected by the strict hosted-decision policy in prior direct-route tests. A guarded three-second silence proposal passed isolated tests but remains unapproved and disabled. Do not enable it to conceal a different abort cause. If the user approves that policy separately, adapt and retest the saved proposal against the latest source.

B1 remains blocked by normal installation of the evaluated published Pipecat packages on Workers, including mandatory ONNX Runtime. The prototype still has disclosed vendored compatibility changes. Performance acceptance is explicitly deferred. These remain separate from the immediate task of explaining and fixing one failed laptop call. No supported release is established.
