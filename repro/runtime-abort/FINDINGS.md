# Observed crash isolation

On macOS, Wrangler 4.139.0/workerd 1.20260923.1 with compatibility date
2026-08-01 selected Python 3.13.2. The minimal source imports only the Python
standard library, Pyodide interop, Workers SDK 1.9.0 and WebSocketPair.

- Baseline: four independent DOs each processed all 27,620 submitted JSON/PCM
  messages (110,480 total) in 30.085 seconds. All closed with code 1000;
  per-DO queue peak was one; no errors occurred.
- SDK abort variant: while four new DOs received the same traffic, an unrelated
  fifth DO called `self.ctx.abort(...)` after one second. The request returned
  HTTP 500 as expected, then the runtime logged `Uncaught RuntimeError: memory
  access out of bounds`, followed by errors in Pyodide's `capture_stderr`.
  All four WebSockets closed unexpectedly with code 1006, after approximately
  13,500 submitted messages each. The 30-second test failed.
- No Pipecat, pydantic, provider requests, application history, audio generation,
  or inspector attachment was needed for that failure. The normal message
  callback and asyncio queue pattern alone passed the baseline.
- Native-JS abort comparison: queuing the bound JavaScript abort function
  directly returned HTTP 500 for the fifth DO while all four traffic DOs
  completed normally. Each processed all 27,520 submitted messages (110,080
  total) in 30.027 seconds, with zero errors and normal code-1000 closure.

The installed SDK's `DurableObjectContext.abort` schedules
`create_once_callable(lambda: ctx.abort(reason))` as a microtask, then raises
`DurableObjectAbort`. Its own comment explains that synchronous abort can leave
stale asyncio task state because V8 unwinds execution immediately. The queued
callback still enters Python immediately before aborting JS execution. That is
a plausible failure boundary, not a proven interpreter-level diagnosis.

The comparison supports treating the original Pipecat soak crash as confounded
by its controlled restart test. It does not establish a fix, a supported
workaround, or ten-minute stability without that operation. Repeat baseline and
abort variants from clean processes, then test the full application without
controlled abort or inspector interaction.

Raw evidence in `../../evidence/`: `minimal-py313-baseline.json`,
`minimal-py313-abort.json`, `minimal-py313-sdk-abort-crash.txt`,
and `minimal-py313-native-js-abort.json`.

The optional `--abort-js` variant instead queues a bound native JavaScript
`ctx.abort` callback. This avoids Python re-entry at the aborting microtask but
uses the SDK's private raw context. It is an experiment and requires separate
longer application-level validation; one short passing comparison does not
make it a supported production API or establish sustained stability.


## Full application follow-up

The native-JS comparison did not resolve the sustained application failure.
The final frozen Python 3.13 application process first passed its lifecycle test
with the native-JS abort path, then crashed during its four-DO soak after the
last successful 507.270-second/420-turn sample. No inspector was attached during
this run. See ../../evidence/workerd-soak-py313-shim-failed.json and
../../evidence/workerd-native-js-shim-crash.txt. At that stage, controlled abort remained a
variable in both long runs; the later comparison below removes it. The private shim is an experiment, not a validated
fix. The subsequent abort-free comparison and runtime change are recorded below.


## Follow-up with verified runtime versions

A fresh Python 3.13/Pyodide 0.28.2 process also failed WITHOUT any controlled
restart or debugger. All four sockets closed abnormally around 516.54 seconds;
424 turns had completed. Therefore abort is not necessary for the sustained
failure. Python 3.14/Pyodide 314.0.6 then passed two ten-minute four-DO runs, including
one after the public SDK restart method. The main spike now selects this runtime
and removes the private shim. The configuration mitigates the observed local
failure; the precise interpreter-level cause and remote stability are unproven.
See ../../REPORT.md and the named evidence files there.
