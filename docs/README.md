# Pipecat on Workers

Build a supported voice application on Python Workers using Pipecat, with Workers AI handling speech recognition, hosted Pipecat Smart Turn, GPT-OSS-120B language generation, and Aura-2 speech synthesis. Support two ways to carry audio: a direct browser WebSocket and Cloudflare Realtime SFU.

The repository contains a working prototype and a plan for bringing it to that target. Its current code still uses vendored Pipecat, Flux-controlled turn endings, and custom conversation-history logic. The documentation update does not implement the target or establish production readiness.

## Review before coding

| Read in this order | Decision it supports |
| --- | --- |
| [Goals](GOALS.md) | Agree the supported configuration and the six behaviors a release must satisfy. |
| [Conversation behavior](CONVERSATION.md) | Agree to use Pipecat's normal speech/output and assistant-context path, including interruption and recovery. |
| [Transport integration](TRANSPORTS.md) | Agree how the two existing audio routes connect to the same conversation. |
| [Acceptance plan](ACCEPTANCE.md) | Agree what observations count as success and which limits still need values. |
| [Implementation order](IMPLEMENTATION-PLAN.md) | Give each fresh coding session one bounded task and a stopping point. |

Task 1 has recorded the [reference configuration](GOALS.md#task-1-reference-configuration), [speech/output observations](CONVERSATION.md#task-1-speech-reference), and [reproducible gaps](EVIDENCE.md#task-1-reference-investigation). Live model checks remain untested because existing authentication failed. Task 2 is the next bounded implementation session; it has not started. Set performance thresholds before judging live performance.

## Supporting material

| Document | Use it for |
| --- | --- |
| [Capability request](CAPABILITY-REQUEST.md) | The concrete package, runtime, and integration requirements, with evidence and limits. |
| [Evidence](EVIDENCE.md) | What the experiments observed, which source they tested, and what remains untested. |
| [Current implementation](BASELINE.md) | How the prototype works today, including its routes, audio formats, ownership, and known limitations. |
| [Development and test guide](DEVELOPMENT.md) | Setup, repeatable diagnostics, candidate-package checks, and live test procedures. |

The goals define scope. The acceptance plan defines release evidence. Older test results describe their recorded experiments; they cannot add requirements to either document. Keep new results separate from recorded evidence, and identify the application, package, models, runtime, and test configuration used.

All substantive documentation is in this folder. Code and machine-readable evidence remain beside their existing tools. The root [integrity manifest](../HANDOFF-MANIFEST.json) records the files included in the handoff.
