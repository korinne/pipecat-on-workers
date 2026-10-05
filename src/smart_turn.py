"""Bounded Nova/hosted Smart Turn coordination; no model or inference threads.

Nova transcript ranges are coverage evidence, not a trailing-silence clock.
Snapshots end at the observed final word boundary, without endpointing silence.
"""
import asyncio
import math
from dataclasses import dataclass
from datetime import datetime, timezone

from pipecat.audio.turn.base_turn_analyzer import BaseTurnAnalyzer, BaseTurnParams, EndOfTurnState
from pipecat.frames.frames import SystemFrame, TranscriptionFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame
from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregator
from pipecat.turns.user_stop.base_user_turn_stop_strategy import BaseUserTurnStopStrategy

RATE = 16000
MAX_AUDIO = RATE * 2 * 8
MAX_BUFFER = MAX_AUDIO + RATE * 2 // 5
MAX_FRAGMENTS = 16
MAX_CHARS = 8192


@dataclass
class ReadyTurnFrame(SystemFrame):
    revision: int
    text: str


@dataclass
class AbortTurnFrame(SystemFrame):
    pass


@dataclass
class CommitTurnFrame(SystemFrame):
    revision: int
    text: str


class HostedSmartTurnAnalyzer(BaseTurnAnalyzer):
    """A single immutable snapshot through Pipecat's async analyzer interface."""
    def __init__(self, provider, pcm):
        super().__init__(sample_rate=RATE)
        self.set_sample_rate(RATE)
        self.provider = provider
        self.audio = bytes(pcm)
        self.probability = None

    @property
    def speech_triggered(self):
        return bool(self.audio)

    @property
    def params(self):
        return BaseTurnParams()

    def append_audio(self, buffer, is_speech):
        self.audio = (self.audio + buffer)[-MAX_AUDIO:]
        return EndOfTurnState.INCOMPLETE

    async def analyze_end_of_turn(self):
        result = await self.provider.analyze_turn(self.audio)
        if not isinstance(result, dict) or type(result.get("is_complete")) is not bool:
            raise ValueError("Invalid turn decision")
        self.probability = result.get("probability")
        return (EndOfTurnState.COMPLETE if result["is_complete"] else EndOfTurnState.INCOMPLETE), None

    def clear(self):
        self.audio = b""


class HostedTurnStopStrategy(BaseUserTurnStopStrategy):
    def __init__(self, coordinator):
        super().__init__()
        self.coordinator = coordinator
        self.aggregator = None
        self.finalizing = False

    async def process_frame(self, frame):
        if isinstance(frame, CommitTurnFrame):
            # Final guard occurs after normal transcription handling, immediately
            # before Pipecat's inference signal (which precedes its stop signal).
            if self.coordinator.claim(frame.revision):
                self.finalizing = True
                try:
                    await self.trigger_user_turn_stopped()
                finally:
                    self.finalizing = False
                await self.coordinator.committed(frame.text)
            else:
                await self.aggregator.reset()
        elif isinstance(frame, AbortTurnFrame):
            await self.aggregator.reset()
            self.finalizing = True
            try:
                await self.trigger_user_turn_finalized()
            finally:
                self.finalizing = False

    async def handle_user_turn_stopped(self):
        # Also runs when Pipecat's watchdog closes an empty turn.
        if self.coordinator.active and not self.finalizing:
            await self.coordinator.abort("pipecat_turn_closed")


class HostedUserAggregator(LLMUserAggregator):
    """Serialize a guarded commit with VAD system frames; use normal aggregation."""
    def __init__(self, *args, coordinator, **kwargs):
        super().__init__(*args, **kwargs)
        self.coordinator = coordinator

    async def process_frame(self, frame, direction):
        if isinstance(frame, ReadyTurnFrame):
            if not self.coordinator.ready(frame.revision):
                return
            await super().process_frame(TranscriptionFrame(
                frame.text, "user", datetime.now(timezone.utc).isoformat()), direction)
            await super().process_frame(CommitTurnFrame(frame.revision, frame.text), direction)
        elif isinstance(frame, AbortTurnFrame):
            await super().process_frame(VADUserStoppedSpeakingFrame(), direction)
            await super().process_frame(frame, direction)
        else:
            await super().process_frame(frame, direction)


class NovaTurnCoordinator:
    def __init__(self, provider, queue, send, measure, *, timeout=5):
        self.provider, self.queue, self.send, self.measure = provider, queue, send, measure
        self.timeout = timeout
        self.connection = None
        self.revision = 0
        self.active = False
        self.speaking = False
        self.closed = False
        self.audio = bytearray()
        self.cursor = 0
        self.consumed = 0
        self.start = 0
        self.latest_onset = -1
        self.pause_end = None
        self.segments = {}
        self.word_ends = {}
        self.decision = None
        self.queued = False
        self.tasks = set()
        self.detector = None
        self.detectors = set()
        self.deadline = None

    def _task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def _cancel_pending(self):
        for task in (self.detector, self.deadline):
            if task and task is not asyncio.current_task() and not task.done():
                task.cancel()
        self.detector = self.deadline = None

    def discard(self, reason):
        self.revision += 1  # before any await or cancellation callback
        self._cancel_pending()
        self.active = self.speaking = self.queued = False
        self.segments.clear()
        self.word_ends.clear()
        self.pause_end = self.decision = None
        self.consumed = self.cursor
        self.measure("turn_discarded", {"reason": reason})

    async def abort(self, reason, *, notify=True):
        was_active = self.active
        self.discard(reason)
        if was_active and not self.closed:
            await self.queue(AbortTurnFrame())
            if notify:
                await self.send({"type": "error", "message": "Speech turn could not be completed; please repeat your request."})

    async def connected(self, generation):
        if type(generation) is not int or generation < 1:
            return
        if self.connection is not None and generation <= self.connection:
            return
        await self.abort("stt_connection_changed", notify=False)
        self.connection = generation
        self.cursor = self.consumed = 0
        self.latest_onset = -1
        self.audio.clear()

    def append_audio(self, pcm):
        self.cursor += len(pcm) // 2
        self.audio.extend(pcm)
        if len(self.audio) > MAX_BUFFER:
            del self.audio[:-MAX_BUFFER]

    @staticmethod
    def _sample(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError("Invalid Nova timestamp")
        return round(value * RATE)

    async def event(self, event):
        if self.closed:
            return
        if event.get("type") == "ProviderStatus" and event.get("status") == "connected":
            await self.connected(event.get("connection_generation"))
            return
        if event.get("connection_generation") != self.connection or self.connection is None:
            return
        try:
            if event.get("type") == "SpeechStarted":
                await self._onset(self._sample(event.get("timestamp")))
            elif event.get("type") == "Results":
                await self._results(event)
        except (ValueError, TypeError, KeyError, IndexError, OverflowError):
            await self.abort("invalid_nova_event")

    async def _onset(self, timestamp):
        if timestamp > self.cursor:
            raise ValueError("Speech onset beyond sent audio")
        if timestamp < self.consumed or timestamp <= self.latest_onset:
            return
        self.latest_onset = timestamp
        self.revision += 1
        self._cancel_pending()
        if not self.active:
            self.segments.clear()
            self.word_ends.clear()
            self.start = timestamp
        self.active = self.speaking = True
        self.pause_end = self.decision = None
        self.queued = False
        await self.queue(VADUserStartedSpeakingFrame())
        self.measure("speech_started", {"revision": self.revision})

    async def _results(self, event):
        if not self.active:
            return
        start = self._sample(event["start"])
        duration = self._sample(event["duration"])
        end = start + duration
        if end <= self.consumed:
            return
        if start < self.consumed:
            raise ValueError("Transcript crosses committed boundary")
        if end > self.cursor:
            raise ValueError("Transcript beyond sent audio")
        if self.pause_end is not None and end > self.pause_end:
            await self.abort("transcript_beyond_pause")
            return
        if type(event.get("is_final")) is not bool or type(event.get("speech_final")) is not bool:
            raise ValueError("Missing Nova flags")
        alternative = event["channel"]["alternatives"][0]
        text = alternative["transcript"]
        if not isinstance(text, str) or len(text) > MAX_CHARS:
            raise ValueError("Invalid transcript")
        text = text.strip()
        words = alternative.get("words", [])
        word_end = None
        if words:
            if not isinstance(words, list):
                raise ValueError("Invalid Nova words")
            previous = start
            for word in words:
                left, right = self._sample(word["start"]), self._sample(word["end"])
                if left < start or right < left or right > end or left < previous:
                    raise ValueError("Invalid Nova word timing")
                previous = right
            word_end = previous
        if text and word_end is None:
            raise ValueError("Missing Nova word timing")
        if event["is_final"]:
            key = (start, end)
            old = self.segments.get(key)
            if old is not None and old != text:
                raise ValueError("Conflicting finalized transcript")
            if old is None:
                if any(start < b and end > a for a, b in self.segments):
                    raise ValueError("Overlapping finalized transcript")
                self.segments[key] = text
                if word_end is not None:
                    self.word_ends[key] = word_end
            if len(self.segments) > MAX_FRAGMENTS or len(self.text()) > MAX_CHARS:
                raise ValueError("Turn text limit")
        if text:
            await self.send({"type": "transcript", "role": "user", "text":
                self.text() if event["is_final"] else (self.text() + " " + text).strip()[:MAX_CHARS], "final": False})
        if event["speech_final"] and end > self.latest_onset and self.pause_end is None:
            self.speaking = False
            self.pause_end = end
            revision = self.revision
            self.deadline = self._task(self._expire(revision))
            await self.queue(VADUserStoppedSpeakingFrame())
            # Transcript end bounds the snapshot; it is NOT a processed-audio
            # or trailing-silence watermark. Do not include later queued speech.
            # Nova's result range includes endpointing silence. Hosted Smart
            # Turn falsely completed the recorded incomplete clause when that
            # silence was included. Use its observed final word boundary.
            right = word_end or max(self.word_ends.values(), default=0)
            buffer_start = self.cursor - len(self.audio) // 2
            left = max(self.start - RATE // 5, right - RATE * 8, buffer_start, 0)
            if end > self.cursor or right <= left:
                await self.abort("pause_audio_unavailable")
                return
            pcm = bytes(self.audio[(left - buffer_start) * 2:(right - buffer_start) * 2])
            if any(not task.done() for task in self.detectors):
                await self.abort("detector_busy")
                return
            self.detector = self._task(self._analyze(revision, pcm))
            self.detectors.add(self.detector)
            self.detector.add_done_callback(self.detectors.discard)
            self.measure("turn_pause", {"revision": revision, "snapshot_samples": len(pcm)//2,
                                        "transcript_end_sample": end, "audio_end_sample": right})
        await self._maybe_ready()

    def text(self):
        return " ".join(text for _, text in sorted(self.segments.items()) if text)

    def _covered(self):
        if self.pause_end is None:
            return False
        cursor = self.start
        for (start, end), _ in sorted(self.segments.items()):
            if start > cursor:
                return False
            cursor = max(cursor, end)
            if cursor >= self.pause_end:
                return True
        return False

    async def _analyze(self, revision, pcm):
        analyzer = HostedSmartTurnAnalyzer(self.provider, pcm)
        try:
            decision, _ = await analyzer.analyze_end_of_turn()
            if revision == self.revision and self.active and not self.speaking:
                self.decision = decision
                self.measure("smart_turn", {"revision": revision, "complete": decision == EndOfTurnState.COMPLETE,
                                            "probability": analyzer.probability})
                await self._maybe_ready()
        except asyncio.CancelledError:
            raise
        except Exception:
            if revision == self.revision:
                await self.abort("smart_turn_failed")
        finally:
            analyzer.clear()

    async def _expire(self, revision):
        await asyncio.sleep(self.timeout)
        if revision == self.revision and self.active:
            await self.abort("turn_readiness_timeout")

    def ready(self, revision):
        return (revision == self.revision and self.active and not self.speaking and
                self.decision == EndOfTurnState.COMPLETE and self._covered() and bool(self.text()))

    async def _maybe_ready(self):
        if not self.queued and self.ready(self.revision):
            self.queued = True
            await self.queue(ReadyTurnFrame(self.revision, self.text()))

    def claim(self, revision):
        if not self.ready(revision):
            return False
        self.consumed = self.pause_end
        self.active = self.speaking = False
        self.segments.clear()
        self.word_ends.clear()
        self._cancel_pending()
        self.measure("user_end", {"revision": revision})
        return True

    async def committed(self, text):
        await self.send({"type": "transcript", "role": "user", "text": text, "final": True})

    def diagnostics(self):
        return {"pending_turn_tasks": sum(not t.done() for t in self.tasks),
                "pending_user_fragments": len(self.segments), "pending_user_chars": len(self.text()),
                "turn_audio_bytes": len(self.audio), "turn_revision": self.revision,
                "turn_timeout_secs": self.timeout}

    async def close(self):
        self.closed = True
        self.discard("closed")
        tasks = [t for t in self.tasks if t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.audio.clear()
