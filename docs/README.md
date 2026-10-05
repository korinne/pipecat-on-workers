# Pipecat on Workers

Build a supported voice application on Python Workers using Pipecat, with Workers AI handling speech recognition, hosted Pipecat Smart Turn, GPT-OSS-120B language generation, and Aura-2 speech synthesis. Support two ways to carry audio: a direct browser WebSocket and Cloudflare Realtime SFU.

The repository contains a prototype and a plan for bringing it to that target. Its current code uses vendored Pipecat, Nova-3 with hosted Smart Turn, GPT-OSS and the standard speech/output/assistant-context path. The [speech integration](SPEECH-CONTEXT.md) and [package investigation](PACKAGE-CANDIDATE.md) distinguish implemented behavior from release support.

Both voice examples start without a demo access key. Anyone with the URL can create a call using the deployment’s Workers AI and SFU resources. Each call still uses a private session token.

## Review before coding

| Read in this order | Decision it supports |
| --- | --- |
| [Goals](GOALS.md) | Agree the supported configuration and the six behaviors a release must satisfy. |
| [Conversation behavior](CONVERSATION.md) | Agree to use Pipecat's normal speech/output and assistant-context path, including interruption and recovery. |
| [Transport integration](TRANSPORTS.md) | Agree how the two existing audio routes connect to the same conversation. |
| [Acceptance plan](ACCEPTANCE.md) | Agree what observations count as success and which limits still need values. |
| [Implementation order](IMPLEMENTATION-PLAN.md) | Track the remaining implementation and verification work. |

Task 1 recorded the reference configuration and speech/output behavior. The continuation adds live Nova/Smart Turn investigation, GPT-OSS streaming, standard assistant context on both adapters, and bounded cleanup ownership. Historical checks remain in [Evidence](EVIDENCE.md). Published Pipecat packages still cannot satisfy the Workers installation requirement, so B1 remains open. The user has deferred performance acceptance. Their Chrome/headphones laptop check failed with turn-readiness errors; a tested silence fallback remains a separate proposal pending the turn-policy decision. The [continuation handoff](CONTINUATION-HANDOFF.md) records the active deployment, commit sequence and next action.

## Supporting material

| Document | Use it for |
| --- | --- |
| [Capability request](CAPABILITY-REQUEST.md) | The concrete package, runtime, and integration requirements, with evidence and limits. |
| [Evidence](EVIDENCE.md) | What the experiments observed, which source they tested, and what remains untested. |
| [Current implementation](BASELINE.md) | How the prototype works today, including its routes, audio formats, ownership, and known limitations. |
| [Development and test guide](DEVELOPMENT.md) | Setup, repeatable diagnostics, candidate-package checks, and live test procedures. |

The goals define scope. The acceptance plan defines release evidence. Older test results describe their recorded experiments; they cannot add requirements to either document. Keep new results separate from recorded evidence, and identify the application, package, models, runtime, and test configuration used.

All substantive documentation is in this folder. Code and machine-readable evidence remain beside their existing tools. The root [integrity manifest](../HANDOFF-MANIFEST.json) records the files included in the handoff.
