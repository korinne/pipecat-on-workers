#!/usr/bin/env python3
"""Controlled Pipecat 1.11.0 HTTP TTS/output/context reference; no network inference.

Run with an isolated, normally installed pipecat-ai==1.11.0 and bundled punkt_tab.
Only the HTTP response and transport write leaves are doubles. Upstream service,
sentence aggregation, queues, output chunking, interruption, and context are real.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys
import tarfile
import traceback
from datetime import datetime, timezone

if not __debug__:
    raise RuntimeError("Run this evidence probe with assertions enabled (omit -O and -OO).")

import pipecat
from loguru import logger
from pipecat.frames.frames import (
    ErrorFrame, Frame, InterruptionFrame, LLMFullResponseEndFrame,
    LLMFullResponseStartFrame, StartFrame, TextFrame, TTSAudioRawFrame, TTSTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair, LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.deepgram.tts import DeepgramHttpTTSService
from pipecat.services.tts_service import TextAggregationMode
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import TransportParams
from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies
from pipecat.utils.asyncio.task_manager import TaskManager
from pipecat.workers.base_worker import WorkerParams

ARCHIVE_SHA256 = "49e7532a1f035e8884c47448a06d998a1b7f0943d11eae9bc8600a9e749a2a04"
FIRST = "Tuesday morning is available."
SECOND = "Thursday afternoon is also available."
PCM = b"\x01\x00" * 480  # One nonzero PCM16 mono 20 ms output chunk at 24 kHz.
CASES = (
    "complete", "interrupt_before_output", "interrupt_mid_first_sentence",
    "interrupt_after_first_sentence", "error_before_audio", "error_mid_first_sentence",
    "error_after_first_sentence", "error_only_sentence_before_audio",
    "error_only_sentence_partial", "interrupt_before_buffered_tail_write",
)


def record(trace, stage, event, **values):
    trace.append({"sequence": len(trace), "stage": stage, "event": event, **values})


def validate_error_fixture(case, trace):
    """A swallowed harness timeout must never count as the intended provider error."""
    errors = [e for e in trace if e["stage"] == "provider" and e["event"] == "raise_error"]
    expected = 1 if case.startswith("error_") else 0
    assert len(errors) == expected, (case, "controlled error not reached", errors)
    if not errors:
        return
    error = errors[0]
    before = trace[:error["sequence"]]
    accepted = [e for e in before if e["stage"] == "output" and e["event"] == "write_accepted"]
    if case in ("error_mid_first_sentence", "error_only_sentence_partial"):
        assert len(accepted) == 1, (case, "expected one accepted chunk before failure", accepted)
    elif case == "error_after_first_sentence":
        assert len(accepted) == 3, (case, "first sentence audio did not progress", accepted)
        assert any(e["stage"] == "after_output" and e["event"] == "TTSTextFrame"
                   and e.get("text") == FIRST for e in before), (case, "first sentence text did not progress")
    elif case in ("error_before_audio", "error_only_sentence_before_audio"):
        assert not accepted, (case, "failure was expected before audio", accepted)
    surfaced = [e for e in trace if e["event"] == "ErrorFrame" and e["stage"] == "before_tts"]
    assert any("controlled TTS provider failure" in e.get("error", "") for e in surfaced), (case, surfaced)
    assert all(e["sequence"] > error["sequence"] for e in surfaced), (case, "error arrived before controlled failure")


class Response:
    """The HTTP provider boundary, used by unchanged DeepgramHttpTTSService."""

    status = 200

    def __init__(self, session, number):
        self.session = session
        self.number = number
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def iter_chunked(self, chunk_size):
        session = self.session
        before = session.case in ("error_before_audio", "error_only_sentence_before_audio") and self.number == 1
        partial = session.case in ("error_mid_first_sentence", "error_only_sentence_partial") and self.number == 1
        later = session.case == "error_after_first_sentence" and self.number == 2
        if later:
            await asyncio.wait_for(session.first_text_progress.wait(), 3)
        if before or later:
            record(session.trace, "provider", "raise_error", sentence=self.number)
            raise OSError("controlled TTS provider failure")
        if session.case == "interrupt_before_buffered_tail_write":
            record(session.trace, "provider", "audio", sentence=self.number, chunk=1,
                   note="10 ms remains below the 20 ms transport chunk size")
            yield PCM[:480]
            return
        for index in range(3):
            record(session.trace, "provider", "audio", sentence=self.number, chunk=index + 1)
            yield PCM
            if partial:
                await asyncio.wait_for(session.first_audio_progress.wait(), 3)
                record(session.trace, "provider", "raise_error", sentence=self.number)
                raise OSError("controlled TTS provider failure after partial audio")
        record(session.trace, "provider", "sentence_complete", sentence=self.number)


class Session:
    def __init__(self, case, trace):
        self.case = case
        self.trace = trace
        self.requests = []
        self.first_text_progress = asyncio.Event()
        self.first_audio_progress = asyncio.Event()

    def post(self, url, *, headers, json, params):
        self.requests.append({"url": url, "text": json["text"], "params": params})
        record(self.trace, "provider", "request", sentence=len(self.requests), text=json["text"])
        return Response(self, len(self.requests))


class Output(BaseOutputTransport):
    """Writes acknowledge acceptance by this test sink, never browser playback."""

    def __init__(self, case, trace, session):
        super().__init__(TransportParams(
            audio_out_enabled=True, audio_out_sample_rate=24000,
            audio_out_channels=1, audio_out_10ms_chunks=2,
            audio_out_end_silence_secs=0, audio_out_write_timeout_secs=10,
        ))
        self.case = case
        self.trace = trace
        self.session = session
        self.attempts = 0
        self.accepted = 0
        self.blocked = asyncio.Event()
        self.write_cancelled = asyncio.Event()

    async def start(self, frame: StartFrame):
        await super().start(frame)
        await self.set_transport_ready(frame)

    async def write_audio_frame(self, frame):
        self.attempts += 1
        attempt = self.attempts
        record(self.trace, "output", "write_attempt", chunk=attempt, bytes=len(frame.audio))
        blocked_at = {
            "interrupt_before_output": 1,
            "interrupt_mid_first_sentence": 2,
            "interrupt_after_first_sentence": 4,
            "interrupt_before_buffered_tail_write": 1,
        }.get(self.case)
        if attempt == blocked_at:
            self.blocked.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                record(self.trace, "output", "write_cancelled", chunk=attempt)
                self.write_cancelled.set()
                raise
        self.accepted += 1
        record(self.trace, "output", "write_accepted", chunk=attempt)
        self.session.first_audio_progress.set()
        return True


class Observe(FrameProcessor):
    def __init__(self, stage, trace, session=None):
        super().__init__()
        self.stage = stage
        self.trace = trace
        self.session = session

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        data = {"direction": direction.name}
        if isinstance(frame, TextFrame):
            data.update(text=frame.text, append_to_context=frame.append_to_context)
        if isinstance(frame, ErrorFrame):
            data.update(error=frame.error, fatal=frame.fatal)
        if isinstance(frame, TTSAudioRawFrame):
            data.update(bytes=len(frame.audio))
        record(self.trace, self.stage, type(frame).__name__, **data)
        if self.session is not None and isinstance(frame, TTSTextFrame) and frame.text == FIRST:
            self.session.first_text_progress.set()
        await self.push_frame(frame, direction)


async def scenario(case):
    trace = []
    session = Session(case, trace)
    context = LLMContext([{"role": "user", "content": "Which appointments are available?"}])
    pair = LLMContextAggregatorPair(context, user_params=LLMUserAggregatorParams(
        user_turn_strategies=ExternalUserTurnStrategies(), vad_analyzer=None,
    ))
    tts = DeepgramHttpTTSService(
        api_key="fixture-no-network", aiohttp_session=session, sample_rate=24000,
        settings=DeepgramHttpTTSService.Settings(voice="aura-2-luna-en"),
        text_aggregation_mode=TextAggregationMode.SENTENCE,
        # Bound an audio-less fake response, which otherwise uses the default 3 s.
        stop_frame_timeout_s=0.1,
    )
    output = Output(case, trace, session)
    worker = PipelineWorker(
        Pipeline([Observe("before_tts", trace), tts, Observe("tts_output", trace),
                  output, Observe("after_output", trace, session), pair.assistant()]),
        enable_rtvi=False, enable_turn_tracking=False, idle_timeout_secs=None,
        params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=24000),
    )
    started = asyncio.Event()
    stopped = asyncio.Event()
    stopped_messages = []

    @worker.event_handler("on_pipeline_started")
    async def on_started(worker, frame):
        started.set()

    @pair.assistant().event_handler("on_assistant_turn_stopped")
    async def on_stopped(aggregator, message):
        stopped_messages.append({"content": message.content, "interrupted": message.interrupted})
        record(trace, "aggregator", "committed", content=message.content,
               interrupted=message.interrupted)
        stopped.set()

    manager = TaskManager()
    running = asyncio.create_task(worker.run(WorkerParams(task_manager=manager)))
    try:
        await asyncio.wait_for(started.wait(), 20)
        answer = FIRST if case.startswith("error_only_sentence") or case == "interrupt_before_buffered_tail_write" else FIRST + " " + SECOND
        for frame in [LLMFullResponseStartFrame(), TextFrame(answer),
                      LLMFullResponseEndFrame()]:
            await worker.queue_frame(frame)
        if case.startswith("interrupt_"):
            await asyncio.wait_for(output.blocked.wait(), 5)
            await worker.queue_frame(InterruptionFrame())
            await asyncio.wait_for(output.write_cancelled.wait(), 3)
        await asyncio.wait_for(stopped.wait(), 5)
        messages = json.loads(json.dumps(context.get_messages()))
        await worker.cancel()
        await asyncio.wait_for(running, 5)
        remaining = [task.get_name() for task in manager.current_tasks() if not task.done()]
        assert not remaining, remaining
        assistant = [m["content"] for m in messages if m["role"] == "assistant"]
        expected = {
            "complete": [FIRST + " " + SECOND],
            "interrupt_before_output": [],
            "interrupt_mid_first_sentence": [],
            "interrupt_after_first_sentence": [FIRST],
            # Observation of the standard nonfatal ErrorFrame flow, not desired recovery.
            "error_before_audio": [FIRST + " " + SECOND],
            "error_mid_first_sentence": [FIRST + " " + SECOND],
            "error_after_first_sentence": [FIRST + " " + SECOND],
            "error_only_sentence_before_audio": [FIRST],
            "error_only_sentence_partial": [FIRST],
            "interrupt_before_buffered_tail_write": [FIRST],
        }[case]
        assert assistant == expected, (case, assistant, expected)
        expected_audio = {"complete": 6, "interrupt_before_output": 0,
                          "interrupt_mid_first_sentence": 1, "interrupt_after_first_sentence": 3,
                          "error_before_audio": 3, "error_mid_first_sentence": 4,
                          "error_after_first_sentence": 3, "error_only_sentence_before_audio": 0,
                          "error_only_sentence_partial": 1,
                          "interrupt_before_buffered_tail_write": 0}[case]
        assert output.accepted == expected_audio, (case, output.accepted, expected_audio)
        validate_error_fixture(case, trace)
        return {"case": case,
                "status": "observed_gap" if case.startswith("error_") else "supported_in_scope",
                "classification": "application_error_policy_needed" if case.startswith("error_") else "reference_behavior",
                "context": messages,
                "accepted_output_chunks": output.accepted, "tts_requests": session.requests,
                "turn_stopped_events": stopped_messages, "events": trace,
                "pipecat_tasks_remaining_after_cancel": remaining}
    except Exception as error:
        return {"case": case, "status": "test_error", "error": repr(error),
                "traceback": traceback.format_exc(), "events": trace}
    finally:
        if not running.done():
            await worker.cancel()
            await asyncio.wait_for(running, 5)


def provenance(archive):
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert digest == ARCHIVE_SHA256, digest
    root = Path(pipecat.__file__).resolve().parent
    distribution = importlib.metadata.distribution("pipecat-ai")
    assert distribution.version == "1.11.0", distribution.version
    assert root == Path(distribution.locate_file("pipecat")).resolve(), root
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    assert not direct.get("dir_info", {}).get("editable"), direct
    loaded = {}
    with tarfile.open(archive) as source:
        for name, module in list(sys.modules.items()):
            path = getattr(module, "__file__", None)
            if not name.startswith("pipecat") or not path or not path.endswith(".py"):
                continue
            file = Path(path).resolve()
            assert file.is_relative_to(root), str(file)
            relative = file.relative_to(root)
            original = source.extractfile("pipecat_ai-1.11.0/src/pipecat/" + str(relative)).read()
            assert file.read_bytes() == original, str(relative)
            loaded[name] = {"path": str(file), "sha256": hashlib.sha256(original).hexdigest()}
    data_root = Path(os.environ["NLTK_DATA"])
    data_files = {str(p.relative_to(data_root)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(data_root.rglob("*")) if p.is_file()}
    return {"package": "pipecat-ai", "version": distribution.version,
            "archive_sha256": digest, "pipecat_path": str(root),
            "archive_path": str(archive.resolve()), "python_executable": sys.executable,
            "python_assertions_enabled": __debug__, "direct_url": direct,
            "all_loaded_pipecat_sources_match_archive": True, "loaded_sources": loaded,
            "python": sys.version, "platform": platform.platform(),
            "dependencies": {d.metadata["Name"]: d.version
                             for d in sorted(importlib.metadata.distributions(),
                                             key=lambda d: d.metadata["Name"].lower())},
            "nltk_data_path": str(data_root.resolve()), "nltk_data_files_sha256": data_files,
            "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


async def main(args):
    if args.output.exists():
        raise SystemExit("Refusing to overwrite evidence; choose a fresh output path")
    logger.remove()
    logger.add(sys.stderr, level="ERROR")
    results = [await scenario(case) for case in CASES]
    report = {
        "schema_version": 1,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Local CPython controlled provider/output reference; no Workers, browser, SFU, or model inference.",
        "pipeline": "DeepgramHttpTTSService -> BaseOutputTransport subclass -> paired LLMAssistantAggregator",
        "provider_limit": "Deepgram HTTP session is a deterministic double. Workers AI Aura needs a TTSService adapter; this does not validate the binding or Deepgram endpoint.",
        "output_limit": "write_audio_frame return means accepted by fixture sink; no claim of audible playback or exact word progress.",
        "error_policy": "Upstream nonfatal ErrorFrame observed with default worker continuation; no custom recovery or context filtering.",
        "findings": [
            "Completion commits both sentence text frames after their output queue entries.",
            "With full 20 ms audio chunks, interrupting before a sentence's TTSTextFrame reaches the assistant preserves only earlier whole sentences; no exact word boundary is inferred.",
            "A sentence's TTSTextFrame can precede its final subchunk audio: a 10 ms answer interrupted before the padded 20 ms write retains the sentence despite zero accepted fixture writes.",
            "A nonfatal HTTP TTS error does not suppress the failed sentence's TTSTextFrame or stop later synthesis. Even an entirely audio-less answer enters context. The Workers adapter needs a tested failure policy before it supports recovery.",
        ],
        "untested": ["Workers AI Aura binding", "Python Workers execution", "real browser playback",
                     "SFU output", "resampling between different rates", "word timestamps",
                     "chosen fail-closed TTS adapter and reconnect behavior"],
        "configuration": {"sample_rate": 24000, "channels": 1, "encoding": "PCM16LE",
                          "sentence_audio_ms": 60, "output_chunk_ms": 20,
                          "aggregation": "SENTENCE", "push_start_frame": True,
                          "push_stop_frames": True, "push_text_frames": True,
                          "stop_frame_timeout_s": 0.1,
                          "push_silence_after_stop": False,
                          "pause_frame_processing": False,
                          "reuse_context_id_within_turn": True,
                          "video_out_enabled": False, "audio_mixer": None,
                          "audio_out_end_silence_secs": 0,
                          "audio_out_write_timeout_secs": 10,
                          "pipeline_enable_rtvi": False, "pipeline_enable_turn_tracking": False,
                          "pipeline_idle_timeout_secs": None,
                          "pipeline_processor_unusable_policy": "CONTINUE",
                          "prewarm_override": None,
                          "prewarm_behavior": "Unmodified upstream default startup; threads allowed in this CPython reference.",
                          "fixture_progress_deadline_secs": 3,
                          "fixture_audio_source": "Nonzero synthetic PCM, no real speech; 60 ms per ordinary sentence, 20 ms for partial-failure and 10 ms for subchunk-tail cases.",
                          "note": "Timeout reduced from 3 s only to bound no-audio fixtures; no timing score."},
        "results": results,
        "provenance": provenance(args.archive),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as output_file:
        output_file.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "results": [
        {"case": r["case"], "status": r["status"],
         "context": r.get("context"), "error": r.get("error")}
        for r in results]}, indent=2))
    return 2 if any(r["status"] == "test_error" for r in results) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(asyncio.run(main(parser.parse_args())))
