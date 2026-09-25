#!/usr/bin/env python3
"""Exercise actual vendored Pipecat without native audio modules or new threads.

This checks the conversation runtime on CPython. It is intentionally not labeled
as a Workers, provider, browser, audio, or deployment test.
"""
from __future__ import annotations

import asyncio
import argparse
import importlib.abc
import json
import pathlib
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

FORBIDDEN = {"numpy", "PIL", "audioop", "soxr", "loudness", "onnxruntime", "nltk", "numba", "soundfile", "resampy", "aiohttp", "openai"}


class DenyNative(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in FORBIDDEN:
            raise RuntimeError(f"Unsupported dependency imported in narrow configuration: {fullname}")


sys.meta_path.insert(0, DenyNative())


def deny_thread(*args, **kwargs):
    raise RuntimeError("OS thread creation is forbidden in this compatibility check")


threading.Thread.start = deny_thread

from loguru import logger
logger.remove()
from pipecat.frames.frames import (
    Frame, InterruptionFrame, LLMContextFrame, LLMFullResponseStartFrame,
    LLMFullResponseEndFrame, TextFrame, ProposedUserStartedSpeakingFrame,
    ProposedUserStoppedSpeakingFrame, TranscriptionFrame,
)
from pipecat.pipeline.worker import PipelineWorker, PipelineParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies
from pipecat.utils.asyncio.task_manager import TaskManager
from pipecat.workers.base_worker import WorkerParams


class StubGeneration(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.calls = 0
        self.pending = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.completed = asyncio.Event()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame) and direction == FrameDirection.DOWNSTREAM:
            self.calls += 1
            await self.push_frame(LLMFullResponseStartFrame())
            if self.calls == 1:
                await self.push_frame(TextFrame("Acknowledged prefix."))
                self.pending.set()
                try:
                    await asyncio.sleep(30)
                    await self.push_frame(TextFrame("STALE AUDIO TEXT"))
                except asyncio.CancelledError:
                    self.cancelled.set()
                    raise
            else:
                await self.push_frame(TextFrame("Second reply."))
                await self.push_frame(LLMFullResponseEndFrame())
                self.completed.set()
        else:
            await self.push_frame(frame, direction)


class ObserveOutput(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.frames = []
        self.first_text = asyncio.Event()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        self.frames.append(type(frame).__name__)
        if isinstance(frame, TextFrame):
            self.frames.append(frame.text)
            self.first_text.set()
        await self.push_frame(frame, direction)


async def check(record_modules=None):
    started = asyncio.Event()
    context = LLMContext([{"role": "system", "content": "Compatibility check."}])
    pair = LLMContextAggregatorPair(context, user_params=LLMUserAggregatorParams(
        user_turn_strategies=ExternalUserTurnStrategies(), vad_analyzer=None,
    ))
    generation = StubGeneration()
    observed = ObserveOutput()
    interrupted_context_committed = asyncio.Event()

    @pair.assistant().event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(aggregator, message):
        if message.interrupted:
            interrupted_context_committed.set()
    worker = PipelineWorker(Pipeline([pair.user(), generation, observed, pair.assistant()]),
        enable_rtvi=False, enable_turn_tracking=False, enable_import_prewarm=False,
        idle_timeout_secs=None, params=PipelineParams(enable_metrics=False),
    )

    @worker.event_handler("on_pipeline_started")
    async def on_started(worker, frame):
        started.set()

    manager = TaskManager()
    run = asyncio.create_task(worker.run(WorkerParams(task_manager=manager)))
    try:
        await asyncio.wait_for(started.wait(), 3)
        await worker.queue_frame(ProposedUserStartedSpeakingFrame())
        await worker.queue_frame(TranscriptionFrame("First question.", "user", "2026-09-24T00:00:00Z"))
        await worker.queue_frame(ProposedUserStoppedSpeakingFrame())
        await asyncio.wait_for(generation.pending.wait(), 3)
        await asyncio.wait_for(observed.first_text.wait(), 1)
        await asyncio.sleep(0.02)
        cancellation_start = time.perf_counter()
        await worker.queue_frame(ProposedUserStartedSpeakingFrame())
        await asyncio.wait_for(generation.cancelled.wait(), 2)
        cancellation_ms = (time.perf_counter() - cancellation_start) * 1000
        # An upstream cancellation does not mean its downstream assistant context
        # flush has already completed. Respect the real aggregator commit event.
        await asyncio.wait_for(interrupted_context_committed.wait(), 2)
        await worker.queue_frame(TranscriptionFrame("New question.", "user", "2026-09-24T00:00:01Z"))
        await worker.queue_frame(ProposedUserStoppedSpeakingFrame())
        await asyncio.wait_for(generation.completed.wait(), 3)
        await asyncio.sleep(0.05)
        messages = context.get_messages()
        assert generation.calls == 2, generation.calls
        assert "STALE AUDIO TEXT" not in observed.frames, observed.frames
        assert [m["content"] for m in messages if m["role"] == "user"] == ["First question.", "New question."], messages
        assert [m["content"] for m in messages if m["role"] == "assistant"] == ["Acknowledged prefix.", "Second reply."], messages
        assert [m["role"] for m in messages] == ["system", "user", "assistant", "user", "assistant"], messages
        await worker.cancel()
        await asyncio.wait_for(run, 3)
        await asyncio.sleep(0)
        remaining = [t.get_name() for t in manager.current_tasks() if not t.done()]
        assert not remaining, remaining
        if record_modules:
            root = pathlib.Path(__file__).resolve().parents[1] / "src" / "pipecat"
            modules = set()
            for module in list(sys.modules.values()):
                file = getattr(module, "__file__", None)
                if file and pathlib.Path(file).is_relative_to(root):
                    modules.add(pathlib.Path(file).relative_to(root).as_posix())
            modules.add("pipeline/task.py")  # Deprecated upstream alias remains available.
            pathlib.Path(record_modules).write_text("# Pipecat1.11.0 module closure exercised by check_pipecat_core.py\n" + "\n".join(sorted(modules)) + "\n")
        print(json.dumps({"passed": True, "scope": "CPython core runtime; synthetic frames, no provider audio", "generations": generation.calls,
            "server_cancellation_ms": round(cancellation_ms, 3), "interruption_frames": observed.frames.count("InterruptionFrame"),
            "context": messages, "live_pipecat_tasks_after_cancel": remaining,
            "forbidden_imports_loaded": sorted(FORBIDDEN.intersection(sys.modules)), "threads_created": 0}, indent=2))
    finally:
        if not run.done():
            await worker.cancel()
            await asyncio.wait_for(run, 3)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-modules", type=pathlib.Path)
    asyncio.run(check(parser.parse_args().record_modules))
