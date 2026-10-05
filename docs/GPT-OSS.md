# GPT-OSS integration

The LLM provider uses Workers AI `@cf/openai/gpt-oss-120b`, `reasoning_effort="low"`, streaming messages and a 2,048-token budget. The live Python binding accepted these fields. The provider does not report the effective effort independently, so acceptance of the setting is the available evidence.

The actual stream carries answers in `choices[0].delta.content`, reasoning in `reasoning_content` and `reasoning`, a `stop` or `length` completion reason, and a final native `response`/usage event followed by `[DONE]`. The parser ignores native `response`, counts one reasoning field without retaining its contents, and forwards only answer deltas. Usage on intermediate events is incremental; the final event contains totals.

A live one-token request returned the protocol marker `<|channel|>` in the answer field before reporting `length`. The parser suppresses reserved `<|` sequences even when split across deltas and rejects the response. This guard covers the observed provider behavior. It does not establish that arbitrary model content has been checked for every possible protocol defect.

Normal completion requires an explicit `stop` and nonempty answer. Token exhaustion, empty completion, provider errors, malformed streams, unsupported completion reasons and premature EOF raise a sanitized error. `[DONE]` alone cannot establish success. Text already sent before a later failure is partial output; the caller must stop that response and apply its tested speech/context failure policy.

Generation has a 45-second request/read deadline, a 64 KiB event bound, a 1 MiB total stream bound and a two-second reader-disposal wait. These are application bounds, not agreed performance targets. Cancellation aborts the request, closes an acquired reader and retains ownership of late binding results. Python 3.14's `asyncio.shield` logged expected abort rejection despite a late-result callback consuming it. The provider now waits without transferring cancellation to the owned binding task. Local cancellation does not prove remote inference or billing stopped.

## Evidence

[Task 3 GPT live results](../audit/results/task3-gpt-live.json) retain the interface captures, the initial application-adapter run and the final rerun. These are local Python Workers executing real Workers AI requests. No fixture answered these calls. They do not test Pipecat speech queues, either browser transport, microphone capture or physical playback.

The normal and fixed context-follow-up cases completed within the 2,048-token budget. The report records first-answer timing and final usage for each request. The sample is too small to establish conversational latency, answer quality or budget sufficiency for the intended workload. The single-token limit and empty-answer cases exercise explicit failures. Unexpected EOF and synthetic error events remain controlled-test evidence.

The initial nested probe configuration failed before inference because Wrangler did not find the packaged Workers SDK. A root-level configuration fixed the probe setup. A separate pre-aborted signal case waited until the harness's 45-second deadline; it remains a failed cancellation observation. The application cancellation cases cancel an active owned request and record their actual resource settlement separately.

## Reproduction

Run the pure parser and provider fixtures with the repository interpreter:

```sh
.venv/bin/python -m unittest tests.test_gpt_stream -v
.venv/bin/python scripts/check_providers.py
```

For interface shapes, start the isolated probe from the repository root:

```sh
.venv/bin/pywrangler dev --config gpt-probe.wrangler.jsonc --ip 127.0.0.1 --port 8791
```

POST `/normal`, `/one-token`, `/invalid-budget`, `/preabort`, `/cancel-after-first-event`, `/cancel-after-answer`, `/empty` or `/follow-up`. Save each response in a fresh output directory. Do not add `--local`: the pinned Wrangler disables the remote AI binding with that option. The probe records synthetic answer text and event shapes, never reasoning text or credentials. Its reasoning-character count sums both aliases and can double-count the same reasoning; application diagnostics count only one alias.

For the exact application adapter, create a fresh temporary directory with `src/`, copy `src/providers.py`, `src/gpt_stream.py` and `audit/reference/task3_gpt_adapter.py` into it, naming the latter `src/probe.py`. Copy the normally installed `python_modules` directory into the temporary root so later application work cannot reload the probe. Record file hashes before starting. This copy is an isolated application probe, not package-installation evidence for B1.

Use this temporary `wrangler.jsonc`:

```json
{
  "name": "task3-gpt-adapter-local",
  "main": "src/probe.py",
  "compatibility_date": "2026-09-24",
  "compatibility_flags": ["python_workers"],
  "ai": {"binding": "AI", "remote": true},
  "workers_dev": false,
  "preview_urls": false
}
```

Start the repository's pinned Wrangler executable against that configuration. POST `/normal`, `/one-token`, `/empty`, `/follow-up`, `/invalid-budget`, `/cancel-before-answer` and `/cancel-after-answer`, preserving each JSON response. Keep the probe local and stop it afterward. No probe routes belong in the deployed application.
