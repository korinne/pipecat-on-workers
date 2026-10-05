# Continuation handoff

## Latest laptop evidence, 5 October 2026

The new browser export records 48.6 seconds of local capture, a final user transcript event, then a control close 1013 and two failed reconnects with code 1006. The last pong confirms at least 3.92 seconds of server-received and locally forwarded audio. Server diagnostics were unavailable, so the cause remains unknown; this is not evidence of an INCOMPLETE Smart Turn rejection. Diagnostic-only source `cdd8a55` is deployed as `742a8942-84b6-4247-a3b0-e193af3855b4` and now preserves diagnostic HTTP failures and close cleanliness. All 111 JavaScript checks pass in an independent archive, and served assets match. No fresh physical conversation pass exists. See [the failed call, limits and next observation](SFU-CONNECTION-DROP.md). Conversation policy and the unapproved silence fallback are unchanged. Earlier findings below retain their historical scope.


The checkout remains on `codex/transport-contract`. This investigation began clean at `8d3f1964093372a21b581d5aa9a594ddd92024f9`. Application source `cdd8a55051295401a5d7bdc3f33b1233ab7bc02d` is deployed as `742a8942-84b6-4247-a3b0-e193af3855b4` at [the existing Worker](https://pipecat-on-workers.korinne.workers.dev). The immediately previous version is `425815d1-a965-47bc-b863-4dad470ad0a1`; retain rollback `06111df0-88ce-4142-96e7-4158a9a6e7a9` and original rollback `3f8bd208-8e4a-4f2d-96cb-d9a0e51f9542`. Later commits record evidence and this handoff.

The application uses Nova-3, hosted Smart Turn v2, GPT-OSS-120B with low reasoning and the 2,048-token probe budget, and Aura-2 Luna. The selected Pipecat TTS, output and assistant aggregator now own speech context on both transports. Committed context is persisted before the next request; old saved dialogue is reset under the documented migration. Browser receipts control direct output buffering. Transport adapters and cleanup ownership have separate commits, including late-allocation ownership, bounded retries and accurate unresolved-resource reporting. Public creation, private call tokens and SFU media authorization remain in place; fixture routes remain disabled.

Live direct checks cover ordinary provider responses, actual Nova transcription and hosted turn requests, context restoration and an alternate follow-up, explicit interruption before/during output, reconnect, isolation and End. Source `39fe5b0` passed an additional ordinary provider call. No provider call has been repeated on `cdd8a55`; its Python source is unchanged, and this turn checked only the diagnostic frontend and deployment. These automated calls use synthesized recordings and software receipts. They do not establish audible playback. The user's Chrome/headphones laptop check failed with inconsistent replies and the turn error. The user also reported an SFU gathering timeout. The [setup correction](SFU-GATHERING.md) is deployed and passes controlled checks. In a later retry without headphones, the user's screenshot shows the SFU page connected and recognizing “hello hello,” but they report no reply. The loaded asset revision and the exact failure stage are unknown. SFU speech input is observed; full conversation acceptance fails and audible output remains untested. The user explicitly deferred performance limits.

The reproduced follow-up failure is caused by hosted Smart Turn classifying “What country is that city in?” as INCOMPLETE. Saved context remains present, but the strict turn policy stops the follow-up before model inference. The [reviewable silence proposal](../audit/proposals/silence-fallback-20261005/PROPOSAL.md) passes this exact follow-up and the earlier paused recording in an isolated real-provider Worker. It is not enabled. The pending user choice is whether to allow a guarded three-second maximum pause after a valid incomplete decision, changing the earlier positive-decision requirement in [Conversation](CONVERSATION.md). Resumption cancels the candidate; final transcript coverage, fresh empty Nova ranges, elapsed time and the five-second deadline are checked. Those signals cannot prove acoustic silence, and a longer intended pause can receive an answer.

B1 remains blocked: evaluated published Pipecat packages cannot resolve the selected Workers environment normally, including the mandatory ONNX Runtime requirement. The application still uses declared vendored compatibility changes. B2 fails the current direct and SFU user checks; B3–B5 have scoped direct live and controlled two-route evidence; B6 is untested. No supported release is claimed. [Full verification](FINAL-VERIFICATION.md), [package blocker](PACKAGE-CANDIDATE.md).

## Next action

Start with the [new connection-drop report](SFU-CONNECTION-DROP.md) and [debugging brief](DEBUGGING-BRIEF.md). The supplied laptop export shows local capture continuing after a 1013 disconnect, but the private server trace was unavailable. Historical log access returned 403; the permitted live reader is attached for a requested short repeat. Check its state before relying on it. Capture the runtime outcome and preceding server sequence before a behavior change. The earlier generic abort is a separate unresolved observation.

The first [SFU retry](../evidence/sfu-laptop-20261005/user-report.json) showed recognized speech, but a [later attempt](../evidence/silent-input-20261005/user-report.json) showed no transcript at all. The exact causes remain unknown. [Controlled silent-input fixes](SILENT-INPUT.md) are now deployed: SFU readiness requires current callback PCM; rejected Nova onsets/results report a bounded error; exported measurements include safe server counters/events. Independent tests and one direct real-provider response pass. Ask the user to reload the current SFU page and download measurements while the failed call is still active if the problem recurs. The latest input counters have now been supplied; network type and VPN/filter conditions remain unspecified. Physical conversation acceptance remains open.

Resolve the pending turn-policy choice before integrating the saved proposal. If approved, adapt it to the current source in a separate commit, rerun affected checks on that exact revision, deploy, repeat the original live failures, then ask for another short physical check on both routes. If the strict positive-decision rule is retained, the recorded turn failures remain unresolved. The fallback's successful proposal tests cannot be counted as a production pass. Local test servers have been stopped. A task-owned live log reader is attached for the requested microphone repeat; its private path and last verified state are in the new investigation record.

## Commit sequence

This investigation adds `b85efeb` (shared diagnostic export/privacy) and `39fe5b0` (bounded causal tracing). Both leave conversation policy unchanged. Commit `cdd8a55` adds bounded diagnostic fetch failures and WebSocket close metadata after the new real disconnect export.

The final evidence/manifest commit follows the commits listed below.

- `1cb90b0` Record published Pipecat package blockers
- `c6a5b72` Extract bounded browser audio delivery
- `5a4b868` Stream GPT-OSS answers with explicit failure outcomes
- `76865b1` Isolate retired audio from bounded SFU cleanup
- `01bba3b` Drain late cleanup results after final retry
- `d89b690` Trim Smart Turn snapshots to observed speech boundaries
- `06dd3b8` Bound unresolved provider requests after cancellation
- `143d6fb` Preserve pending turns across empty Nova results
- `482294a` Vendor pinned Pipecat speech components
- `4245fa2` Use standard speech output and persist assistant context
- `4853e2a` Follow standard speech completion in live checks
- `e6ee995` Add a bounded muted SFU browser check
- `1a6c8d1` Ignore failures from retired speech generations
- `670fa94` Add bounded live context and laptop verification checks
- `d2c0656` Describe restored SFU conversation history accurately
- `6b56c64` Check interruption during live speech in a bounded run
- `b9cfbd6` Record final live thinking and speech interruption checks
- `4ffc5a9` Preserve live follow-up failure events
- `9f77a76` Clear stale browser response state after cancellation
- `0996025` Match untagged SFU transcripts to response generation
- `3940021` Record deployed checks and failed conversational turn cases
- `c8bbb91` Ignore stale browser events before changing current output
- `a5fa113` Record tested silence fallback proposal
- `a5ddcae` Wait for cached SFU cleanup results in tests
- `7d2063b` Record independent browser correction checks
- `3d0ed27` Record browser deployment and pending turn policy
- `3f62b68` Try gathered SFU candidates when discovery times out
- `05ca8f9` Record SFU gathering contract and independent checks
- `cdc1ab7` Record SFU setup fix and deployment
- `b3627a5` Record SFU microphone recognition and missing reply
- `b1a14a9` Include private speech diagnostics in measurement exports
- `852ebf3` Report rejected Nova turn starts without inferring speech
- `2a5f197` Preserve reply state while reporting input failures
- `f8835e8` Wait for SFU input media before reporting readiness
- `a010dfa` Verify silent-input recovery and diagnostics
- `6468022` Record silent-input deployment and verification limits
