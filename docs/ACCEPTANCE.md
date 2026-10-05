# Pipecat capability acceptance tests

Use [B1 through B6](GOALS.md) as the current behavior requirements. The first release uses Workers AI for hosted Smart Turn, STT, GPT-OSS-120B, and Aura-2 TTS, with direct WebSocket and SFU audio. Test [conversation behavior](CONVERSATION.md) against a specified Pipecat speech/output configuration.

Some of these tests run with the files in this delivery. Others need a candidate package, a live service, or a physical device. A procedure becomes evidence only after someone runs it and records the result. A simulated transport cannot prove that two live systems work together, and a passing regression test may preserve a known product limitation.

## Required coverage

| Goal | Checks in this plan |
| --- | --- |
| B1: Install and start | PC1, PC2, and WK1 |
| B2: Have a conversation | AI1, AI2, and real microphone/output checks on both routes |
| B3: Retain useful context | CONTEXT1 |
| B4: Interrupt and continue | AI2 cancellation, INTERRUPTION1, and physical stop timing |
| B5: End and recover | LIFECYCLE1 on both routes |
| B6: Operate practically | RESOURCE1 on the declared deployment |

Use [DEVELOPMENT.md](DEVELOPMENT.md) for commands. Each passing result must name its exact configuration and test conditions.

## Tests that can run now

| Command or test | What it checks | What it leaves unanswered |
| --- | --- | --- |
| `audit/suite/audit.py` provenance | Whether the repository, copied dependency source, and archive checksum match the recorded versions | Whether the code is correct or supported in production |
| `core.baseline` | Existing simulated lifecycle, context, and cancellation behavior while blocking a named list of imports and guarding Python thread startup | Other native imports, other ways to start threads, actual Workers execution, and real audio |
| `ablation.eager_audio` | Whether restoring upstream audio imports fails for the expected dependency reason | Whether every import was checked or a particular package layout is required |
| `ablation.prewarm` | Whether enabling upstream-style prewarming tries to start the guarded thread | Whether every optional subsystem can work without threads |
| `regression.python` and `regression.javascript` | The existing 41 Python and 62 JavaScript checks | Live SFU behavior and deployed performance |
| `candidate.core` | Whether the test loads the installed candidate instead of the repo's copied source, and whether that candidate meets the same core requirements | Dependency installation, a supported deployment, and all Pipecat APIs |
| `audit/acceptance/sfu_gaps.py` | Missing assistant history and cleanup that holds up the next turn, using a real Pipecat pipeline with controlled input and output | Missing SFU features, network latency, and whether audio has stopped coming from a speaker |
| `audit/benchmarks/media_costs.py` | Local CPU use for existing PCM conversion and packet wrapping, plus payload-size calculations | Opus, Workers CPU use, the cost of calls between languages, audio quality, and call capacity |

The [shared diagnostics](DEVELOPMENT.md) additionally expose custom browser-receipt dependence and cancellation/cleanup behavior. Their `B3.generated-context` expectations do not define interruption behavior for a standard Pipecat voice pipeline. The recorded `B3.delivery-state` requirement for a custom delivery-state representation is superseded. Preserve the old result files under their original definitions. Current diagnostics omit retired checklist requirements. Neither old nor new diagnostic totals define release readiness.

An expected incompatibility is an observed gap. It cannot count as support for that capability. An unrelated import error or a timeout is a test error. The runner never installs candidate dependencies silently or patches source files to make candidate tests pass.

## Task 1 evidence boundary

The new [reference probes](DEVELOPMENT.md#repeat-task-1-reference-probes) exercise the normally installed Pipecat 1.11.0 speech/output path, selected turn interfaces, baseline GPT parser and expanded imports/startup. Read their [results and classifications](EVIDENCE.md#task-1-reference-investigation) before treating them as implementation acceptance. All earlier result files remain unchanged.

Use [GOALS](GOALS.md#task-1-reference-configuration) for the exact service/format target and [CONVERSATION](CONVERSATION.md#task-1-speech-reference) for fixture expectations. In particular, ordinary sentence text progress is coarser than individual samples; default nonfatal synthesis failure can retain failed text. An integration must state its failure rule and verify it before continuing a call after that error.

AI1 now needs full-pipeline tests for the observed stale-result inference risk, p99 fallback, inactivity watchdog, provider/audio-watermark alignment and timeout invalidation. A controller-only probe cannot pass those integration checks. AI2 still needs live request acceptance and actual GPT-OSS response events: controlled SSE fixtures cannot satisfy them. PC1 needs a Workers-compatible installation, not the successful local CPython installation. No B1 through B6 release outcome changes to pass from these probes alone.

## Task 2 controlled integration boundary

The [Task 2 results](EVIDENCE.md#task-2-turn-integration) exercise Nova-shaped events and synthetic hosted decisions through actual vendored Pipecat frame queues. They cover onset interruption, incomplete pauses, completion in either transcript order, empty endpoints, duplicate/reordered final ranges, resumed speech overtaking queued completion, late canceled decisions, reconnect, detector failure, readiness deadlines, forced watchdog expiry and immediate recovery. They assert the next model input and absence of partial/stale user context.

These checks establish the application coordinator's behavior for the declared fixtures. AI1 still needs observed Nova events, accepted hosted input, Python/JavaScript conversion, cancellation and representative speech through both live routes. The timestamp/coverage assumptions are explicit in [Conversation](CONVERSATION.md#task-2-user-turn-coordination). Login has since been restored and the [deployment follow-up](EVIDENCE.md#task-2-deployment-follow-up) passes imports/startup and public HTTP checks; the live conversation checks remain untested. The application still uses the baseline Llama response path and custom assistant history, so Task 2 does not pass AI2, CONTEXT1 or the full reference pipeline.

## Package and runtime requirements

### PC1: Install the candidate package

Pipecat supplies a candidate wheel or an agreed set of distributions. Record their hashes, the full resolved dependency list, Python and runtime versions, compatibility date, application commit, and the locations from which Python imports modules.

Install into a clean environment using normal dependency resolution. An installation with `--no-deps` does not establish supported installation. Run candidate mode using that environment's interpreter. If Pipecat changes the supported API, explicitly review the test requirements before updating them; do not add compatibility code just to make the test pass.

### PC2: Load core code without unused optional features

Exercise exactly the imports intended for support, including the selected TTS, output transport, assistant aggregator, and hosted turn client. The existing candidate audit covers a smaller core and must be extended to these components. Run the lifecycle with Pipecat embedded in the host without the unsupported thread path. Any unsupported feature exposed by the chosen package should fail clearly when selected.

Importing frames or conversation context must not require unused image, native audio, or provider libraries. Adding those libraries until the test passes would leave that requirement untested.

### WK1: Run inside a Python Durable Object

Deploy the fixed candidate version in an isolated environment. Test startup, conversation-context ordering, cancellation of pending model and tool work, and the following turn. Also test disconnect, idle cleanup, and controlled restart.

Persist only application state unless seamless recovery has been implemented separately. Check every task, socket, and reader the application owns, including results that arrive after cancellation. Record the imported modules' origins from inside Python Workers as well as on the build machine.

Existing repo tools supply some of the simulated test inputs. Use a disposable checkout because `scripts/probe_worker.mjs` writes to a historical evidence path. The other two commands accept new output paths:

```sh
# In a disposable copy of the pinned repository, after its documented setup.
uv run pywrangler dev --local --var ENABLE_TEST_ROUTES:true

# In another terminal; use the actual printed port.
node scripts/check_worker.mjs http://127.0.0.1:8787 /absolute/path/to/new-lifecycle.json
node scripts/soak_worker.mjs http://127.0.0.1:8787 600 /absolute/path/to/new-soak.json
```

These commands use simulated providers. The repo's real-provider tests and a physical-device test are still required. The candidate deployment must replace the copied Pipecat source with the installed candidate. Running the original repo again only repeats the baseline test.

## Workers AI turn detection

### AI1: Coordinate speech and transcript events

Use hosted `@cf/pipecat-ai/smart-turn-v2`, Workers AI STT, and the selected Pipecat turn strategies. Pin the model IDs, audio formats, adapter versions, thresholds, and timeout behavior before acceptance. Nova-3 plus hosted Smart Turn is the first candidate to evaluate. If Flux is selected instead, specify how its turn events interact with Smart Turn so only one path can finish a turn. This work does not require local inference in the Worker. [Hosted Smart Turn](https://developers.cloudflare.com/workers-ai/models/smart-turn-v2/), [Nova-3](https://developers.cloudflare.com/workers-ai/models/nova-3/), [Flux](https://developers.cloudflare.com/workers-ai/models/flux/)

Check these cases with deterministic events, then repeat representative speech cases through both live transports:

- Speech starts during an answer. The interruption path runs promptly without waiting for the semantic end detector.
- A person pauses and resumes. An obsolete completion result does not end the resumed or next turn.
- The turn completes before its final transcript arrives. The next model call receives the complete relevant transcript under the selected Pipecat strategy.
- Completion or transcript events are duplicated or reordered. A completed user turn causes one intended response.
- The hosted detector or STT is slow, fails, or never finishes. The documented timeout/recovery behavior is bounded and does not silently start duplicate responses.
- The call ends while inference is pending. Late results cannot restart the conversation or publish old output.

Repeat representative speech cases with a real microphone through each transport. Confirm the answer reaches an audio device and is intelligible. Recorded speech and nonzero samples supplement this check; they do not establish physical playback.

Record turn-decision latency and time from speech end to first response audio. A hosted model's availability does not prove a Workers-compatible Pipecat client. Test that connection in the actual Python runtime, including cancellation and the Python/JavaScript boundary.

### AI2: Stream GPT-OSS answers into speech

Use `@cf/openai/gpt-oss-120b` through Workers AI. Begin with the model page's streaming `env.AI.run` interface and verify it from Python Workers. Record the accepted request fields, reasoning setting, output-token budget, and response events. Cloudflare's separate Responses endpoint currently documents non-streaming requests only; do not assume all endpoints have the same streaming behavior. [Model interface](https://developers.cloudflare.com/workers-ai/models/gpt-oss-120b/), [Cloudflare endpoint compatibility](https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/)

- Verify a normal streamed answer, an empty result, completion at the token limit, and a provider error. Handle incomplete output explicitly; the baseline parser does not inspect completion reasons.
- Send only user-facing answer text to TTS, browser transcripts, and ordinary assistant context. Reasoning and protocol events must not be spoken or mistaken for the answer. Confirm their actual representation rather than assuming a field name. [Official OpenAI guidance on GPT-OSS reasoning](https://developers.openai.com/cookbook/articles/gpt-oss/handle-raw-cot)
- Interrupt before answer text arrives and during speech. Cancel or close the owned stream and reject late output. Local reader cancellation does not prove that remote inference or billing stopped.
- Test a context-dependent follow-up with the selected Pipecat pipeline on both routes. Existing fixture or Llama results cannot establish GPT-OSS compatibility.
- Measure time to the first speakable answer, time to first audible response, answer quality, and usage/cost. Test low reasoning effort first, confirm the setting took effect where observable, and judge the results against the agreed requirements. Arrival of a reasoning event is not first-answer latency.

These checks fit B2 through B4 and the existing resource acceptance. They do not add a general model comparison, new tools, or a reasoning display to the application.

## Conversation and SFU requirements

### CONTEXT1: Preserve the selected Pipecat context behavior

Specify a reference pipeline: Pipecat version, Workers AI service adapters, TTS/output components, and the frame sequence that updates assistant context. Use equivalent speech/output events when comparing the Worker integration with that reference. Raw model text sent straight to an aggregator cannot establish parity with speech progress.

Run the following on direct WebSocket and SFU:

- Complete an answer, then ask a follow-up that depends on it. Inspect the exact history sent to the model and the saved context.
- Interrupt while the model is generating and while generated speech is waiting in output queues. Check retained text against the selected Pipecat configuration, without assuming every generated token should be kept.
- Start a new turn while old output arrives late. Confirm that canceled work cannot append stale text or audio to the current turn.
- Fail TTS or disconnect during output. Confirm the documented recovery behavior and the context supplied if the call continues.
- Restore a supported saved conversation. Preserve message order and prevent duplicate assistant entries. Test the agreed handling of old saved sessions and obsolete system instructions; missing historical answers cannot be reconstructed.

Use Pipecat's assistant aggregator and supported speech/output flow first. A separate delivery ledger, routine “playback unknown” prompt notes, and exact-word browser receipts are outside this release. Operational response identity, cancellation, bounded error handling, and useful diagnostics remain required.

The included baseline tests establish the prototype's custom receipt dependency and SFU history omission. They have not tested the new reference pipeline. Before acceptance, replace obsolete regression expectations and add equivalent-event checks without changing the historical JSON.

### INTERRUPTION1: Let interruption proceed while cleanup finishes

Hold remote cleanup open, interrupt the current response, and queue the next model inference, which is the work that produces the next answer. Confirm that the application rejects output from the old response and dispatches the browser-clear event. The conversation must be able to advance while cleanup is still waiting.

Cleanup also needs an owner responsible for finishing it or reporting that it remains unresolved. Test that responsibility separately from the next turn's progress.

The included controlled test shows that the baseline waits for cleanup before advancing. It does not measure how long sound continues at a speaker. Sending a clear event, removing queued audio, and hearing silence are separate observations.

In live tests, interrupt while generation is pending, near the first audio packet, mid-sentence, and with queued output. Test repeated interruptions and late results. Use aligned speech-onset and output recordings or an equivalent physical measurement for interruption-to-silence timing under the agreed device and network conditions.

### LIFECYCLE1: Test live audio and resource cleanup

Run both routes with real browsers and Workers AI. Test normal End, mute/unmute, disconnect, bounded reconnect, idle abandonment, provider startup failure, failure during output, and controlled restart. Reconnect must restore committed context according to the agreed recovery behavior and create fresh live resources. An interrupted utterance may require repetition if that is the declared behavior.

Run simultaneous conversations with distinguishable content and inspect their model inputs. Different session identifiers alone do not establish state isolation.

For SFU, also test a failed close, a resource created after cancellation, and a create request whose response is lost. Inspect tasks, sockets, readers, queued media, late requests, and cleanup tasks. Canceling a Python waiter does not establish cancellation of remote model compute or billing.

Use the server API or diagnostics to verify remote resources as well as the application's local counters. If an allocation's status is unknown, leave it unknown until it has been reconciled. Test the selected response-isolation approach. Track reuse is optional work only if a demonstrated need justifies changing that approach.

## Deployed resource and timing requirements

RESOURCE1 applies to the selected Workers AI pipeline and both transports, including existing SFU PCM conversions. Before collecting results, complete a measurement contract for the intended workload and approved limits, using the [resource contract](../audit/acceptance/resource-contract.json) as the starting template. A null threshold means undecided, not zero or unlimited. Performance cannot pass while an applicable threshold is undecided.

Record model IDs, Pipecat version, audio formats, and turn settings alongside the contract. Explain any inapplicable field. Save an approved contract separately and preserve the original template and earlier measurements.

Include the managed SFU path in capacity, cost, and latency measurements. Work outside the Worker still contributes to the complete call.

Run these comparisons on the same runtime and build. Record every repeat, including failures:

- Measure the existing PCM conversion with representative input, packet sizes, and output duration. Include it in the complete deployed pipeline measurement; a local comparison cannot substitute for that result.
- Run sustained conversation with audio flowing in both directions, TTS bursts produced faster than they can be played, and a slow receiver. Track waiting audio in both bytes and milliseconds.
- Generate a long bot response while the microphone is muted, during silence or discontinuous transmission (DTX), and with no incoming user media. Test the API's documented CPU and lifetime rules. Do not assume that an arriving packet resets its allowance.
- Run the target number of concurrent calls, each in its own Durable Object (DO), along with bursts and cold starts. Record which isolates host the calls where that is observable. One DO per call does not establish one isolate per call.
- Introduce lost, late, and reordered media, reconnects, provider throttling, and controlled restarts. Measure recovery without silently resetting cumulative counters.

Collect the following measurements:

- Platform-measured CPU per event and per second of audio.
- Processing delay at p50, p95, p99, and maximum.
- Peak and steady memory for the whole isolate.
- Queue size, age, and growth.
- Time from the end of user speech to the first response audio.
- Time from interruption to the last audible sample from the old response.
- Remote resources with known owners and those whose status remains unknown.
- Cost per call-minute.

Save runtime logs, errors, workload duration, input-fixture hashes, and sample counts. JavaScript wall-clock time does not measure platform CPU. A sample of the Python or JavaScript heap does not measure all memory in the isolate.

To pass, a run must produce no stale output after the agreed boundary, no sustained queue growth, and no termination caused by a resource limit. Memory must stay bounded. Cleanup must either finish or report failure. The run must meet the latency, spare-capacity, and cost targets chosen before testing.

A short local PCM benchmark cannot establish these conditions. Raising a hard CPU allowance also leaves unanswered whether the system processes audio as quickly as it arrives.

## Recording evidence

Every live result needs fixed source and artifact identifiers, runtime and deployment identity, input-fixture hashes, and timestamps. Record whether it used real or simulated services, along with workload duration, concurrency, raw traces, sample counts, and the thresholds applied.

Mark missing measurements as untested. Keep failed runs when a retry passes. Do not combine different revisions into one acceptance result.

The recorded baseline includes the offline audit, controlled behavioral tests, local PCM measurements, and tests of the audit tools. Task 1 adds controlled reference evidence but no live acceptance result. Candidate installation, the new hosted Smart Turn and Pipecat speech/output integration, actual Workers execution of that configuration, live SFU behavior, resource acceptance, and physical audio still need testing. The audit did not deploy or change a live service.
