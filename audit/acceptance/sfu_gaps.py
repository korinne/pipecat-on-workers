#!/usr/bin/env python3
"""Observe two application gaps using the repository's existing SFU fixtures.

This runs real vendored Pipecat with synthetic provider I/O and a transport
double. It never contacts Cloudflare SFU or measures audible latency. An
``observed_gap`` result is evidence of missing behavior, not capability success.
Exit 0 means both expected gaps were reproduced; 1 means behavior differed;
2 means a probe could not run reliably. No repository source is modified.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import pathlib
import platform
import subprocess
import sys
import traceback
from datetime import datetime, timezone

TIMEOUT = 8
SCOPE = (
    "Local CPython; real repository Pipecat pipeline; synthetic providers and "
    "SFU transport double. No live SFU, Workers runtime, codec, acoustic, "
    "performance, or platform-capability validation."
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def provenance(repo):
    files = (
        "tests/test_sfu_conversation.py", "src/runtime_probe.py",
        "src/conversation.py", "src/sfu_transport.py",
        "src/pipecat/VENDOR_MANIFEST.json",
        "src/pipecat/processors/frame_processor.py",
    )
    value = {
        "repo": str(repo), "python": platform.python_version(),
        "python_executable": sys.executable,
        "project_declares_python": ">=3.14,<3.15",
        "python_meets_project_requirement": sys.version_info[:2] == (3, 14),
        "source_sha256": {name: digest(repo / name) for name in files},
        "probe_sha256": digest(pathlib.Path(__file__)),
    }
    try:
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              text=True, capture_output=True, check=True)
        diff = subprocess.run(["git", "-C", str(repo), "diff", "HEAD", "--"],
                              capture_output=True, check=True)
        value.update(commit=head.stdout.strip(), tracked_diff_sha256=hashlib.sha256(diff.stdout).hexdigest(),
                     tracked_worktree_dirty=bool(diff.stdout))
    except (OSError, subprocess.CalledProcessError) as exc:
        value["git_provenance_unavailable"] = type(exc).__name__
    return value


def load_fixture(repo):
    """Use the selected repository fixture and verify its imports came from it."""
    sys.path.insert(0, str(repo / "src"))
    spec = importlib.util.spec_from_file_location(
        "audit_sfu_fixture", repo / "tests" / "test_sfu_conversation.py")
    if not spec or not spec.loader:
        raise RuntimeError("Unable to load the selected repository's SFU fixture")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import conversation
    import pipecat
    import runtime_probe
    loaded = {}
    for item in (conversation, pipecat, runtime_probe):
        path = pathlib.Path(item.__file__).resolve()
        if not path.is_relative_to(repo / "src"):
            raise RuntimeError(f"Unexpected import source for {item.__name__}: {path}")
        loaded[item.__name__] = str(path)
    try:
        from loguru import logger
        logger.remove()
    except ImportError:
        pass
    return module.SfuHarness, loaded


def watch_responses(harness):
    """Observe completed calls without changing their scheduling or result."""
    completed = asyncio.Queue()
    original = harness.session.respond

    async def observe():
        await original()
        completed.put_nowait(harness.session.generation)

    harness.session.respond = observe
    return completed


async def close_harness(harness):
    return await asyncio.wait_for(harness.close(), TIMEOUT)


async def history_probe(SfuHarness):
    harness = SfuHarness()
    completed = watch_responses(harness)
    result = None
    try:
        await harness.start()
        await harness.turn("Give me two suggestions", 1)
        await asyncio.wait_for(completed.get(), TIMEOUT)
        first_reply = [event["text"] for event in harness.events
                       if event.get("type") == "transcript" and event.get("role") == "assistant"]
        first_finished = list(harness.transport.finished)
        await harness.turn("Expand your second suggestion", 2)
        await asyncio.wait_for(completed.get(), TIMEOUT)
        generations = harness.provider.generations
        second_context = generations[1] if len(generations) >= 2 else []
        assistant = [message for message in second_context if message.get("role") == "assistant"]
        persisted_assistant = [message for message in harness.saved[-1]["messages"]
                               if message.get("role") == "assistant"]
        prerequisites = bool(first_reply and first_finished and len(generations) == 2)
        gap = prerequisites and not assistant and not persisted_assistant
        result = {
            "id": "SFU-HISTORY", "status": "observed_gap" if gap else "untested",
            "baseline_behavior_differs": not gap,
            "review_reason": None if gap else "Baseline behavior differs; inspect before making a capability claim.",
            "hypothesis_tested": "Completed SFU replies are absent from the next model input and persisted assistant history.",
            "observations": {
                "completed_first_reply_transcript": first_reply,
                "first_transport_finished_generations": first_finished,
                "provider_generation_count": len(generations),
                "second_model_input": second_context,
                "assistant_messages_in_second_model_input": assistant,
                "assistant_messages_in_latest_saved_state": persisted_assistant,
                "output_packets_submitted_to_double": len(harness.transport.sent),
            },
            "interpretation": (
                "Application history gap reproduced; submission and EOS are not played receipts. "
                "Choose generated/unconfirmed versus playback-confirmed history semantics before fixing. "
                "This does not establish a missing Cloudflare platform feature or test model semantics."
            ) if gap else "Baseline behavior differs; inspect the observations before drawing a capability conclusion.",
        }
    finally:
        cleanup = await close_harness(harness)
        if result is not None:
            result["cleanup"] = cleanup
    return result


async def clear_probe(SfuHarness):
    from pipecat.frames.frames import LLMContextFrame

    harness = SfuHarness()
    completed = watch_responses(harness)
    entered = asyncio.Event()
    release = asyncio.Event()
    next_context_queued = asyncio.Event()
    invalidation_returned = asyncio.Event()
    clear_returned = asyncio.Event()
    result = None
    try:
        await harness.start()
        await harness.turn("First turn", 1)
        await asyncio.wait_for(completed.get(), TIMEOUT)
        before = len(harness.provider.generations)

        original_clear = harness.transport.clear
        original_invalidate = harness.session.invalidate
        original_queue = harness.session.processor.queue_frame

        async def held_clear(generation):
            await original_clear(generation)
            entered.set()
            await release.wait()
            clear_returned.set()

        async def observe_invalidate(reason):
            await original_invalidate(reason)
            invalidation_returned.set()

        async def observe_queue(frame, *args, **kwargs):
            await original_queue(frame, *args, **kwargs)
            if isinstance(frame, LLMContextFrame):
                next_context_queued.set()

        harness.transport.clear = held_clear
        harness.session.invalidate = observe_invalidate
        harness.session.processor.queue_frame = observe_queue

        await harness.begin("Next turn while cleanup is pending", 2)
        await asyncio.wait_for(entered.wait(), TIMEOUT)
        await harness.finish("Next turn while cleanup is pending", 2)
        await asyncio.wait_for(next_context_queued.wait(), TIMEOUT)
        generation = harness.session.generation
        # This is an event-based causal snapshot: the next LLM frame has reached
        # its processor while clear is suspended on a gate controlled here.
        # It is not a stopwatch test or a claimed latency threshold.
        snapshot = {
            "clear_entered": entered.is_set(),
            "clear_returned": clear_returned.is_set(),
            "release_gate_set": release.is_set(),
            "next_context_queued_at_response_processor": next_context_queued.is_set(),
            "invalidation_returned": invalidation_returned.is_set(),
            "provider_generation_count_before_hold": before,
            "provider_generation_count_during_hold": len(harness.provider.generations),
            "browser_clear_event_already_emitted": ("clear", generation) in harness.timeline,
            "server_clear_metric_already_recorded_for_generation": any(
                item.get("event") == "server_clear" and item.get("generation") == generation
                for item in harness.session.metrics),
        }
        release.set()
        await asyncio.wait_for(invalidation_returned.wait(), TIMEOUT)
        await asyncio.wait_for(completed.get(), TIMEOUT)
        after = len(harness.provider.generations)
        gap = (snapshot["browser_clear_event_already_emitted"]
               and not snapshot["invalidation_returned"]
               and not snapshot["clear_returned"]
               and snapshot["provider_generation_count_during_hold"] == before
               and after == before + 1)
        result = {
            "id": "SFU-CLEAR", "status": "observed_gap" if gap else "untested",
            "baseline_behavior_differs": not gap,
            "review_reason": None if gap else "Baseline behavior differs; inspect before making a capability claim.",
            "hypothesis_tested": "The next model turn waits behind application invalidation while transport.clear is held.",
            "observations": {
                "while_clear_held": snapshot,
                "after_release": {"invalidation_returned": invalidation_returned.is_set(),
                                  "clear_returned": clear_returned.is_set(),
                                  "provider_generation_count": after},
            },
            "interpretation": (
                "Application scheduling dependency reproduced using a held transport double. "
                "The browser clear event is sent before the wait; this does not show that local "
                "playback keeps running. No REST request or acoustic timing was measured, "
                "and no Cloudflare platform deficiency is inferred."
            ) if gap else "Baseline behavior differs; inspect the observations before drawing a capability conclusion.",
        }
    finally:
        release.set()
        cleanup = await close_harness(harness)
        if result is not None:
            result["cleanup"] = cleanup
    return result


async def run_probes(SfuHarness):
    results = []
    for identifier, probe in (("SFU-HISTORY", history_probe), ("SFU-CLEAR", clear_probe)):
        try:
            results.append(await asyncio.wait_for(probe(SfuHarness), 4 * TIMEOUT))
        except Exception as exc:
            results.append({"id": identifier, "status": "test_error",
                            "error_type": type(exc).__name__, "error": str(exc),
                            "traceback": traceback.format_exc()})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    report = {"schema_version": 1, "kind": "sfu_application_gap_observations",
              "recorded_at_utc": datetime.now(timezone.utc).isoformat(), "scope": SCOPE,
              "capability_acceptance": "not_passed", "results": []}
    try:
        report["provenance"] = provenance(repo)
        SfuHarness, loaded = load_fixture(repo)
        report["provenance"]["loaded_module_paths"] = loaded
        report["results"] = asyncio.run(run_probes(SfuHarness))
    except Exception as exc:
        report["results"] = [{"id": "SFU-PROBE-SETUP", "status": "test_error",
                              "error_type": type(exc).__name__, "error": str(exc),
                              "traceback": traceback.format_exc()}]
    statuses = [item["status"] for item in report["results"]]
    if "test_error" in statuses:
        report["execution_status"], code = "incomplete", 2
    elif statuses != ["observed_gap", "observed_gap"]:
        report["execution_status"], code = "behavior_changed_review_required", 1
    else:
        report["execution_status"], code = "two_gaps_reproduced", 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"execution_status": report["execution_status"],
                      "capability_acceptance": report["capability_acceptance"],
                      "results": [{"id": result["id"], "status": result["status"]}
                                  for result in report["results"]],
                      "output": str(args.output.resolve())}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
