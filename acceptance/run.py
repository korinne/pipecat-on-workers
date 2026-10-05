#!/usr/bin/env python3
"""Check proposed conversation behavior against the current application.

Each local probe runs in a fresh process with a deadline. Providers, audio and
transport I/O are synthetic; Pipecat's queues and the application are real.
Exit 1 means a requirement remains unresolved. Exit 2 means a probe failed to
produce reliable evidence. This runner cannot establish release readiness.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import pathlib
import platform
import subprocess
import sys
import tempfile
import traceback
from collections import Counter
from datetime import datetime, timezone

TIMEOUT = 6
PROCESS_TIMEOUT = 35
LOCAL_SCOPE = (
    "Local CPython, repository Pipecat and application, synthetic providers, "
    "silent PCM, captured WebSocket events or an SFU transport double. "
    "No deployed Worker, network media, physical playback or resource acceptance."
)
CASES = {
    "ws-context": ("B3.generated-context.websocket", "websocket", "history", False),
    "sfu-context": ("B3.generated-context.sfu", "sfu", "history", False),
    "ws-receipt-control": ("B3.receipt-control.websocket", "websocket", "history", True),
    "ws-cancel": ("B4.pending-generation.websocket", "websocket", "cancel", False),
    "sfu-cancel": ("B4.pending-generation.sfu", "sfu", "cancel", False),
    "sfu-cleanup": ("B4.cleanup-order.sfu", "sfu", "cleanup", False),
}


class ProbeError(RuntimeError):
    """The experiment did not establish its prerequisites."""


def require(condition, message):
    # Do not use assert: callers may start Python with optimization enabled.
    if not condition:
        raise ProbeError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def classify(prerequisites, expected_behavior):
    require(prerequisites, "Probe prerequisites were not satisfied")
    return "scoped_pass" if expected_behavior else "observed_gap"


def exit_code(results):
    statuses = {item["status"] for item in results}
    require(statuses <= {"scoped_pass", "observed_gap", "untested", "test_error"},
            "Unknown result status")
    if "test_error" in statuses:
        return 2
    # Live acceptance is not implemented by this local runner, even if someone
    # filters the result list down to local successes.
    return 1


def check_origin(module, root):
    path = pathlib.Path(module.__file__).resolve()
    require(path.is_relative_to(root), f"Unexpected import origin: {module.__name__}: {path}")
    return str(path)


def load_fixtures(repo):
    require(sys.flags.optimize == 0, "Fixture assertions must be enabled")
    sys.path.insert(0, str(repo / "src"))
    spec = importlib.util.spec_from_file_location(
        "acceptance_sfu_fixture", repo / "tests/test_sfu_conversation.py")
    require(spec is not None and spec.loader is not None, "Unable to load SFU fixture")
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    check_origin(fixture, repo / "tests")
    import conversation
    import runtime_probe
    from loguru import logger
    logger.remove()
    loaded = {}
    for name, module in list(sys.modules.items()):
        if name == "pipecat" or name.startswith("pipecat.") or name in {"conversation", "runtime_probe"}:
            loaded[name] = check_origin(module, repo / "src")
    require(conversation is sys.modules["conversation"], "Conversation import changed")
    return {"websocket": runtime_probe.Harness, "sfu": fixture.SfuHarness}, loaded


def watch_responses(harness):
    completed = asyncio.Queue()
    original = harness.session.respond

    async def observe():
        await original()
        completed.put_nowait(harness.session.generation)

    harness.session.respond = observe
    return completed


def assistant_text(messages):
    return [item.get("content") for item in messages if item.get("role") == "assistant"]


def classify_history(expected_reply, reported_reply, next_context, saved_messages):
    # The transcript is application output, so it cannot also define the oracle.
    # A transcript and history that lose the same suffix must never pass together.
    require(" ".join(reported_reply) == expected_reply,
            "First reported reply differs from the fixture's complete generated answer")
    return classify(True, expected_reply in assistant_text(next_context)
                    and expected_reply in assistant_text(saved_messages))


def submitted(harness, route):
    if route == "sfu":
        return [{"generation": generation, "bytes": len(pcm)} for pcm, generation in harness.transport.sent]
    return [{"generation": event["generation"], "chunk_id": event["chunk_id"]} for event in harness.audio()]


async def close_harness(harness):
    state = await asyncio.wait_for(harness.close(), TIMEOUT)
    required_zero = ("pipecat_tasks", "unacked_audio_bytes", "pending_playback_chunks",
                     "fixture_live_generations", "fixture_live_syntheses")
    require(all(state[key] == 0 for key in required_zero), "Fixture resources remain after close")
    require(harness.provider.closed, "Fixture provider did not close")
    if hasattr(harness, "transport"):
        require(harness.transport.closed, "SFU transport double did not close")
    return {key: state[key] for key in required_zero} | {"provider_closed": harness.provider.closed}


async def history(harness, route, completed, receipts):
    first_prompt = "Give me two suggestions"
    # FixtureProviders.generate independently defines this deterministic output.
    # Deriving it from the application's transcript would hide matching loss in
    # both the transcript and conversation history.
    expected_reply = f"{harness.provider.label} reply to {first_prompt}."
    await harness.turn(first_prompt, 1)
    await asyncio.wait_for(completed.get(), TIMEOUT)
    first_reply = [event["text"] for event in harness.events
                   if event.get("type") == "transcript" and event.get("role") == "assistant"]
    first_packets = submitted(harness, route)
    require(first_reply and first_packets, "First reply was not generated and submitted")
    if receipts:
        require(route == "websocket", "Receipt control is only available in the direct WebSocket fixture")
        await harness.acknowledge()
    await harness.turn("Expand your second suggestion", 2)
    await asyncio.wait_for(completed.get(), TIMEOUT)
    require(len(harness.provider.generations) == 2, "Expected exactly two model calls")
    require(harness.saved, "No persisted conversation was observed")
    context = harness.provider.generations[1]
    status = classify_history(expected_reply, first_reply, context, harness.saved[-1]["messages"])
    return {
        "status": status,
        "expectation": (
            "The first completed generated answer is in the next model input and saved history, "
            + ("after simulated player receipts." if receipts else "without requiring player receipts.")
        ),
        "observations": {
            "expected_complete_fixture_reply": expected_reply,
            "first_generated_reply": first_reply,
            "first_audio_packets_submitted": len(first_packets),
            "simulated_receipts_sent": len(first_packets) if receipts else 0,
            "second_model_input": context,
            "assistant_history_saved": assistant_text(harness.saved[-1]["messages"]),
        },
        "limit": (
            "This checks text availability, not whether a model understands the follow-up. "
            "Receipts here are function calls, not browser or human playback evidence. "
            "Standard Pipecat TTS/output progress and interrupted-context behavior are not exercised here."
        ),
    }


async def cancel(harness, route, completed):
    harness.provider.hold_generation = True
    await harness.turn("obsolete request", 1)
    await asyncio.wait_for(harness.provider.generation_started.wait(), TIMEOUT)
    old_generation = harness.session.generation
    await harness.begin("replacement request", 2)
    await asyncio.wait_for(harness.provider.generation_cancelled.wait(), TIMEOUT)
    harness.provider.hold_generation = False
    harness.provider.generation_gate.set()
    await harness.finish("replacement request", 2)
    await asyncio.wait_for(completed.get(), TIMEOUT)
    packets = submitted(harness, route)
    require(len(harness.provider.generations) == 2 and packets, "Replacement answer was not submitted")
    stale = [item for item in packets if item["generation"] <= old_generation]
    clear = [item for item in harness.events if item.get("type") == "clear"
             and item.get("generation", 0) > old_generation]
    correct = not stale and bool(clear) and harness.provider.syntheses == ["fixture reply to replacement request."]
    return {
        "status": classify(True, correct),
        "expectation": "Interrupt a held model call, discard its output, then submit the replacement reply.",
        "observations": {"old_generation": old_generation, "new_generation": harness.session.generation,
                         "old_generation_cancelled": harness.provider.generation_cancelled.is_set(),
                         "stale_audio_packets": stale, "replacement_packets": len(packets),
                         "browser_clear_commands": len(clear), "synthesized_text": harness.provider.syntheses},
        "limit": "No audio was playing when interruption began. This does not establish audible stop latency or live transport recovery.",
    }


async def cleanup(harness, route, completed):
    from pipecat.frames.frames import LLMContextFrame
    require(route == "sfu", "Only the SFU fixture has an external transport.clear operation")
    await harness.turn("first request", 1)
    await asyncio.wait_for(completed.get(), TIMEOUT)
    entered, release, queued, returned = (asyncio.Event() for _ in range(4))
    original_clear = harness.transport.clear
    original_queue = harness.session.processor.queue_frame

    async def held_clear(generation):
        await original_clear(generation)
        entered.set()
        await release.wait()
        returned.set()

    async def observed_queue(frame, *args, **kwargs):
        await original_queue(frame, *args, **kwargs)
        if isinstance(frame, LLMContextFrame):
            queued.set()

    harness.transport.clear = held_clear
    harness.session.processor.queue_frame = observed_queue
    try:
        await harness.begin("next request", 2)
        await asyncio.wait_for(entered.wait(), TIMEOUT)
        invalidated_generation = harness.session.generation
        await harness.finish("next request", 2)
        await asyncio.wait_for(queued.wait(), TIMEOUT)
        # Give independently runnable work a turn. This observation window is
        # an experiment deadline, not a product latency threshold.
        started_before_release = False
        try:
            await asyncio.wait_for(completed.get(), 0.25)
            started_before_release = True
        except asyncio.TimeoutError:
            pass
        count_while_held = len(harness.provider.generations)
        clear_sent = any(event.get("type") == "clear" and event.get("generation") == invalidated_generation
                         for event in harness.events)
        require(entered.is_set() and queued.is_set() and not returned.is_set(), "Cleanup hold did not remain active")
        release.set()
        await asyncio.wait_for(returned.wait(), TIMEOUT)
        if not started_before_release:
            await asyncio.wait_for(completed.get(), TIMEOUT)
        require(len(harness.provider.generations) == 2, "Next model call did not run after release")
        return {
            "status": classify(clear_sent, count_while_held == 2),
            "expectation": "A queued next model call can begin while old external media cleanup is pending.",
            "observations": {"browser_clear_command_sent_before_release": clear_sent,
                             "next_context_queued_before_release": queued.is_set(),
                             "cleanup_observation_window_seconds": 0.25,
                             "model_calls_during_hold": count_while_held,
                             "model_calls_after_release": len(harness.provider.generations)},
            "limit": "The held operation is a transport double. No REST request, browser clear receipt or audible stop was measured.",
        }
    finally:
        release.set()


async def run_case(case, factories):
    identifier, route, kind, receipts = CASES[case]
    harness = factories[route]()
    result = None
    try:
        completed = watch_responses(harness)
        await asyncio.wait_for(harness.start(), TIMEOUT)
        if kind == "history":
            result = await history(harness, route, completed, receipts)
        elif kind == "cancel":
            result = await cancel(harness, route, completed)
        else:
            result = await cleanup(harness, route, completed)
    finally:
        closed = await close_harness(harness)
    return {"id": identifier, "transport": route, "scope": LOCAL_SCOPE, **result, "fixture_cleanup": closed}


def failure(identifier, exc):
    return {"id": identifier, "status": "test_error", "error_type": type(exc).__name__,
            "error": str(exc), "traceback": traceback.format_exc()}


def child(repo, case, output):
    try:
        factories, loaded = load_fixtures(repo)
        result = asyncio.run(asyncio.wait_for(run_case(case, factories), 4 * TIMEOUT))
        result["execution"] = {"assertions_enabled": __debug__, "python_optimization": sys.flags.optimize,
                               "loaded_module_paths": loaded}
    except Exception as exc:
        result = failure(CASES[case][0], exc)
    output.write_text(json.dumps(result, indent=2) + "\n")
    return 2 if result["status"] == "test_error" else 0


def subprocess_case(repo, case):
    with tempfile.TemporaryDirectory(prefix="pipecat-acceptance-") as directory:
        output = pathlib.Path(directory) / "result.json"
        env = os.environ.copy()
        env.pop("PYTHONOPTIMIZE", None)
        command = [sys.executable, "-u", str(pathlib.Path(__file__).resolve()), "--repo", str(repo),
                   "--case", case, "--output", str(output)]
        try:
            process = subprocess.run(command, capture_output=True, text=True, env=env, timeout=PROCESS_TIMEOUT)
            require(output.is_file(), f"Child produced no result (exit {process.returncode}): {process.stderr[-2000:]}")
            result = json.loads(output.read_text())
            require(result.get("id") == CASES[case][0], "Child returned the wrong case")
            require(result.get("status") in {"scoped_pass", "observed_gap", "test_error"}, "Invalid child result")
            expected_exit = 2 if result["status"] == "test_error" else 0
            require(process.returncode == expected_exit, f"Unexpected child exit: {process.returncode}")
            return result
        except Exception as exc:
            return failure(CASES[case][0], exc)


def provenance(repo):
    files = sorted(repo.glob("src/**/*.py")) + [repo / name for name in (
        "pyproject.toml", "tests/test_sfu_conversation.py", "src/pipecat/VENDOR_MANIFEST.json",
        "public/audio-player.mjs", "public/sfu-client.mjs")]
    packages = {}
    for name in ("loguru", "attrs", "docstring-parser", "pydantic", "typing-extensions"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    value = {"repo": str(repo), "python": platform.python_version(), "python_executable": sys.executable,
             "project_python_requirement": ">=3.14,<3.15", "python_matches_requirement": sys.version_info[:2] == (3, 14),
             "local_dependency_versions": packages, "parent_python_optimization": sys.flags.optimize,
             "child_optimization_policy": "PYTHONOPTIMIZE removed; no -O; fixtures require optimize=0",
             "source_sha256": {str(path.relative_to(repo)): digest(path) for path in files},
             "runner_sha256": digest(pathlib.Path(__file__).resolve())}
    try:
        root = subprocess.run(["git", "-C", str(repo), "rev-parse", "--show-toplevel"], check=True, capture_output=True, text=True)
        require(pathlib.Path(root.stdout.strip()).resolve() == repo, "Git metadata belongs to a parent directory")
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True)
        diff = subprocess.run(["git", "-C", str(repo), "diff", "HEAD", "--"], check=True, capture_output=True)
        value.update(commit=head.stdout.strip(), tracked_diff_sha256=hashlib.sha256(diff.stdout).hexdigest(),
                     tracked_worktree_dirty=bool(diff.stdout))
    except (OSError, subprocess.CalledProcessError, ProbeError) as exc:
        value["git_provenance_unavailable"] = str(exc)
    return value


def unresolved():
    entries = [
        ("B1.supported-install", "No supported unmodified Pipecat package was installed in actual Workers by this runner."),
        ("B2.real-voice", "No real microphone, speech provider, browser speaker or deployed Worker was used."),
        ("B2.hosted-turn", "Workers AI hosted Pipecat Smart Turn, speech onset, and transcript readiness are not integrated or exercised by these fixtures."),
        ("B3.reference-context", "Context from the selected standard Pipecat TTS/output and assistant-aggregator configuration, including interrupted speech, remains untested."),
        ("B4.audible-interruption", "A browser clear command is not a measurement of when audio stopped playing."),
        ("B5.live-lifecycle", "Fixture cleanup is checked in every probe; live reconnect, duplicate sessions and resource release still need acceptance runs."),
        ("B6.resources", "Workload, latency, CPU, memory and cost limits need agreed thresholds and deployed measurements."),
    ]
    return [{"id": identifier, "status": "untested", "reason": reason} for identifier, reason in entries]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--case", choices=CASES, help=argparse.SUPPRESS)
    args = parser.parse_args()
    repo = args.repo.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.case:
        return child(repo, args.case, args.output)
    report = {"schema_version": 1, "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
              "scope": LOCAL_SCOPE, "readiness": "not_established", "results": []}
    try:
        report["provenance"] = provenance(repo)
        report["results"] = [subprocess_case(repo, case) for case in CASES]
    except Exception as exc:
        report["results"] = [failure("HARNESS.setup", exc)]
    report["results"].extend(unresolved())
    report["counts"] = dict(Counter(item["status"] for item in report["results"]))
    code = exit_code(report["results"])
    report["exit_code"] = code
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"readiness": report["readiness"], "counts": report["counts"], "exit_code": code,
                      "results": [{"id": item["id"], "status": item["status"]} for item in report["results"]],
                      "output": str(args.output.resolve())}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
