# Nova and hosted Smart Turn live checks

The deployed Python Worker completed one synthetic recorded question through Nova, hosted Smart Turn, Llama and Aura. It emitted one final user transcript and nonzero response PCM. End left no owned tasks, sockets or readers in that run. This is the older response pipeline at deployment `3f8bd208-8e4a-4f2d-96cb-d9a0e51f9542`, not GPT-OSS or physical playback acceptance.

A recording of “If I wanted to”, one second of silence, then “visit France, what city is the capital?” failed: the application committed two user turns. Nova reported the first final transcript range ending at 1.53 seconds, while its last word ended at 1.12 seconds. A Python Workers binding probe classified the cursor-length audio as complete (probability 0.7685), and the word-length audio as incomplete (0.1586). No threshold or model decision was overridden.

The coordinator now snapshots through the last observed word boundary. It still uses final transcript ranges to verify coverage. Word intervals must be finite, ordered and within the reported range; missing timing for nonempty text fails visibly. Empty endpoints can use the latest retained final word. Resumed speech and cancellation retain the existing revision guard. Final deployment checks must verify this correction; the isolated detector comparison alone cannot establish conversation acceptance.

The [captures](../evidence/task2-live-20261005/summary.json) preserve a first Nova HTTP 429, the failed pause run and later successful request captures. Smart Turn returned an SDK `JsDict`; the isolated probe initially serialized that incorrectly. Its corrected conversion matches the application's existing `.to_py()` conversion. The old probe config also omitted the external Workers SDK. Start the corrected local recipe from the repository root after normal dependency setup:

```sh
.venv/bin/pywrangler dev --config turn-probe.wrangler.jsonc --ip 127.0.0.1 --port 8792
```

POST the synthetic PCM request described in DEVELOPMENT to `/nova` or `/smart-turn`. This runs Python locally with real Workers AI. Keep all fresh result files. The standalone probe's ten-second detector wait is not the application's two-second limit.

Physical microphone/playback and SFU media were not exercised by these captures. The user approved their laptop for a later physical check and left performance acceptance untested.

## Follow-up investigation

A frozen local Python Worker using live providers reproduced a second defect: Nova sends empty Results while silence continues. Those results advanced beyond the saved pause range and incorrectly aborted it. Empty text now leaves the pending decision intact; nonempty text beyond the pause still requires observed resumption. The regression checks both interim and final empty results.

The resumed synthetic question still received INCOMPLETE from hosted Smart Turn (0.0574 with the word-boundary slice). Six isolated comparisons retaining another 100 or 200 ms of observed input classified the short complete question correctly and the incomplete prefix correctly, but still classified the assembled resumed question as incomplete. The model result was not overridden and the audio boundary was not tuned to force a pass. This fixture remains a failed semantic turn case. Real speech and additional natural phrasing need final verification.

The new files preserve the failed application candidate and the frozen diagnostic trace separately. The trace contains only generated test speech. It uses temporary local timing instrumentation and is not an exact production revision or final acceptance result. An earlier temporary diagnostic incorrectly assumed every Nova `channel` field was a dictionary; SpeechStarted uses a list. That instrumentation error was corrected before the frozen trace recorded here.
