#!/usr/bin/env python3
"""Probe installed Pipecat reference imports and startup without altering it.

Run with a normally installed Pipecat 1.11.0 environment, from any directory:
  python -I runtime_probe.py --archive /path/pipecat_ai-1.11.0.tar.gz --output /new.json

Import and thread guards run in fresh subprocesses. They deny operations to
identify a dependency, not emulate Workers. No package source is patched and no
fake dependency modules are installed. Actual Workers execution remains untested.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import importlib.abc
import importlib.metadata
import json
import pathlib
import platform
import subprocess
import sys
import tarfile
import threading
import traceback
from datetime import datetime, timezone


ARCHIVE_SHA256 = "49e7532a1f035e8884c47448a06d998a1b7f0943d11eae9bc8600a9e749a2a04"
COMPONENTS = {
    "tts": "pipecat.services.tts_service",
    "output": "pipecat.transports.base_output",
    "aggregators": "pipecat.processors.aggregators.llm_response_universal",
    "stt": "pipecat.services.stt_service",
    "llm": "pipecat.services.llm_service",
    "turn_strategy": "pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy",
    "http_smart_turn": "pipecat.audio.turn.smart_turn.http_smart_turn",
    "base_smart_turn": "pipecat.audio.turn.smart_turn.base_smart_turn",
    "worker": "pipecat.pipeline.worker",
}
DENIED_IMPORTS = {
    "audioop", "numpy", "PIL", "soxr", "loudness", "onnxruntime", "nltk",
    "numba", "soundfile", "resampy", "aiohttp", "openai",
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


class GuardRejection(RuntimeError):
    pass


class DenyImports(importlib.abc.MetaPathFinder):
    def __init__(self, denied):
        self.denied = set(denied)

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in self.denied:
            raise GuardRejection(f"import denied by local probe: {fullname}")


def deny_threads(attempts=None):
    previous = threading.Thread.start

    def deny(self, *args, **kwargs):
        if attempts is not None:
            attempts.append({"thread_name": self.name, "stack": traceback.format_stack()})
        raise GuardRejection("threading.Thread.start denied by local probe")

    threading.Thread.start = deny
    return previous


def exception_record(exc):
    return {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}


async def check_http_analyzer(guard):
    import aiohttp
    from loguru import logger
    from pipecat.audio.turn.smart_turn.http_smart_turn import HttpSmartTurnAnalyzer

    log_messages = []
    logger.remove()
    sink = logger.add(lambda message: log_messages.append(str(message)), level="DEBUG")
    requests = []
    trace = aiohttp.TraceConfig()

    async def request_started(session, context, params):
        requests.append({"method": params.method, "url": str(params.url)})

    trace.on_request_start.append(request_started)
    previous = deny_threads() if guard else None
    result = {"network": "No remote provider called; an unused loopback URL records any request attempt."}
    try:
        async with aiohttp.ClientSession(trace_configs=[trace]) as session:
            analyzer = HttpSmartTurnAnalyzer(
                url="http://127.0.0.1:9/not-a-provider", aiohttp_session=session, sample_rate=16000
            )
            analyzer.append_audio(b"\x10\x00" * 320, is_speech=True)
            try:
                state, metrics = await asyncio.wait_for(analyzer.analyze_end_of_turn(), 3)
                result.update({"state": str(state), "metrics": metrics.model_dump() if metrics else None})
            except Exception as exc:
                result["exception"] = exception_record(exc)
            finally:
                await analyzer.cleanup()
    finally:
        if previous:
            threading.Thread.start = previous
        logger.remove(sink)
    result.update({"requests_started": requests, "logs": log_messages})
    return result


async def check_worker_prewarm():
    from loguru import logger
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineWorker
    from pipecat.processors.frame_processor import FrameProcessor
    from pipecat.utils.asyncio.task_manager import TaskManager
    from pipecat.workers.base_worker import WorkerParams

    logger.remove()
    manager = TaskManager()
    worker = PipelineWorker(
        Pipeline([FrameProcessor()]), enable_rtvi=False, enable_turn_tracking=False,
        idle_timeout_secs=None,
    )
    attempts = []
    previous = deny_threads(attempts)
    try:
        # _setup is the exact startup operation run by PipelineWorker.run.
        # Calling it directly retains the original exception and bounds this probe.
        try:
            await asyncio.wait_for(worker._setup(WorkerParams(task_manager=manager)), 3)
            result = {"unexpected_success": True}
        except Exception as exc:
            # TaskManager catches the thread exception and returns None, so the
            # startup caller can fail secondarily while unpacking that result.
            result = {"exception": exception_record(exc)}
    finally:
        threading.Thread.start = previous
        await worker._cleanup(cleanup_pipeline=True)
    result["thread_start_attempts"] = attempts
    return result


def run_case(case):
    if case.startswith("import:") or case.startswith("guard-import:"):
        guarded = case.startswith("guard-import:")
        name = case.split(":", 1)[1]
        if guarded:
            sys.meta_path.insert(0, DenyImports(DENIED_IMPORTS))
        module = importlib.import_module(COMPONENTS[name])
        return {"module": module.__name__, "path": module.__file__}
    if case.startswith("deny-one:"):
        _, component, dependency = case.split(":")
        sys.meta_path.insert(0, DenyImports([dependency]))
        module = importlib.import_module(COMPONENTS[component])
        return {"module": module.__name__, "path": module.__file__}
    if case == "worker-prewarm":
        return asyncio.run(check_worker_prewarm())
    if case == "http-analyzer-thread-guard":
        return asyncio.run(check_http_analyzer(True))
    if case == "http-analyzer-original":
        return asyncio.run(check_http_analyzer(False))
    if case == "http-wire":
        import numpy as np
        from pipecat.audio.turn.smart_turn.http_smart_turn import HttpSmartTurnAnalyzer
        # Serialization is synchronous and does not read the HTTP session.
        analyzer = HttpSmartTurnAnalyzer(url="unused", aiohttp_session=None, sample_rate=16000)
        payload = analyzer._serialize_array(np.zeros(320, dtype=np.float32))
        return {
            "payload_magic_hex": payload[:6].hex(), "payload_bytes": len(payload),
            "encoding": "NumPy .npy float32 array", "content_type": "application/octet-stream",
            "consumer_result_keys": ["prediction", "probability"],
            "workers_ai_documented_result_keys": ["is_complete", "probability"],
            "source_only_limit": "Response-key comparison uses published schema; hosted API not called.",
        }
    if case == "sentence-tokenizer-import":
        from pipecat.utils.string import _sent_tokenizer
        sys.meta_path.insert(0, DenyImports(["nltk"]))
        _sent_tokenizer()
    if case == "default-turn-import":
        from pipecat.turns.user_turn_strategies import UserTurnStrategies
        sys.meta_path.insert(0, DenyImports(["onnxruntime"]))
        UserTurnStrategies()
    raise ValueError(f"Unknown case: {case}")


def child(case):
    try:
        result = {"ok": True, "observation": run_case(case)}
    except Exception as exc:
        result = {"ok": False, "exception": exception_record(exc)}
    print(json.dumps(result, sort_keys=True))


def run_child(case):
    try:
        process = subprocess.run(
            [sys.executable, "-I", "-B", str(pathlib.Path(__file__).resolve()), "--case", case],
            cwd=pathlib.Path(__file__).parent, capture_output=True, text=True, timeout=35,
        )
    except subprocess.TimeoutExpired as exc:
        return {"case": case, "status": "test_error", "classification": "test limitation", "error": "subprocess timeout"}
    try:
        raw = json.loads(process.stdout)
    except json.JSONDecodeError:
        return {"case": case, "status": "test_error", "classification": "test limitation", "exit_code": process.returncode, "stdout": process.stdout, "stderr": process.stderr}
    result = {"case": case, "exit_code": process.returncode, "raw": raw, "stderr": process.stderr}
    if case.startswith("import:"):
        passed = raw["ok"] and process.returncode == 0
        result.update(status="scoped_pass" if passed else "test_error", classification="local CPython import")
    elif case == "http-analyzer-original":
        observation = raw.get("observation", {})
        observed = raw["ok"] and not observation.get("requests_started") and any(
            "no running event loop" in message for message in observation.get("logs", [])
        )
        result.update(status="observed_gap" if observed else "test_error", classification="package defect" if observed else "test limitation")
    elif case == "http-analyzer-thread-guard":
        observed = raw["ok"] and raw.get("observation", {}).get("exception", {}).get("type") == "GuardRejection"
        result.update(status="observed_gap" if observed else "test_error", classification="package/runtime incompatibility under local thread guard")
    elif case == "worker-prewarm":
        attempts = raw.get("observation", {}).get("thread_start_attempts", [])
        observed = raw["ok"] and any("warm_lazy_imports" in "".join(item["stack"]) for item in attempts)
        result.update(status="observed_gap" if observed else "test_error", classification="package/runtime incompatibility under local thread guard" if observed else "test limitation")
    elif case == "http-wire":
        result.update(status="source_observation" if raw["ok"] else "test_error", classification="integration adapter requirement")
    else:
        observed = not raw["ok"] and (
            raw.get("exception", {}).get("type") == "GuardRejection"
            or "import denied by local probe: onnxruntime" in raw.get("exception", {}).get("traceback", "")
        )
        result.update(status="observed_gap" if observed else "test_error", classification="package/runtime incompatibility under local guard" if observed else "test limitation")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path)
    parser.add_argument("--case")
    args = parser.parse_args()
    if args.case:
        child(args.case)
        return 0
    if not args.archive or not args.output:
        parser.error("--archive and --output are required")
    if args.output.exists():
        parser.error("output already exists; preserve old evidence and select a new path")
    archive_sha = digest(args.archive.read_bytes())
    if archive_sha != ARCHIVE_SHA256:
        parser.error("archive checksum differs from the pinned reference")
    distribution = importlib.metadata.distribution("pipecat-ai")
    if distribution.version != "1.11.0":
        parser.error("this reference probe requires normally installed Pipecat 1.11.0")
    source_provenance = []
    with tarfile.open(args.archive) as archive:
        for module in [*COMPONENTS.values(), "pipecat.utils.string", "pipecat.audio.utils", "pipecat.turns.user_turn_strategies", "pipecat.utils.prewarm"]:
            relative = module.replace(".", "/") + ".py"
            installed = pathlib.Path(distribution.locate_file(relative))
            archived = archive.extractfile("pipecat_ai-1.11.0/src/" + relative).read()
            installed_sha = digest(installed.read_bytes())
            archive_source_sha = digest(archived)
            source_provenance.append({"module": module, "path": str(installed), "installed_sha256": installed_sha, "archive_source_sha256": archive_source_sha, "matches_archive": installed_sha == archive_source_sha})
    if not all(item["matches_archive"] for item in source_provenance):
        parser.error("installed reference source differs from verified archive")
    cases = ["import:" + key for key in COMPONENTS]
    cases += ["guard-import:" + key for key in COMPONENTS]
    cases += [
        "deny-one:tts:loudness", "deny-one:tts:soxr", "deny-one:output:PIL",
        "deny-one:http_smart_turn:numpy", "deny-one:http_smart_turn:aiohttp",
        "worker-prewarm", "http-analyzer-thread-guard", "http-analyzer-original",
        "http-wire", "sentence-tokenizer-import", "default-turn-import",
    ]
    report = {
        "schema": "task1-runtime-reference-v1", "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "application_source_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=pathlib.Path(__file__).resolve().parents[2], text=True).strip(),
        "readiness": "not_established", "runtime": {"python": sys.version, "executable": sys.executable, "platform": platform.platform(), "machine": platform.machine(), "isolated": bool(sys.flags.isolated), "assertions_enabled": __debug__},
        "probe_sha256": digest(pathlib.Path(__file__).read_bytes()),
        "archive": {"name": args.archive.name, "sha256": archive_sha},
        "distribution": {"name": distribution.metadata["Name"], "version": distribution.version, "requires_dist": distribution.requires, "direct_url": distribution.read_text("direct_url.json")},
        "resolved_distributions": sorted(({"name": item.metadata["Name"], "version": item.version} for item in importlib.metadata.distributions()), key=lambda item: item["name"].lower()),
        "source_provenance": source_provenance,
        "guard_scope": {"denied_import_roots": sorted(DENIED_IMPORTS), "thread_operation": "threading.Thread.start", "limits": ["The guard identifies import/thread requirements on local CPython; it is not a Workers emulator.", "No dependency is declared unsupported on Workers solely because this guard denies it.", "Other native extensions, direct low-level thread APIs, networking, snapshotting, and deployment packaging are not covered.", "Imports run in fresh isolated subprocesses without vendored source on sys.path. Package source and dependency modules are unchanged."]},
        "harness_development_note": "The initial classifier expected the prewarm rejection to propagate. TaskManager catches it and startup raises a secondary TypeError. This version records thread attempts and their call stacks, preserving the secondary exception, so an unrelated TypeError cannot count as the expected gap.",
        "observations": [run_child(case) for case in cases],
        "untested": ["Normal Pipecat dependency installation on actual Workers/Pyodide", "Selected installed components executing in a Python Durable Object", "Async hosted Smart Turn client through Python/JavaScript binding", "NLTK punkt_tab build-time packaging and runtime loading on Workers", "Deployed audio-only output without threads", "Workers compatibility of each mandatory native dependency"],
        "sources": [{"url": "https://developers.cloudflare.com/workers-ai/models/smart-turn-v2/", "use": "documented is_complete and probability result fields; checked during Task 1"}],
    }
    report["counts"] = {status: sum(item["status"] == status for item in report["observations"]) for status in ("scoped_pass", "observed_gap", "source_observation", "test_error")}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output), "counts": report["counts"], "readiness": report["readiness"]}))
    return 2 if report["counts"]["test_error"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
