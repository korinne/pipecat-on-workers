# Independent final verification

The deployed candidate reviewed in this report is `d2c0656cd2b018405a1850ddabac6266507c590e`. The full offline suite passed on `670fa94a3179283c44ff907d18435111a09358c8`; the final revision changes only the SFU history explanation and a browser-code comment. An independent comparison verified all other recorded source files unchanged, and all 68 JavaScript tests passed again on a fresh export of the final revision. A supported release is not established. B1 remains blocked by normal Workers package installation; physical audio and performance acceptance remain untested.

The independent verification used fresh Git exports of both named revisions. All archived files remained unchanged through the run. The [complete result](../audit/results/final-independent-20261005.json) records the commit, tree, archive, per-file hashes, interpreter/dependency versions, commands and their full output. The [Pipecat vendor manifest](../src/pipecat/VENDOR_MANIFEST.json) has SHA-256 `c50f06b01732b8b9fe96a4a742400124987c9a6c17f6f292b1be416933ea60fc`.

## Local results

On 5 October 2026 UTC, Python 3.14.7 passed 147 tests and Node passed 68 tests. Provider fixtures, the guarded core lifecycle, seven Durable Object startup/lifecycle cases, 48 public/private access cases, 16 SFU entry cases, 12 acceptance-classifier tests and 13 audit-classifier tests passed. The vendor reproducer matched all 144 Python files and passed 378 sentence-prefix comparisons.

The conversation diagnostics returned six scoped passes and seven untested requirements, with no test errors. Their exit code is intentionally 1 because local diagnostics cannot establish release readiness. These fixtures cover both transports with controlled provider/media leaves. The final suite includes the correction for retired TTS, generation and output-completion errors invalidating a newer response.

The historical package audit remains pinned to its original revision. Its classifiers were checked without rewriting the old audit identity. The current vendor was verified separately from the pinned archives. The exported tree has no Git metadata; the nested diagnostic report says so, while the outer report records the exact Git identity and file hashes independently.

A separate [local Workers lifecycle run](../audit/results/final-workerd-lifecycle-670fa94.json) passed five checks on the same revision in Python 3.14.2/Pyodide 314.0.6: two-Durable-Object isolation, network reconnect with restored history, controlled `ctx.abort` restart with restored history, End, and 30-second abandonment cleanup. It used direct network WebSockets, fixture providers and silent PCM. Its [provenance record](../audit/results/final-workerd-provenance-670fa94.json) verifies 154 application Python files and 983 normal Workers dependency files remained unchanged. The intentional restart logged a cross-request promise warning; subsequent restored turns and cleanup assertions passed. This run does not establish live provider, SFU media or physical-audio behavior.

## B1–B6 on both transports

| Requirement | Direct WebSocket | Realtime SFU | Remaining acceptance |
| --- | --- | --- | --- |
| B1: Install and start | Blocked | Blocked | The application still imports copied and modified Pipecat. Normal supported Workers installation of the evaluated published releases fails. |
| B2: Have a conversation | Failed user check; ordinary synthetic turn passes | Local fixtures pass; live media untested | Valid follow-up and paused speech are rejected by the turn policy. User reports inconsistent direct conversation. |
| B3: Retain useful context | Controlled cases and an additional live follow-up pass | Controlled cases pass | Exact saved answers restore in two live direct calls and stay isolated. The original follow-up phrase is rejected before inference; its failure remains open. |
| B4: Interrupt and continue | Controlled and explicit live-control checks pass | Controlled cases pass | Before/during-speech direct checks recover without stale audio. Held SFU cleanup permits the next turn in fixtures. Physical stop timing remains untested. |
| B5: End and recover | Controlled and bounded live reconnect/End checks pass | Controlled cases pass | Direct live checks restore context and reach zero tracked counters. SFU remote reconciliation and physical lifecycle remain untested. |
| B6: Operate practically | Untested | Untested | Performance acceptance was deferred by the user. No workload or resource limit is inferred from these functional checks. |

The [package investigation](PACKAGE-CANDIDATE.md) preserves normal resolver failures for Pipecat 1.11.0 and 1.12.0. The required ONNX Runtime range has no matching Workers target artifact, including attempts allowing source builds. Normal CPython installation of 1.12.0 passes, but does not clear that blocker. The final application continues with the [declared vendor changes](VENDORED-SPEECH.md).

The [turn investigation](TURN-LIVE.md) preserves an assembled pause/resume utterance that hosted Smart Turn classified INCOMPLETE. A successful ordinary utterance does not replace that failed observation. Remote model-compute and billing cancellation are also unverified.

## Deployment and physical checks

Revision `d2c0656` is deployed as `13762ff7-679d-477a-bafc-dcf37df2d5f4`. The [deployment record](../evidence/deployment-standard-13762ff7.json) names its models, runtime, disabled fixtures and source identity. The [live captures](../evidence/final-live-d2c0656/) preserve ordinary speech, both failed original follow-up attempts, a failed paused recording, the additional passing context phrase and public/private access checks. [Before-speech cancellation](../audit/results/final-live-thinking-d2c0656.json) and [during-speech cancellation](../audit/results/final-live-speaking-d2c0656.json) passed separately with no stale direct-route audio after clear and zero tracked resources at End. The known rollback is `3f8bd208-8e4a-4f2d-96cb-d9a0e51f9542`. Deployed service evidence and browser media observations will retain separate identities and scopes from the local Worker fixtures.

The user approved their laptop and deferred performance acceptance. Real microphone capture, intelligible physical playback and aligned interruption-to-silence measurement still require participation. Nonzero PCM, browser scheduling and server clear events do not establish those outcomes.

## User check and follow-up diagnosis

The user tested the direct route and reported inconsistent answers, the repeat-request turn error, and missed follow-ups. [The report](../evidence/final-live-d2c0656/physical-websocket-user-report.json) is a failed physical conversation check; clear audio and interruption behavior were not separately confirmed. SFU physical behavior remains untested.

The deployed context probe restored the exact saved answer after reconnect, but hosted Smart Turn classified “What country is that city in?” as INCOMPLETE. That follow-up never reached GPT-OSS. Full-recording and trailing-audio comparisons preserved the rejection. A separate four-turn test using “Which country contains that city?” passed in two calls, with distinct saved answers and correct country follow-ups. That additional pass does not erase the failed original phrase or paused recording.

The installed Pipecat reference includes a three-second max-silence completion path. The hosted adapter omitted it and instead discards a still-incomplete turn at five seconds. A guarded adaptation is being prepared separately; enabling it requires the user's decision because the recorded turn policy explicitly required a positive hosted decision. Browser stale-response-state and developer SFU-result defects found during this review have separate fixes and remain undeployed at this point.
