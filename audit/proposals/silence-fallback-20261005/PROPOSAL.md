# Draft maximum-pause fallback

This draft is isolated in `/private/tmp/pipecat-silence-draft-4ffc5a9`, based on application commit `4ffc5a9b0d289b8a71790c3b5ae69d84297e52c3`. It has not been copied into the main checkout or deployed. It changes `src/smart_turn.py` and adds `tests/test_silence_fallback.py`.

The installed Pipecat 1.11.0 reference's `BaseSmartTurn` defaults to a three-second maximum pause. It accumulates audio duration while VAD reports no speech, returns COMPLETE at that boundary and resets the count when speech resumes. The Task 1 controlled turn probe replaced this analyzer behavior with a fixture that always returned INCOMPLETE from `append_audio`; it therefore did not exercise the maximum-pause policy. The separate installed-reference probe here confirms the three-second boundary and reset behavior without running a model.

The draft requires all of these conditions before committing an incomplete turn:

- Smart Turn returned a valid INCOMPLETE decision for the current revision.
- The existing finalized transcript fully covers the pause, and contains text.
- Nova has not reported resumed speech or newer nonempty text.
- Three seconds have elapsed since the pause was observed, and three seconds of new input have been forwarded since then.
- Fresh empty Nova Results cover that same new input without a gap. Interim Results are allowed here because observed live pauses produced interim empty ranges. These ranges remain separate from transcript segments and cannot establish transcript finality.
- The existing five-second readiness deadline has not passed. The final claim checks this clock directly, even if a queued callback delays the expiry task.

Forwarded audio alone never satisfies the fallback. The draft keeps the original model decision and probability, records `completion_reason: silence_timeout`, and leaves the consumed transcript boundary at the original pause end. It adds no model requests. The new timer and readiness task have owners, and retained empty ranges are limited to 16 merged intervals. Resumption, reconnect, End, detector failure, missing final text and stale revisions still prevent a commit.

The signal assumption needs explicit approval and live evaluation: Nova's paused VAD state plus empty processed transcript ranges is evidence that no new speech has been recognized. It is not proof of acoustic silence. An interim result can later change, and a speech-onset event can arrive late. An utterance that pauses for more than three seconds can therefore receive an answer before the speaker intended to finish. Requiring finalized empty ranges would be stricter, but current live captures do not show those ranges during ordinary pauses, so that gate would leave the observed failure unresolved.

This restores a bounded maximum-pause policy in the hosted adapter; it does not turn the failed Smart Turn classification into a successful semantic model test. Original live failures must remain in the evidence. Models, audio encoding, semantic inference snapshots, provider limits, storage policy and output ordering are unchanged.

The tests use the real application pipeline and both real adapters with controlled provider leaves, timestamps and silent PCM. A manual monotonic clock makes the boundary checks fast. They establish ordering and conditions, not measured response latency or physical playback. The initial broader run's unrelated SFU test failure and the subsequent focused rerun remain separate in the report. No SFU fixture or implementation was changed in this draft.

## Bounded live proposal checks

A local Python Worker ran the draft with real Workers AI providers. Two calls first answered the France and Germany capital questions. Each call restored its exact answer after reconnect, then correctly answered the previously rejected follow-up, “What country is that city in?” Hosted Smart Turn still returned INCOMPLETE with probability 0.0130677; the draft recorded `silence_timeout` as the completion reason. Both calls ended with zero tracked local resources. [Context result](live-context.json).

The earlier one-second-pause recording also produced one final transcript and one answer: “If I wanted to visit France what city is the capital” / “Paris is the capital city of France.” No assistant output arrived before the recording finished. [Paused recording result](live-resumed.json). These inputs were synthesized recordings with timed software receipts. They do not establish microphone or audible playback behavior. No performance threshold was judged. [Exact draft source and dependency hashes](live-provenance.json).

The draft remains a proposal. The user decision about allowing completion after an incomplete model decision is pending. Production still uses the documented positive-decision rule, and the original failing evidence remains under `evidence/final-live-d2c0656/`.
