# Python Durable Object crash isolation

This minimal experiment contains no Pipecat, AI providers, pydantic, audio model,
or browser UI. Four Python Durable Objects receive JSON/base64 WebSocket frames
through retained synchronous `create_proxy` event listeners and consume them
through an asyncio queue. They count decoded bytes and acknowledge completion.
The optional `--abort` request aborts an unrelated fifth DO while traffic flows.

It investigates a fatal `RuntimeError: memory access out of bounds` seen late in
the full spike's four-DO synthetic soak. The fatal traceback included
`json.loads` inside its WebSocket callback and workers-runtime-sdk's
`DurableObjectContext.abort` callback (`entrypoints.py:69`). A controlled abort of
a separate DO and DevTools inspection had occurred before that crash. The
traceback does not establish which operation caused memory corruption or
whether Pipecat contributed.

Pinned setup: Wrangler 4.139.0/workerd 1.20260923.1, compatibility date
2026-08-01 (Python 3.13.2), workers-runtime-sdk 1.9.0. The instructions below copy only the runtime SDK into `python_modules` and use
the parent spike's pinned Wrangler. No authentication or network model call is involved. This is a local
debugging project and should not be deployed.

First run `npm ci`, `uv sync --python 3.14.7`, and `uv run pywrangler sync`
in the main spike directory. Then, from this reproducer directory, prepare the
same resolved runtime SDK and start the local server:

```sh
mkdir -p python_modules
cp -R ../../python_modules/workers ../../python_modules/workers_runtime_sdk-1.9.0.dist-info python_modules/
```

```sh
WRANGLER_LOG_PATH=./wrangler.log node ../../node_modules/wrangler/bin/wrangler.js dev --local --port 8791 --inspector-port 9234 > dev.log 2>&1
```

From another terminal in this directory:

```sh
node flood.mjs http://127.0.0.1:8791 30 20
node flood.mjs http://127.0.0.1:8791 30 20 --abort
node flood.mjs http://127.0.0.1:8791 30 20 --abort-js
```

Restart the dev process before each variant for clean comparison. The last
numeric argument is frames per 20 ms per socket: `20` sends roughly 120,000
total frames over 30 seconds, while `1` matches 50 frames/second per socket.
Each frame decodes to 640 bytes. Use `600 1` for the original input cadence over
ten minutes. Results are written to distinct `result-*.json` files and report
sent/received counts and queue depth. A short passing accelerated run cannot
establish that the original time-dependent crash is fixed. A failing run must
be classified using `dev.log`; disconnects or backpressure are not themselves
the same as a fatal WASM error.

Avoid inspector attachment in the initial comparison. If both variants pass,
investigate inspector attachment as a separate variable without changing the
source mid-run.

`--abort-js` is an experimental comparison that schedules a bound native JS
abort callback, avoiding the SDK's Python callback at abort time. It accesses a
private SDK field and is included for diagnosis, not as a supported API.
Recorded outcomes and interpretation are in `FINDINGS.md`.


The main spike now uses a separately validated Python 3.14 runtime and standard
SDK restart. This reproducer intentionally keeps compatibility date 2026-08-01
to reproduce the older Python 3.13 failure. See ../../REPORT.md for the three
failed 3.13 trials and two passing 3.14 duration trials.
