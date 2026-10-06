# Conversation history storage

Each context commit waits for Durable Object storage acknowledgement before the call reports that the response is complete. Writes are serialized. A rejected write gets at most three attempts, with 50 ms and 100 ms between attempts. Each write has a one-second acknowledgement limit.

If all three attempts fail, the call clears output and sends `context_persistence_failed`. It stops accepting new turns and restores the last acknowledged context. An owned task ends the call outside Pipecat's processor callback, so a processor never waits for its own cancellation. Closing the call still releases its provider, media and pipeline resources if its final status write fails.

A timeout is ambiguous: cancelling the local coroutine does not prove that the remote write failed. The call ends after that timeout without submitting a replacement write. The pending task retains an owner until it settles. Diagnostics distinguish a rejected write from an uncertain one and report pending writes. An interrupted write must settle before a newer snapshot can be submitted.

The Durable Object updates its in-memory saved state after storage acknowledges the write. Reconnect restores acknowledged conversation history; it cannot reconstruct an answer that storage rejected. The browser may already have received sentence text or audio before an assistant save fails. The error and clear event make that failed commit explicit; the application does not mark it as a completed response.

`tests/test_context_persistence.py` exercises the real vendored Pipecat pipeline with controlled storage failures and both transport adapters. `scripts/check_sfu_entry.py` separately checks Durable Object assignment order and control socket cleanup after a failed final write. Results are recorded in `audit/results/task3-context-persistence.json`. These are controlled checks, not evidence of live storage failures or physical playback.
