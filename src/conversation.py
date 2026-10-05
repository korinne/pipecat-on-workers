"""Conversation machinery shared by the Python DO and explicitly synthetic tests.

Pipecat owns the frame queues, turn controller, user context aggregation and
interruption cancellation. This module adapts provider I/O and playback receipts.
"""
import asyncio
import base64
import contextlib
import copy
import re
import time

from pipecat.frames.frames import (
    Frame, InterruptionFrame, LLMContextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregator, LLMUserAggregatorParams
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.turns.user_start.vad_user_turn_start_strategy import VADUserTurnStartStrategy
from smart_turn import NovaTurnCoordinator, HostedTurnStopStrategy, HostedUserAggregator
from pipecat.utils.asyncio.task_manager import TaskManager
from pipecat.workers.base_worker import WorkerParams
from audio_transport import WebSocketAudioTransport, MAX_UNACKED_BYTES, MAX_PENDING_RECEIPTS
from speech_pipeline import (WorkersLLMService, WorkersTTSService, WorkersOutputTransport,
    PersistedAssistantAggregator, MAX_QUEUED_AUDIO_BYTES)

SYSTEM = ('You are a concise, friendly voice assistant. Reply in one or two short sentences, '
          'without markdown. Remember the conversation. Appointment availability is fictional '
          'and read-only. Never claim a booking was made. If a tool result is provided, use it.')
CONTEXT_SCHEMA = 2
MAX_PENDING_USER_FRAGMENTS = 16
MAX_PENDING_USER_CHARS = 8192
PERSIST_ATTEMPTS = 3
PERSIST_TIMEOUT = 1
PERSIST_RETRY_DELAY = .05


class ContextPersistenceError(RuntimeError):
    """The call cannot proceed with context that storage has not acknowledged."""


async def lookup_availability():
    """Harmless async example tool; deliberate delay makes cancellation testable."""
    await asyncio.sleep(0.8)
    return {"fictional": True, "appointments": ["Tuesday at 10 AM", "Thursday at 2 PM"], "booked": False}


class ConversationSession:
    def __init__(self, state, provider_factory, send, save, *, tool=lookup_availability,
                 on_fatal=None, turn_timeout_secs=5, user_turn_stop_timeout=30, audio_transport=None):
        self.state = state
        self.send = send
        self.save = save
        self.tool = tool
        self.on_fatal = on_fatal
        self.audio_transport = audio_transport
        # Older histories omit SFU answers or have receipt-dependent fragments.
        # Start fresh instead of fabricating missing speech. Existing tokens and
        # cleanup ownership are preserved; only conversation context is reset.
        self.history_reset = bool(state.get("messages")) and state.get("context_schema") != CONTEXT_SCHEMA
        messages = copy.deepcopy(state.get("messages", [])) if state.get("context_schema") == CONTEXT_SCHEMA else []
        dialogue = [m for m in messages if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)]
        self.context = LLMContext([{"role": "system", "content": SYSTEM}] + dialogue[-80:])
        self.generation = int(state.get("generation", 0)) + 1
        self.context_ready = asyncio.Event()
        self.context_ready.set()
        self.transport_kind = "webrtc" if audio_transport else "websocket"
        self.transport = audio_transport or WebSocketAudioTransport(
            lambda event: self.send(event), is_current=self.is_current, initial_generation=self.generation)
        self.queued_output_bytes = 0
        self.output_credit = asyncio.Event()
        self.output_credit.set()
        self.response_started = time.monotonic()
        self.input_audio_chunks = 0
        self.input_audio_bytes = 0
        self.forwarded_audio_bytes = 0
        self.started = asyncio.Event()
        self.closed = False
        self.provider = provider_factory(self.provider_event)
        self.manager = TaskManager()
        self.turn = NovaTurnCoordinator(self.provider, self._queue_turn_frame, self.send,
                                        self.measure, timeout=turn_timeout_secs)
        stop = HostedTurnStopStrategy(self.turn)
        self.user = HostedUserAggregator(self.context, coordinator=self.turn, params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(start=[VADUserTurnStartStrategy()], stop=[stop]),
            vad_analyzer=None, audio_idle_timeout=0, user_turn_stop_timeout=user_turn_stop_timeout,
        ))
        stop.aggregator = self.user
        self.user.before_commit = self.wait_for_context
        self.user.before_interrupt = self.context_ready.clear
        self.processor = WorkersLLMService(self)
        self.tts = WorkersTTSService(self)
        self.output = WorkersOutputTransport(self)
        self.assistant = PersistedAssistantAggregator(self, self.user)
        self.worker = PipelineWorker(Pipeline([self.user, self.processor, self.tts, self.output, self.assistant]),
            params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=24000),
            enable_rtvi=False, enable_turn_tracking=False, enable_import_prewarm=False,
            idle_timeout_secs=None, cancel_timeout_secs=3,
        )
        self.run_task = None
        self.last_activity = time.monotonic()
        self.began = self.last_activity
        self.metrics = []
        self.last_interrupt = None
        self.responding = False
        self.persistence_lock = asyncio.Lock()
        self.persistence_failed = False
        self.persistence_uncertain = False
        self.persistence_attempts = 0
        self.persistence_tasks = set()
        self.last_saved_state = copy.deepcopy(state)
        self.last_saved_context = copy.deepcopy(self.context.get_messages())
        self.save_sequence = self.saved_sequence = 0
        self.fatal_close_task = None
        self.close_task = None

        @self.worker.event_handler("on_pipeline_started")
        async def on_started(worker, frame):
            self.started.set()

    def measure(self, kind, values):
        self.metrics.append({"event": kind, "elapsed_ms": round((time.monotonic() - self.began) * 1000, 3), **values})
        self.metrics = self.metrics[-500:]

    def _persistence_failed(self, reason):
        if self.persistence_failed:
            return
        self.persistence_failed = True
        self.closed = True
        self.responding = False
        self.generation += 1
        self.context_ready.clear()
        self.context.set_messages(copy.deepcopy(self.last_saved_context))
        self.state.clear()
        self.state.update(copy.deepcopy(self.last_saved_state))
        self.queued_output_bytes = 0
        self.output_credit.set()
        self.turn.discard("context_persistence_failed")
        self.measure("context_persistence_failed", {"reason": reason,
                                                   "uncertain": self.persistence_uncertain})
        # A processor must not await cancellation of its own pipeline task.
        self.fatal_close_task = asyncio.create_task(self._end_after_persistence_failure())
        self.fatal_close_task.add_done_callback(lambda done: None if done.cancelled() else done.exception())

    async def _end_after_persistence_failure(self):
        for event in ({"type": "error", "code": "context_persistence_failed", "recoverable": False,
                       "message": "Conversation history could not be saved. This call has ended; start a new call."},
                      {"type": "clear", "generation": self.generation, "reason": "context_persistence_failed"}):
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.send(event), PERSIST_TIMEOUT)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.transport.clear(self.generation), PERSIST_TIMEOUT)
        if self.on_fatal:
            try:
                await asyncio.wait_for(self.on_fatal(), PERSIST_TIMEOUT)
                return
            except Exception:
                self.measure("fatal_owner_failed", {})
        await self.close("context_persistence_failed")

    async def persist(self):
        async with self.persistence_lock:
            if self.persistence_failed:
                raise ContextPersistenceError("Conversation history was not saved")
            # An interrupted write retains its owner until settlement. Do not
            # race a replacement write with a cancellation-resistant old one.
            pending = [task for task in self.persistence_tasks if not task.done()]
            if pending:
                _, unsettled = await asyncio.wait(pending, timeout=PERSIST_TIMEOUT)
                if unsettled:
                    self.persistence_uncertain = True
                    self._persistence_failed("previous_write_unsettled")
                    raise ContextPersistenceError("Conversation storage is still pending")
            messages = self.context.get_messages()
            if len(messages) > 81:
                self.context.set_messages([messages[0]] + messages[-80:])
            snapshot = copy.deepcopy(self.state)
            snapshot.update(context_schema=CONTEXT_SCHEMA, messages=copy.deepcopy(self.context.get_messages()),
                            generation=self.generation, updated_at=time.time(),
                            status="ended" if self.closed else "active")
            for attempt in range(PERSIST_ATTEMPTS):
                self.persistence_attempts += 1
                self.save_sequence += 1
                sequence = self.save_sequence
                task = asyncio.create_task(self.save(copy.deepcopy(snapshot)))
                self.persistence_tasks.add(task)

                def settled(done, snapshot=copy.deepcopy(snapshot), sequence=sequence):
                    self.persistence_tasks.discard(done)
                    if done.cancelled() or done.exception() is not None:
                        return
                    if sequence > self.saved_sequence:
                        self.saved_sequence = sequence
                        self.last_saved_state = copy.deepcopy(snapshot)
                        self.last_saved_context = copy.deepcopy(snapshot["messages"])
                        if not self.persistence_failed:
                            self.state.clear()
                            self.state.update(copy.deepcopy(snapshot))

                task.add_done_callback(settled)
                try:
                    done, _ = await asyncio.wait((task,), timeout=PERSIST_TIMEOUT)
                except asyncio.CancelledError:
                    task.cancel()
                    raise
                if not done:
                    # Cancellation does not prove that storage rejected the
                    # write. Stop this call without overlapping another attempt.
                    self.persistence_uncertain = True
                    task.cancel()
                    self._persistence_failed("write_timeout")
                    raise ContextPersistenceError("Conversation storage did not acknowledge the write")
                try:
                    task.result()
                except (Exception, asyncio.CancelledError):
                    if attempt + 1 < PERSIST_ATTEMPTS:
                        await asyncio.sleep(PERSIST_RETRY_DELAY * (attempt + 1))
                        continue
                    self._persistence_failed("write_failed")
                    raise ContextPersistenceError("Conversation history could not be saved") from None
                return

    async def start(self):
        self.run_task = asyncio.create_task(self.worker.run(WorkerParams(task_manager=self.manager)))
        try:
            await asyncio.wait_for(self.started.wait(), 10)
            await self.provider.start()
            await self.persist()
            self.measure("startup", {"startup_ms": round((time.monotonic()-self.began)*1000, 3)})
            history = self.context.get_messages()[1:]
            await self.send({"type": "reset", "generation": self.generation,
                             "history": history,
                             "message": "This saved call used an older history format. Starting with a fresh conversation." if self.history_reset else
                                        "Reconnected. Your conversation history is restored." if history else
                                        "Connected. Say hello to start."})
            await self.send({"type": "ready", "generation": self.generation})
        except BaseException:
            await self.close("startup_failed")
            raise

    async def _queue_turn_frame(self, frame):
        from pipecat.frames.frames import VADUserStartedSpeakingFrame
        if isinstance(frame, VADUserStartedSpeakingFrame):
            self.last_interrupt = time.monotonic()
        await self.worker.queue_frame(frame)

    async def provider_event(self, event):
        if self.closed:
            return
        if event.get("type") == "ProviderError":
            await self.turn.abort("provider_error", notify=False)
            terminal = not event.get("recoverable", False)
            await self.send({"type": "error", "message": event.get("message", "Speech connection failed") +
                             (" Speech recognition stopped; start a new call." if terminal else
                              " Audio was lost; repeat the last utterance after reconnection.")})
            self.measure("provider_error", {"provider": event.get("provider"), "recoverable": event.get("recoverable")})
            if terminal:
                if self.on_fatal:
                    await self.on_fatal()
                else:
                    await self.close("terminal_provider_failure")
            else:
                await self.interrupt()
            return
        await self.turn.event(event)

    async def audio(self, pcm):
        if self.closed:
            return
        if len(pcm) > 16000 or len(pcm) % 2:
            raise ValueError("Expected at most 500 ms of PCM16 at 16 kHz")
        self.last_activity = time.monotonic()
        self.input_audio_chunks += 1
        self.input_audio_bytes += len(pcm)
        if await self.provider.send_audio(pcm) is True:
            self.forwarded_audio_bytes += len(pcm)
            self.turn.append_audio(pcm)

    async def interrupt(self):
        if self.closed:
            return
        self.last_interrupt = time.monotonic()
        self.context_ready.clear()
        await self.worker.queue_frame(InterruptionFrame())

    def is_current(self, generation):
        return not self.closed and generation == self.generation

    @property
    def pending(self):
        return getattr(self.transport, "pending", {})

    @property
    def unacked_bytes(self):
        return getattr(self.transport, "unacked_bytes", 0)

    @property
    def next_chunk(self):
        return getattr(self.transport, "next_chunk", 0)

    async def wait_for_context(self):
        await asyncio.wait_for(self.context_ready.wait(), 5)

    async def invalidate(self, reason):
        self.generation += 1
        self.queued_output_bytes = 0
        self.output_credit.set()
        await self.send({"type": "clear", "generation": self.generation, "reason": reason})
        delay = None if self.last_interrupt is None else (time.monotonic() - self.last_interrupt) * 1000
        self.measure("server_clear", {"dispatch_ms": None if delay is None else round(delay, 3), "generation": self.generation})
        await self.transport.clear(self.generation)
        await self.persist()

    async def fail_response(self, stage, exc):
        if self.closed:
            return
        self.measure("response_failed", {"stage": stage, "exception_type": type(exc).__name__})
        await self.send({"type": "error", "message": f"Response failed during {stage}; please try again."})
        await self.invalidate("response_error")
        self.context_ready.clear()
        await self.worker.queue_frame(InterruptionFrame())

    async def reserve_output(self, size, generation):
        while self.queued_output_bytes + size > MAX_QUEUED_AUDIO_BYTES:
            if not self.is_current(generation):
                raise asyncio.CancelledError()
            self.output_credit.clear()
            await asyncio.wait_for(self.output_credit.wait(), 12)
        if not self.is_current(generation):
            raise asyncio.CancelledError()
        self.queued_output_bytes += size

    def release_output(self, size, generation):
        if self.is_current(generation):
            self.queued_output_bytes = max(0, self.queued_output_bytes - size)
            self.output_credit.set()

    def note_first_audio(self, generation):
        event = "first_audio_submitted" if self.transport_kind == "webrtc" else "first_audio"
        if not any(m["event"] == event and m.get("generation") == generation for m in self.metrics):
            self.measure(event, {"generation": generation,
                "response_ms": round((time.monotonic() - self.response_started) * 1000, 3)})

    async def played(self, generation, chunk):
        if self.transport_kind == "websocket":
            await self.transport.played(generation, chunk)

    def diagnostics(self):
        return {"generation": self.generation, "closed": self.closed,
                "pipecat_tasks": len([t for t in self.manager.current_tasks() if not t.done()]),
                **self.turn.diagnostics(),
                "input_audio_chunks": self.input_audio_chunks,
                "input_audio_bytes": self.input_audio_bytes,
                "forwarded_audio_bytes": self.forwarded_audio_bytes,
                "unacked_audio_bytes": self.unacked_bytes, "pending_playback_chunks": len(self.pending),
                "messages": len(self.context.get_messages()), "metrics": self.metrics,
                "transport": self.transport_kind, "queued_output_bytes": self.queued_output_bytes,
                "context_schema": CONTEXT_SCHEMA, "history_reset": self.history_reset,
                "context_persistence_failed": self.persistence_failed,
                "context_persistence_uncertain": self.persistence_uncertain,
                "context_persistence_attempts": self.persistence_attempts,
                "context_pending_writes": sum(not task.done() for task in self.persistence_tasks),
                "context_saved_messages": len(self.last_saved_context),
                "context_fatal_close_tasks": int(bool(self.fatal_close_task and not self.fatal_close_task.done())),
                **self.provider.diagnostics(),
                **self.transport.diagnostics()}

    async def close(self, reason="ended"):
        if self.close_task is None:
            self.close_task = asyncio.create_task(self._close_resources(reason))
            self.close_task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        if asyncio.current_task() is not self.close_task:
            await asyncio.shield(self.close_task)

    async def _close_resources(self, reason):
        self.closed = True
        for stage, action in (("turn", self.turn.close),
                              ("pipeline", lambda: self.worker.cancel(reason=reason))):
            try:
                await action()
            except Exception:
                self.measure("close_failed", {"stage": stage})
        if self.run_task:
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError, Exception):
                await asyncio.wait_for(asyncio.shield(self.run_task), 5)
        for stage, action in (("provider", self.provider.close), ("transport", self.transport.close)):
            try:
                await action()
            except Exception:
                self.measure("close_failed", {"stage": stage})
        self.responding = False
        self.queued_output_bytes = 0
        self.output_credit.set()
        self.context_ready.set()
        self.measure("closed", {"reason": reason})
        if not self.persistence_failed:
            with contextlib.suppress(ContextPersistenceError):
                await self.persist()
        if self.persistence_failed:
            # CancelFrame can flush aggregator text while the pipeline stops.
            # It cannot turn a rejected write into committed in-memory context.
            self.context.set_messages(copy.deepcopy(self.last_saved_context))
            self.state.clear()
            self.state.update(copy.deepcopy(self.last_saved_state))
