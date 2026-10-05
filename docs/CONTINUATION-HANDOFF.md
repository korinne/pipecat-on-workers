# Continuation handoff

The checkout remains on `codex/transport-contract`. It started clean at `58ccef54653acdd7b8622e789021362a40725f4c`. The deployed application is `3f62b680c32afa8b9f343a4eafb95d34b32c9daa`, version `06111df0-88ce-4142-96e7-4158a9a6e7a9`, at [the existing Worker](https://pipecat-on-workers.korinne.workers.dev). Immediate rollback is `4d75ac46-5a71-4de4-aa12-26f8b026fc38`; original rollback `3f8bd208-8e4a-4f2d-96cb-d9a0e51f9542` is retained. Later commits record evidence and this handoff.

The application uses Nova-3, hosted Smart Turn v2, GPT-OSS-120B with low reasoning and the 2,048-token probe budget, and Aura-2 Luna. The selected Pipecat TTS, output and assistant aggregator now own speech context on both transports. Committed context is persisted before the next request; old saved dialogue is reset under the documented migration. Browser receipts control direct output buffering. Transport adapters and cleanup ownership have separate commits, including late-allocation ownership, bounded retries and accurate unresolved-resource reporting. Public creation, private call tokens and SFU media authorization remain in place; fixture routes remain disabled.

Live direct checks cover ordinary provider responses, actual Nova transcription and hosted turn requests, context restoration and an alternate follow-up, explicit interruption before/during output, reconnect, isolation and End. The final browser build matches the served assets and passes an additional ordinary provider call. These automated calls use synthesized recordings and software receipts. They do not establish audible playback. The user's Chrome/headphones laptop check failed with inconsistent replies and the turn error. The user also reported an SFU gathering timeout. The [setup correction](SFU-GATHERING.md) is deployed and passes controlled checks, with their Chrome retry pending. SFU physical/media acceptance and performance acceptance remain untested; the user explicitly deferred performance limits.

The reproduced follow-up failure is caused by hosted Smart Turn classifying “What country is that city in?” as INCOMPLETE. Saved context remains present, but the strict turn policy stops the follow-up before model inference. The [reviewable silence proposal](../audit/proposals/silence-fallback-20261005/PROPOSAL.md) passes this exact follow-up and the earlier paused recording in an isolated real-provider Worker. It is not enabled. The pending user choice is whether to allow a guarded three-second maximum pause after a valid incomplete decision, changing the earlier positive-decision requirement in [Conversation](CONVERSATION.md). Resumption cancels the candidate; final transcript coverage, fresh empty Nova ranges, elapsed time and the five-second deadline are checked. Those signals cannot prove acoustic silence, and a longer intended pause can receive an answer.

B1 remains blocked: evaluated published Pipecat packages cannot resolve the selected Workers environment normally, including the mandatory ONNX Runtime requirement. The application still uses declared vendored compatibility changes. B2 fails the current physical direct check; B3–B5 have scoped direct live and controlled two-route evidence; B6 is untested. No supported release is claimed. [Full verification](FINAL-VERIFICATION.md), [package blocker](PACKAGE-CANDIDATE.md).

## Next action

Record the requested Chrome retry of [the corrected SFU example](https://pipecat-on-workers.korinne.workers.dev/webrtc.html?v=3f62b68), including whether it reaches Listening. Network type and VPN/filter conditions remain unspecified. A served-file check or native STUN response is not a successful SFU media test.

Resolve the pending turn-policy choice before copying the saved proposal patch into application source. If approved, integrate it in a separate commit, rerun affected checks on that exact revision, deploy, repeat the original live failures, then ask for another short physical check on both routes. If the strict positive-decision rule is retained, the recorded turn failures remain unresolved. The fallback's successful proposal tests cannot be counted as a production pass. All task-owned local test servers have been stopped.

## Commit sequence

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
