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

SYSTEM = ('You are a concise, friendly voice assistant. Reply in one or two short sentences, '
          'without markdown. Remember the conversation. Appointment availability is fictional '
          'and read-only. Never claim a booking was made. If a tool result is provided, use it. '
          'Assistant history includes only fully played sentences; an interrupted sentence may be absent.')
MAX_UNACKED_BYTES = 384000  # eight seconds of 24 kHz mono PCM16
MAX_PENDING_RECEIPTS = 256  # also bounds already-played pieces of an unfinished sentence
MAX_PENDING_USER_FRAGMENTS = 16
MAX_PENDING_USER_CHARS = 8192


async def lookup_availability():
    """Harmless async example tool; deliberate delay makes cancellation testable."""
    await asyncio.sleep(0.8)
    return {"fictional": True, "appointments": ["Tuesday at 10 AM", "Thursday at 2 PM"], "booked": False}


class GenerateResponse(FrameProcessor):
    def __init__(self, session):
        super().__init__()
        self.session = session

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        # Pipecat cancels its in-flight data-frame processing task here when an
        # InterruptionFrame arrives. No detached generation task bypasses it.
        await super().process_frame(frame, direction)
        if isinstance(frame, InterruptionFrame):
            await self.session.invalidate("pipecat_interruption")
        elif isinstance(frame, LLMContextFrame) and direction == FrameDirection.DOWNSTREAM:
            try:
                await self.session.respond()
            except asyncio.CancelledError:
                self.session.measure("generation_cancelled", {})
                raise
            except Exception as exc:
                self.session.measure("error", {"message": str(exc)})
                await self.session.send({"type": "error", "message": f"Response failed: {exc}"})
                await self.session.invalidate("response_error")
            return
        await self.push_frame(frame, direction)


class ConversationSession:
    def __init__(self, state, provider_factory, send, save, *, tool=lookup_availability,
                 on_fatal=None, turn_timeout_secs=5, user_turn_stop_timeout=30, audio_transport=None):
        self.state = state
        self.send = send
        self.save = save
        self.tool = tool
        self.on_fatal = on_fatal
        self.audio_transport = audio_transport
        system = SYSTEM if audio_transport is None else SYSTEM.replace(
            'Assistant history includes only fully played sentences; an interrupted sentence may be absent.',
            'Audio playback cannot be confirmed for this call. Prior assistant replies are omitted from history; '
            'do not assume the user heard any prior reply.')
        self.context = LLMContext(copy.deepcopy(state.get("messages") or [{"role": "system", "content": system}]))
        self.generation = int(state.get("generation", 0)) + 1
        self.next_chunk = 0
        self.pending = {}
        self.unacked_bytes = 0
        self.input_audio_chunks = 0
        self.input_audio_bytes = 0
        self.forwarded_audio_bytes = 0
        self.credit = asyncio.Event()
        self.credit.set()
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
        self.processor = GenerateResponse(self)
        self.worker = PipelineWorker(Pipeline([self.user, self.processor]),
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
        self.sentence_parts = []
        self.last_assistant_message = None

        @self.worker.event_handler("on_pipeline_started")
        async def on_started(worker, frame):
            self.started.set()

    def measure(self, kind, values):
        self.metrics.append({"event": kind, "elapsed_ms": round((time.monotonic() - self.began) * 1000, 3), **values})
        self.metrics = self.metrics[-500:]

    async def persist(self):
        messages = self.context.get_messages()
        # Explicit finite memory budget, preserving the system instruction.
        if len(messages) > 81:
            self.context.set_messages([messages[0]] + messages[-80:])
        self.state.update(messages=copy.deepcopy(self.context.get_messages()), generation=self.generation,
                          updated_at=time.time(), status="ended" if self.closed else "active")
        await self.save(copy.deepcopy(self.state))

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
                             "message": "Reconnected. Your conversation history is restored." if history else
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
        await self.worker.queue_frame(InterruptionFrame())

    async def invalidate(self, reason):
        self.generation += 1
        self.pending.clear()
        self.unacked_bytes = 0
        self.sentence_parts = []
        self.credit.set()
        await self.send({"type": "clear", "generation": self.generation, "reason": reason})
        if self.audio_transport:
            await self.audio_transport.clear(self.generation)
        delay = None if self.last_interrupt is None else (time.monotonic() - self.last_interrupt) * 1000
        self.measure("server_clear", {"dispatch_ms": None if delay is None else round(delay, 3), "generation": self.generation})
        await self.persist()

    async def respond(self):
        self.generation += 1
        generation = self.generation
        self.last_assistant_message = None
        await self.persist()
        messages = copy.deepcopy(self.context.get_messages())
        user_text = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        self.responding = True
        started = time.monotonic()
        self.measure("generation_started", {"generation": generation})
        try:
            # Deliberately narrow application tool dispatch, not generic model tool calling.
            if re.search(r"\b(appointments?|availability|available times|free slots)\b", user_text, re.I):
                await self.send({"type": "status", "state": "tool", "generation": generation})
                result = await self.tool()
                self.measure("tool_completed", {"name": "lookup_availability", "fictional": True})
                messages.append({"role": "system", "content": f"lookup_availability returned: {result}"})
            await self.send({"type": "status", "state": "thinking", "generation": generation})
            text = ""
            stream = self.provider.generate(messages)
            try:
                async for token in stream:
                    if self.closed or generation != self.generation:
                        return
                    text += token
                    # Sentence-sized TTS flushes preserve streaming and a conservative
                    # played-text boundary without assuming word timing alignment.
                    while True:
                        boundary = re.search(r"[.!?](?:\s|$)", text)
                        if not boundary and len(text) < 180:
                            break
                        end = boundary.end() if boundary else text.rfind(" ", 0, 180) + 1
                        if end <= 0:
                            end = 180
                        sentence, text = text[:end].strip(), text[end:]
                        await self.speak(sentence, generation, started)
                if text.strip():
                    await self.speak(text.strip(), generation, started)
            finally:
                await stream.aclose()
            if self.audio_transport and not self.closed and generation == self.generation:
                await self.audio_transport.finish_generation(generation)
        finally:
            self.responding = False
            if not self.closed and generation == self.generation:
                await self.send({"type": "status", "state": "listening", "generation": generation})

    async def speak(self, text, generation, started):
        if not text:
            return
        await self.send({"type": "status", "state": "speaking", "generation": generation})
        await self.send({"type": "transcript", "role": "assistant", "text": text, "final": True})
        stream = self.provider.synthesize(text)
        previous = None
        remainder = bytearray()
        sentence_ids = []
        try:
            async for pcm in stream:
                if generation != self.generation or self.closed:
                    return
                # Coalesce small provider packets into 100 ms frames. Keep at
                # most one full frame plus a 4800-byte remainder so the final
                # frame can carry the sentence's playback receipt marker.
                offset = 0
                while offset < len(pcm):
                    size = min(4800 - len(remainder), len(pcm) - offset)
                    remainder.extend(pcm[offset:offset+size])
                    offset += size
                    if len(remainder) == 4800:
                        if previous is not None:
                            sentence_ids.append(await self.output_audio(previous, generation, "", [], started))
                        previous = bytes(remainder)
                        remainder.clear()
            if remainder:
                if previous is not None:
                    sentence_ids.append(await self.output_audio(previous, generation, "", [], started))
                previous = bytes(remainder)
            if previous:
                await self.output_audio(previous, generation, text, sentence_ids, started)
        finally:
            await stream.aclose()

    async def output_audio(self, pcm, generation, text, sentence_ids, started):
        if self.audio_transport:
            if self.closed or generation != self.generation:
                raise asyncio.CancelledError()
            if not await self.audio_transport.send_audio(pcm, generation):
                raise asyncio.CancelledError()
            self.next_chunk += 1
            # Submission is observable; it is never evidence of playback. No
            # receipt ledger or confirmed assistant history is created here.
            if not any(m["event"] == "first_audio_submitted" and m.get("generation") == generation for m in self.metrics):
                self.measure("first_audio_submitted", {"generation": generation,
                    "response_ms": round((time.monotonic()-started)*1000, 3)})
            return self.next_chunk
        if len(self.pending) >= MAX_PENDING_RECEIPTS:
            raise ValueError("Speech segment exceeded the 256-chunk receipt limit")
        while self.unacked_bytes + len(pcm) > MAX_UNACKED_BYTES:
            self.credit.clear()
            await asyncio.wait_for(self.credit.wait(), 12)
        if self.closed or generation != self.generation:
            raise asyncio.CancelledError()
        self.next_chunk += 1
        chunk = self.next_chunk
        self.pending[chunk] = {"bytes": len(pcm), "text": text, "before": sentence_ids, "played": False,
                               "generation": generation}
        self.unacked_bytes += len(pcm)
        await self.send({"type": "audio", "data": base64.b64encode(pcm).decode(), "sample_rate": 24000,
                         "generation": generation, "chunk_id": chunk, "text": text})
        if not any(m["event"] == "first_audio" and m.get("generation") == generation for m in self.metrics):
            self.measure("first_audio", {"generation": generation, "response_ms": round((time.monotonic()-started)*1000, 3)})
        return chunk

    async def played(self, generation, chunk):
        if self.audio_transport:
            return
        item = self.pending.get(chunk)
        if generation != self.generation or not item or item["played"] or item["generation"] != generation:
            return
        item["played"] = True
        self.unacked_bytes -= item["bytes"]
        self.credit.set()
        # Only commit a sentence once every constituent chunk was acknowledged.
        for last, candidate in list(self.pending.items()):
            if not candidate["text"] or not candidate["played"]:
                continue
            ids = candidate["before"] + [last]
            if not all(self.pending.get(i, {}).get("played") for i in ids):
                continue
            content = candidate["text"]
            messages = self.context.get_messages()
            if self.last_assistant_message is not None and messages and messages[-1] is self.last_assistant_message:
                self.last_assistant_message["content"] += " " + content
            else:
                self.last_assistant_message = {"role": "assistant", "content": content}
                self.context.add_message(self.last_assistant_message)
            for i in ids:
                self.pending.pop(i, None)
            await self.persist()

    def diagnostics(self):
        return {"generation": self.generation, "closed": self.closed,
                "pipecat_tasks": len([t for t in self.manager.current_tasks() if not t.done()]),
                **self.turn.diagnostics(),
                "input_audio_chunks": self.input_audio_chunks,
                "input_audio_bytes": self.input_audio_bytes,
                "forwarded_audio_bytes": self.forwarded_audio_bytes,
                "unacked_audio_bytes": self.unacked_bytes, "pending_playback_chunks": len(self.pending),
                "messages": len(self.context.get_messages()), "metrics": self.metrics,
                "transport": "webrtc" if self.audio_transport else "websocket",
                **self.provider.diagnostics(),
                **(self.audio_transport.diagnostics() if self.audio_transport else {})}

    async def close(self, reason="ended"):
        if self.closed:
            return
        self.closed = True
        await self.turn.close()
        await self.worker.cancel(reason=reason)
        if self.run_task:
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(self.run_task, 5)
        await self.provider.close()
        if self.audio_transport:
            await self.audio_transport.close()
        self.pending.clear()
        self.unacked_bytes = 0
        self.credit.set()
        self.measure("closed", {"reason": reason})
        await self.persist()
