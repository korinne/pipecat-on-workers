# SFU cleanup ownership

An interruption retires the old generation before waiting on a remote service. The SFU adapter advances its generation floor, removes the old output state, closes its media socket, cancels its monitor and preparation waiter, then schedules cleanup. A later output uses a fresh adapter and receiver. Delayed callbacks and sends from the old generation fail the ownership checks.

One cleanup worker owns retired adapters, tracks and sessions. It records late allocation results even when the caller has stopped waiting. Adapter responses also retain the publisher session identifier. Cleanup inspects owned sessions to find tracks whose allocation response was lost. It removes an identifier only after an explicit successful close, an explicit already-absent response or an inspected inactive track. Empty sessions have no DELETE operation and expire remotely; a successful inspection with no remaining owned tracks releases the local session record.

The adapter limits each cleanup operation to three attempts, with 100 ms and 200 ms retry delays. A cleanup request gets a three-second wait; if its network operation remains pending, that operation retains its owner and cannot be duplicated. End waits at most five seconds for local tasks and remote reconciliation together. Pending operations may settle afterward and update the same resource records. A timeout leaves unresolved work visible.

Creation and signaling have a 16-second application wait. At most eight such requests may remain pending. Resource reservations include expected late results and stop new allocations before more than 32 adapter, track and session identifiers can accumulate. A lost creation response marks its allocation unconfirmed and blocks further creation in that bridge. These are application safeguards. The agreed deployment workload and performance thresholds remain separate.

The Durable Object retains old bridge objects across disconnect and reconnect while their requests or resources remain unresolved. It persists private resource identifiers and uncertainty counts with the conversation, coalescing updates from late settlements. Private diagnostics report the current records after the call closes. Persistence failures are visible. Reconnect creates fresh media authorization and resources; eight unresolved prior connection records block another reconnect in that call.

After an object restart, saved records report `owner_restarted`. Requests that were pending at the last checkpoint become unsettled prior-runtime requests; they are not counted as live Python tasks. Their remote outcome remains unknown. The current code does not replay creation requests or automatically retry exhausted cleanup after restart. Those records require reconciliation through the SFU service using the owned identifiers. An allocation whose response was lost may have no recoverable identifier. Expiry preserves unresolved records instead of deleting the only account of them.

The [controlled result](../audit/results/task4-cleanup-verified.json) covers held close requests, replacement output, rejected stale output, repeated interruptions, request/resource bounds, failed closes, late allocations, End, reconnect ownership and storage failure. The [application probe](../acceptance/results/task4-owned-cleanup-verified.json) holds the real SFU adapter's REST close leaf while the Pipecat application starts the next model call. Provider, REST and socket I/O are simulated. These results do not establish remote SFU resource release, physical playback, audible interruption delay or deployed capacity.

Reproduce the focused checks with:

```sh
.venv/bin/python -m unittest tests.test_sfu_transport -v
.venv/bin/python scripts/check_sfu_entry.py
.venv/bin/python scripts/check_entry_lifecycle.py
.venv/bin/python acceptance/run.py --case sfu-cleanup --output /absolute/path/to/new-cleanup-result.json
```

Keep the original evidence files. Use a fresh path when repeating the probe.

The [first recorded run](../audit/results/task4-cleanup.json) retains a harness error: it observed the former response method while the speech integration removed that method. The verified probe observes the shared pipeline's listening event after output and context completion.

An independent review reproduced a late-result race in the final retry pass: a successful adapter close could settle during session inspection after its result had already been checked. The cleanup worker now schedules a follow-up drain without resetting retry budgets. [Failing reproduction](../audit/results/task4-late-drain-before.json) and [verified fix](../audit/results/task4-late-drain-fixed.json) are preserved separately.

A later full test run exposed a race in the test helper: it could report settlement between the old worker finishing and its callback scheduling the cached-result drain. The helper now waits for the result cache too. A deterministic observer reproduces that scheduling window and checks that no cached results or adapters remain when the helper returns. The [failure evidence](../audit/results/task4-cleanup-helper-before.json), [initial suite log](../audit/results/task4-cleanup-helper-initial-suite.log) and [18 passing SFU tests](../audit/results/task4-cleanup-helper-fixed.json) are preserved. This correction changes tests only.
