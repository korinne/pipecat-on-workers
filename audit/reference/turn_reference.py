"""Controlled probes of the installed Pipecat turn controller; no model inference.

Run with a normally installed pipecat-ai==1.11.0 environment and -I.
This is an interface probe, not a Workers adapter or a live speech test.
"""
import argparse
import asyncio
import hashlib
import importlib.metadata
import inspect
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from pipecat.audio.turn.base_turn_analyzer import BaseTurnAnalyzer, BaseTurnParams, EndOfTurnState
from pipecat.clocks.system_clock import SystemClock
from pipecat.frames.frames import STTMetadataFrame, TranscriptionFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame
from pipecat.processors.frame_processor import FrameProcessorSetup
from pipecat.turns.user_start.vad_user_turn_start_strategy import VADUserTurnStartStrategy
from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_controller import UserTurnController
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.utils.asyncio.task_manager import TaskManager


class ControlledAnalyzer(BaseTurnAnalyzer):
    """Provider leaf with an explicit result/release barrier, without model code."""
    def __init__(self, complete=True, held=False, fail=False):
        super().__init__(sample_rate=16000)
        self.complete, self.fail = complete, fail
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        if not held:
            self.release.set()

    @property
    def speech_triggered(self):
        return True

    @property
    def params(self):
        return BaseTurnParams()

    def append_audio(self, buffer, is_speech):
        return EndOfTurnState.INCOMPLETE

    async def analyze_end_of_turn(self):
        self.entered.set()
        await self.release.wait()
        if self.fail:
            raise RuntimeError("controlled provider failure")
        return (EndOfTurnState.COMPLETE if self.complete else EndOfTurnState.INCOMPLETE), None

    def clear(self):
        pass


async def run_case(name):
    events = []
    analyzer = ControlledAnalyzer(complete=name != "incomplete_watchdog", held=name == "resume_during_pending", fail=name == "detector_error")
    strategy = TurnAnalyzerUserTurnStopStrategy(turn_analyzer=analyzer, wait_for_transcript=True)
    manager = TaskManager()
    controller = UserTurnController(user_turn_strategies=UserTurnStrategies(start=[VADUserTurnStartStrategy()], stop=[strategy]), user_turn_stop_timeout=0.05)
    # This controller-level fixture does not execute a PipelineWorker.
    await controller.setup(FrameProcessorSetup(clock=SystemClock(), task_manager=manager, pipeline_worker=None))
    for event in ("on_user_turn_started", "on_user_turn_inference_triggered", "on_user_turn_stopped", "on_user_turn_stop_timeout"):
        async def record(*args, _name=event):
            events.append(_name)
        controller.add_event_handler(event, record)
    async def send(frame):
        events.append(type(frame).__name__)
        await controller.process_frame(frame)
    def transcript(finalized=True):
        return TranscriptionFrame("Tuesday or Thursday.", "fixture", "2026-10-05T00:00:00Z", finalized=finalized)
    snapshots = {}
    def snapshot(key):
        snapshots[key] = {"inference": events.count("on_user_turn_inference_triggered"), "stopped": events.count("on_user_turn_stopped")}
    try:
        # Positive cases use an intentionally long timeout so only finalized
        # transcripts release them. This is not an STT latency measurement.
        if name != "no_stt_metadata":
            await send(STTMetadataFrame(service_name="controlled-stt", ttfs_p99_latency=10.0))
        await send(VADUserStartedSpeakingFrame())
        if name == "completion_before_transcript":
            await send(VADUserStoppedSpeakingFrame(stop_secs=0.2))
            snapshot("before_transcript")
            await send(transcript())
            await send(transcript())
        elif name == "transcript_before_completion":
            await send(transcript())
            snapshot("before_completion")
            await send(VADUserStoppedSpeakingFrame(stop_secs=0.2))
        elif name == "resume_during_pending":
            await send(transcript())
            pending = asyncio.create_task(send(VADUserStoppedSpeakingFrame(stop_secs=0.2)))
            await analyzer.entered.wait()
            await send(VADUserStartedSpeakingFrame())
            await send(transcript())
            snapshot("before_old_result")
            analyzer.release.set()
            await pending
        elif name == "no_stt_metadata":
            await send(transcript(finalized=False))
            await send(VADUserStoppedSpeakingFrame(stop_secs=0.2))
            await asyncio.sleep(0.02)
        elif name == "incomplete_watchdog":
            await controller.start()
            await send(transcript())
            await send(VADUserStoppedSpeakingFrame(stop_secs=0.2))
            await asyncio.sleep(0.12)
        elif name == "detector_error":
            await send(transcript())
            try:
                await send(VADUserStoppedSpeakingFrame(stop_secs=0.2))
            except RuntimeError as exc:
                events.append(str(exc))
        snapshot("final")
        expected = {
            "completion_before_transcript": (1, 1), "transcript_before_completion": (1, 1),
            "resume_during_pending": (1, 0), "no_stt_metadata": (1, 1),
            "incomplete_watchdog": (0, 1), "detector_error": (0, 0),
        }[name]
        actual = snapshots["final"]
        if (actual["inference"], actual["stopped"]) != expected:
            raise AssertionError({"expected": expected, "actual": actual})
        if name == "completion_before_transcript" and snapshots["before_transcript"]["inference"] != 0:
            raise AssertionError("inference preceded transcript")
        if name == "transcript_before_completion" and snapshots["before_completion"]["inference"] != 0:
            raise AssertionError("inference preceded completion")
        return {"case": name, "status": "scoped_pass" if name in ("completion_before_transcript", "transcript_before_completion") else "observed_gap", "events": events, "counts": snapshots}
    finally:
        await controller.cleanup()
        for task in tuple(manager.current_tasks()):
            await manager.cancel_task(task)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Refusing to overwrite evidence")
    if importlib.metadata.version("pipecat-ai") != "1.11.0":
        raise SystemExit("Probe requires Pipecat 1.11.0")
    repo = Path(__file__).resolve().parents[2]
    modules = [BaseTurnAnalyzer, TurnAnalyzerUserTurnStopStrategy, UserTurnController, VADUserTurnStartStrategy]
    identities = {}
    for module in modules:
        path = Path(inspect.getfile(module)).resolve()
        if repo in path.parents:
            raise SystemExit("Vendored source must not satisfy this probe")
        identities[module.__name__] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    report = {"schema": 1, "timestamp": datetime.now(timezone.utc).isoformat(), "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(), "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "python": platform.python_version(), "platform": platform.platform(), "pipecat": "1.11.0", "modules": identities, "environment": "CPython controller-level controlled events; no network, audio, provider, Workers or production code", "limits": ["Concurrent resume probe directly calls controller.process_frame while detector awaits; a full pipeline scheduling reproduction is still needed", "50ms watchdog is accelerated fixture time, not a proposed production timeout", "Watchdog emits stop without inference event; actual user aggregator's stop handler can still flush context with run_llm=True", "No modified strategy or Workers adapter is implemented"], "cases": []}
    for name in ("completion_before_transcript", "transcript_before_completion", "resume_during_pending", "no_stt_metadata", "incomplete_watchdog", "detector_error"):
        try:
            report["cases"].append(await run_case(name))
        except Exception as exc:
            report["cases"].append({"case": name, "status": "test_error", "error": repr(exc)})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps([{k: case[k] for k in ("case", "status")} for case in report["cases"]]))
    if any(c["status"] == "test_error" for c in report["cases"]):
        raise SystemExit(2)


if __name__ == "__main__":
    asyncio.run(main())
