"""Synthetic I/O evidence for the *real* vendored Pipecat conversation pipeline.

These fixtures never contact an AI provider. Silent PCM and explicit provider
events test frame ordering, cancellation, context and lifetime only. They do not
validate recognition, semantic turn detection, real speech or audible latency.
run_probe() may be called from a Python Durable Object or from CPython.
"""

import asyncio
import copy
import platform
import time
import traceback

from conversation import ConversationSession, MAX_UNACKED_BYTES


async def wait_for(predicate, description, timeout=4):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError(f"Timed out waiting for {description}")
        await asyncio.sleep(0.005)


class FixtureProviders:
    """A provider fixture with controllable pending operations and silent audio."""

    def __init__(self, on_event, *, label="fixture", hold_generation=False, pcm_chunks=3):
        self.on_event = on_event
        self.label = label
        self.hold_generation = hold_generation
        self.pcm_chunks = pcm_chunks
        self.generation_gate = asyncio.Event()
        self.generation_started = asyncio.Event()
        self.generation_cancelled = asyncio.Event()
        self.synthesis_started = asyncio.Event()
        self.synthesis_cancelled = asyncio.Event()
        self.generations = []
        self.syntheses = []
        self.live_generations = 0
        self.live_syntheses = 0
        self.closed = False
        self.audio_bytes_received = 0

    async def start(self):
        return

    async def send_audio(self, data):
        self.audio_bytes_received += len(data)

    async def generate(self, messages):
        self.generations.append(copy.deepcopy(messages))
        self.generation_started.set()
        self.live_generations += 1
        completed = False
        try:
            if self.hold_generation:
                await self.generation_gate.wait()
            user = next(m["content"] for m in reversed(messages) if m["role"] == "user")
            yield f"{self.label} reply to {user}."
            completed = True
        finally:
            self.live_generations -= 1
            if not completed:
                self.generation_cancelled.set()

    async def synthesize(self, text):
        self.syntheses.append(text)
        self.synthesis_started.set()
        self.live_syntheses += 1
        completed = False
        try:
            for _ in range(self.pcm_chunks):
                await asyncio.sleep(0)
                yield bytes(4800)  # 100 ms of digital silence, not generated speech.
            completed = True
        finally:
            self.live_syntheses -= 1
            if not completed:
                self.synthesis_cancelled.set()

    async def close(self):
        self.closed = True

    def diagnostics(self):
        return {"fixture_live_generations": self.live_generations,
                "fixture_live_syntheses": self.live_syntheses,
                "fixture_provider_closed": self.closed,
                "fixture_audio_bytes_received": self.audio_bytes_received}


class PendingTool:
    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.gate = asyncio.Event()
        self.completed = False

    async def __call__(self):
        self.started.set()
        try:
            await self.gate.wait()
            self.completed = True
            return {"fictional": True, "appointments": ["Tuesday at 10 AM"], "booked": False}
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


async def immediate_tool():
    return {"fictional": True, "appointments": ["Tuesday at 10 AM"], "booked": False}


class Harness:
    def __init__(self, *, state=None, label="fixture", hold_generation=False, pcm_chunks=3,
                 tool=immediate_tool, turn_end_grace_ms=0):
        self.events = []
        self.saved = []
        self.provider = None

        async def send(event):
            self.events.append(copy.deepcopy(event))

        async def save(value):
            self.saved.append(copy.deepcopy(value))

        def factory(callback):
            self.provider = FixtureProviders(callback, label=label,
                hold_generation=hold_generation, pcm_chunks=pcm_chunks)
            return self.provider

        self.session = ConversationSession(copy.deepcopy(state or {}), factory, send, save,
                                           tool=tool, turn_end_grace_ms=turn_end_grace_ms)

    async def start(self):
        await self.session.start()
        return self

    def audio(self, generation=None):
        return [e for e in self.events if e["type"] == "audio" and
                (generation is None or e["generation"] == generation)]

    def assistant(self):
        return [m["content"] for m in self.session.context.get_messages() if m["role"] == "assistant"]

    async def begin(self, text, index):
        old = self.session.generation
        await self.session.provider_event({"type": "TurnInfo", "event": "StartOfTurn",
            "turn_index": index, "transcript": text, "connection_generation": 1})
        await wait_for(lambda: self.session.generation > old, "Pipecat interruption from proposed start")

    async def finish(self, text, index):
        await self.session.provider_event({"type": "TurnInfo", "event": "EndOfTurn",
            "turn_index": index, "transcript": text, "connection_generation": 1})

    async def turn(self, text, index):
        await self.begin(text, index)
        await self.finish(text, index)

    async def response(self, count=1):
        await wait_for(lambda: len(self.provider.generations) >= count and self.audio() and
                       not self.session.responding, "complete fixture response")

    async def acknowledge(self, events=None):
        for event in events if events is not None else self.audio():
            await self.session.played(event["generation"], event["chunk_id"])

    async def close(self):
        await self.session.close("synthetic_probe_complete")
        state = self.session.diagnostics()
        assert state["pipecat_tasks"] == 0, state
        assert state["unacked_audio_bytes"] == 0, state
        assert state["pending_playback_chunks"] == 0, state
        assert state["fixture_live_generations"] == 0, state
        assert state["fixture_live_syntheses"] == 0, state
        assert self.provider.closed
        return state


async def receipts_and_context():
    h = await Harness().start()
    try:
        await h.turn("alpha", 0)
        await h.response()
        audio = h.audio()
        assert len(audio) == 3
        assert not h.assistant(), "Unplayed generated text entered conversation history"
        # Final chunk alone is insufficient; every preceding PCM chunk is required.
        await h.acknowledge([audio[-1], audio[0]])
        assert not h.assistant(), "Partly played sentence was committed"
        await h.acknowledge([audio[1]])
        assert h.assistant() == ["fixture reply to alpha."]
        saved = copy.deepcopy(h.saved[-1])
        await h.acknowledge(audio)
        assert h.saved[-1] == saved, "Duplicate receipts changed history"
        assert h.session.unacked_bytes == 0
        return {"audio_chunks": 3, "played_sentence_commits": 1,
                "duplicate_receipts_ignored": 3, "partial_sentence_committed": False}
    finally:
        await h.close()


async def pause_before_end_event():
    h = await Harness().start()
    try:
        await h.begin("Please explain", 0)
        await h.session.provider_event({"type": "TurnInfo", "event": "Update",
            "transcript": "Please explain", "turn_index": 0})
        await asyncio.sleep(0.2)
        assert not h.provider.generations and not h.audio()
        await h.finish("Please explain why the sky is blue", 0)
        await h.response()
        users = [m["content"] for m in h.session.context.get_messages() if m["role"] == "user"]
        assert users == ["Please explain why the sky is blue"]
        return {"synthetic_event_pause_ms": 200, "responses_before_end_event": 0,
                "semantic_audio_turn_detection_tested": False}
    finally:
        await h.close()


async def pending_llm_interruption():
    h = await Harness(hold_generation=True).start()
    try:
        await h.turn("obsolete request", 0)
        await asyncio.wait_for(h.provider.generation_started.wait(), 4)
        start = time.monotonic()
        await h.begin("replacement request", 1)
        await asyncio.wait_for(h.provider.generation_cancelled.wait(), 4)
        cancellation_ms = (time.monotonic() - start) * 1000
        assert not h.audio()
        h.provider.hold_generation = False
        h.provider.generation_gate.set()
        await h.finish("replacement request", 1)
        await h.response(2)
        assert h.provider.syntheses == ["fixture reply to replacement request."]
        await h.acknowledge()
        assert h.assistant() == ["fixture reply to replacement request."]
        return {"pending_llm_canceled": True, "stale_audio_chunks": 0,
                "fixture_cancellation_ms": round(cancellation_ms, 3)}
    finally:
        await h.close()


async def pending_tool_interruption():
    tool = PendingTool()
    h = await Harness(tool=tool).start()
    try:
        await h.turn("appointment availability", 0)
        await asyncio.wait_for(tool.started.wait(), 4)
        await h.begin("tell me something else", 1)
        await asyncio.wait_for(tool.cancelled.wait(), 4)
        assert not tool.completed
        assert not h.provider.generations
        tool.gate.set()
        await h.finish("tell me something else", 1)
        await h.response()
        assert all("lookup_availability returned" not in str(m) for m in h.provider.generations[0])
        assert not any(m["event"] == "tool_completed" for m in h.session.metrics)
        return {"pending_tool_canceled": True, "tool_completed_after_interruption": False,
                "stale_tool_result_reached_model": False}
    finally:
        await h.close()


async def repeated_playback_interruptions():
    h = await Harness().start()
    interrupted = []
    try:
        for index in range(4):
            await h.turn(f"turn {index}", index)
            await h.response(index + 1)
            current = h.audio(h.session.generation)
            assert len(current) == 3
            if index < 3:
                # One played chunk is deliberately less than the full sentence.
                await h.acknowledge([current[0]])
                interrupted.extend(current)
                assert not h.assistant()
        current_generation = h.session.generation
        bytes_before = h.session.unacked_bytes
        await h.acknowledge(interrupted)
        assert h.session.unacked_bytes == bytes_before
        assert not h.assistant(), "Old receipt resurrected interrupted speech"
        await h.acknowledge(h.audio(current_generation))
        assert h.assistant() == ["fixture reply to turn 3."]
        return {"playback_interruptions": 3, "stale_receipts_ignored": len(interrupted),
                "partial_sentences_committed": 0, "audible_stop_measured": False}
    finally:
        await h.close()


async def simultaneous_sessions():
    left, right = await asyncio.gather(Harness(label="left").start(), Harness(label="right").start())
    try:
        await asyncio.gather(left.turn("left-secret", 0), right.turn("right-secret", 0))
        await asyncio.gather(left.response(), right.response())
        await asyncio.gather(left.acknowledge(), right.acknowledge())
        assert "right-secret" not in str(left.session.context.get_messages())
        assert "left-secret" not in str(right.session.context.get_messages())
        assert left.assistant() == ["left reply to left-secret."]
        assert right.assistant() == ["right reply to right-secret."]
        assert left.provider is not right.provider and left.session.context is not right.session.context
        return {"simultaneous_sessions": 2, "cross_session_leaks": 0}
    finally:
        await asyncio.gather(left.close(), right.close())


async def reconstruct_persisted_state():
    old = await Harness().start()
    new = None
    try:
        await old.turn("remember violet", 0)
        await old.response()
        await old.acknowledge()
        persisted = copy.deepcopy(old.saved[-1])
        old_generation = old.session.generation
        await old.close()
        new = await Harness(state=persisted).start()
        assert new.session.generation > old_generation
        assert new.session.context.get_messages() == persisted["messages"]
        assert new.session.pending == {} and new.session.unacked_bytes == 0
        assert new.provider is not old.provider
        reset = next(e for e in new.events if e["type"] == "reset")
        assert reset["history"] == persisted["messages"][1:]
        await new.turn("what should you remember", 1)
        await new.response()
        assert "remember violet" in str(new.provider.generations[0])
        return {"persisted_messages_reconstructed": len(persisted["messages"]),
                "live_audio_restored": False, "new_provider_instance": True,
                "actual_process_restart_tested": False}
    finally:
        await old.close()
        if new:
            await new.close()


async def backpressure_and_cleanup():
    h = await Harness(pcm_chunks=1000).start()
    try:
        await h.turn("long reply", 0)
        await wait_for(lambda: h.session.unacked_bytes == MAX_UNACKED_BYTES,
                       "bounded playback buffer filling")
        assert h.provider.live_syntheses == 1
        before = len(h.audio())
        await asyncio.sleep(0.03)
        assert len(h.audio()) == before, "Producer ignored playback backpressure"
        await h.session.interrupt()
        await asyncio.wait_for(h.provider.synthesis_cancelled.wait(), 4)
        await wait_for(lambda: h.session.unacked_bytes == 0, "interruption clearing playback buffer")
        assert h.session.pending == {}
        return {"max_unacked_pcm_bytes": MAX_UNACKED_BYTES, "audio_chunks_before_backpressure": before,
                "blocked_synthesis_canceled": True}
    finally:
        await h.close()


async def harmless_tool_integration():
    h = await Harness().start()
    try:
        await h.turn("appointment availability", 0)
        await h.response()
        request = h.provider.generations[0]
        result = next(m["content"] for m in request if "lookup_availability returned" in m["content"])
        assert "Tuesday at 10 AM" in result and "'booked': False" in result
        assert any(m["event"] == "tool_completed" for m in h.session.metrics)
        return {"fixture_tool_result_reached_model": True, "booking_created": False,
                "dispatch": "explicit application rule, not model-selected function calling"}
    finally:
        await h.close()


async def abandon_pending_session():
    h = await Harness(hold_generation=True).start()
    await h.turn("will be abandoned", 0)
    await asyncio.wait_for(h.provider.generation_started.wait(), 4)
    state = await h.close()
    assert h.provider.generation_cancelled.is_set()
    assert not h.audio()
    return {"pipeline_tasks_after_close": state["pipecat_tasks"],
            "pending_generation_canceled": True, "live_fixture_operations_after_close": 0}


PROBE_CASES = {
    "receipts_and_context": receipts_and_context,
    "pause_before_end_event": pause_before_end_event,
    "pending_llm_interruption": pending_llm_interruption,
    "pending_tool_interruption": pending_tool_interruption,
    "repeated_playback_interruptions": repeated_playback_interruptions,
    "simultaneous_sessions": simultaneous_sessions,
    "reconstruct_persisted_state": reconstruct_persisted_state,
    "backpressure_and_cleanup": backpressure_and_cleanup,
    "harmless_tool_integration": harmless_tool_integration,
    "abandon_pending_session": abandon_pending_session,
}


async def run_probe(cases=None):
    """Return honest machine-readable results using actual Pipecat frame tasks."""
    started = time.monotonic()
    results = []
    for name in cases if cases is not None else PROBE_CASES:
        began = time.monotonic()
        try:
            evidence = await PROBE_CASES[name]()
            results.append({"name": name, "passed": True, "evidence": evidence,
                            "duration_ms": round((time.monotonic() - began) * 1000, 3)})
        except Exception as exc:
            results.append({"name": name, "passed": False, "error": str(exc),
                            "traceback": traceback.format_exc(limit=8),
                            "duration_ms": round((time.monotonic() - began) * 1000, 3)})
    return {"passed": all(r["passed"] for r in results), "test_kind": "real_pipecat_synthetic_io",
            "python": platform.python_version(), "runtime_platform": platform.system(),
            "actual_voice_conversation": False, "provider_network_tested": False,
            "audible_interruption_tested": False, "semantic_audio_turn_detection_tested": False,
            "test_count": len(results), "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "results": results}


async def run_soak(duration_seconds=600, turn_interval_seconds=5, sessions=4,
                   progress=None, memory_sample=None):
    """Measured wall-clock longevity with four isolated real Pipecat pipelines.

    Input is silent PCM; turn events and playback receipts are injected. No real
    provider, audio detector, speaker or microphone participates in this test.
    """
    if not 1 <= duration_seconds <= 900 or not 1 <= sessions <= 8:
        raise ValueError("Use 1–900 real seconds and 1–8 concurrent sessions")
    harnesses = await asyncio.gather(*(Harness(label=f"soak-{i}").start() for i in range(sessions)))
    started = time.monotonic()
    turns = [0] * sessions
    next_turns = [started] * sessions
    audio_frames = [0] * sessions
    interruptions = 0
    stale_acks = 0
    peak_unacked = 0
    peak_pipeline_tasks = 0
    errors = []
    startup_ms = [next(m["startup_ms"] for m in h.session.metrics if m["event"] == "startup") for h in harnesses]
    first_audio_ms = []
    clear_ms = []
    samples = []
    next_sample = started

    def summary(values):
        if not values:
            return {"samples": 0}
        ordered = sorted(values)
        return {"samples": len(values), "min_ms": round(ordered[0], 3),
                "median_ms": round(ordered[len(ordered)//2], 3),
                "p95_ms": round(ordered[min(len(ordered)-1, int(len(ordered)*.95))], 3),
                "max_ms": round(ordered[-1], 3)}

    async def advance(i):
        nonlocal interruptions, stale_acks, peak_unacked
        h = harnesses[i]
        old_audio = h.audio(h.session.generation)
        pending = bool(h.session.pending)
        expected_generations = len(h.provider.generations) + 1
        await h.turn(f"session-{i} token-{turns[i]}", turns[i])
        if pending:
            interruptions += 1
            before = h.session.unacked_bytes
            await h.acknowledge(old_audio)
            stale_acks += len(old_audio)
            assert h.session.unacked_bytes == before, "Stale receipt changed new audio budget"
        await h.response(expected_generations)
        current = h.audio(h.session.generation)
        peak_unacked = max(peak_unacked, h.session.unacked_bytes)
        # Two in three replies are only partly acknowledged and are then
        # interrupted by the next turn. The third commits one full sentence.
        await h.acknowledge(current if turns[i] % 3 == 2 else current[:1])
        turns[i] += 1
        next_turns[i] = time.monotonic() + turn_interval_seconds
        for m in h.session.metrics:
            if m["event"] == "error":
                errors.append(m)
            elif m["event"] == "first_audio":
                first_audio_ms.append(m["response_ms"])
            elif m["event"] == "server_clear" and m.get("dispatch_ms") is not None:
                clear_ms.append(m["dispatch_ms"])
        messages = h.session.context.get_messages()
        for other in range(sessions):
            if other != i:
                assert f"session-{other} token-" not in str(messages), "Cross-session context leak"
        # Bound fixture records separately from application state. Keep only the
        # latest audio's metadata for stale-receipt injection at the next turn.
        h.events[:] = current
        h.saved[:] = h.saved[-1:]
        h.provider.generations[:] = h.provider.generations[-1:]
        h.provider.syntheses[:] = h.provider.syntheses[-1:]
        h.session.metrics.clear()

    async def sample():
        nonlocal peak_pipeline_tasks
        current = [h.session.diagnostics() for h in harnesses]
        task_count = sum(d["pipecat_tasks"] for d in current)
        peak_pipeline_tasks = max(peak_pipeline_tasks, task_count)
        item = {"elapsed_seconds": round(time.monotonic() - started, 3),
                "turns": sum(turns), "pipeline_tasks": task_count,
                "unacked_pcm_bytes": sum(d["unacked_audio_bytes"] for d in current),
                "context_messages": [d["messages"] for d in current],
                "playback_interruptions": interruptions, "errors": len(errors)}
        if memory_sample is not None:
            item.update(memory_sample())
        samples.append(item)
        if progress is not None:
            await progress(item)

    try:
        while time.monotonic() - started < duration_seconds:
            await asyncio.gather(*(h.session.audio(bytes(2560)) for h in harnesses))
            for i in range(sessions):
                audio_frames[i] += 1
            due = [i for i in range(sessions) if time.monotonic() >= next_turns[i]]
            if due:
                await asyncio.gather(*(advance(i) for i in due))
            if time.monotonic() >= next_sample:
                await sample()
                next_sample = time.monotonic() + 60
            await asyncio.sleep(min(.08, max(0, duration_seconds - (time.monotonic()-started))))
        duration = time.monotonic() - started
        await sample()
        assert sum(turns) >= sessions
        assert not errors, errors
        live = [h.session.diagnostics() for h in harnesses]
    finally:
        cleaned = await asyncio.gather(*(h.close() for h in harnesses))
    return {"passed": True, "test_kind": "real_pipecat_synthetic_io_soak",
            "requested_seconds": duration_seconds, "actual_wall_seconds": round(duration, 3),
            "ten_minutes_elapsed": duration >= 600, "simultaneous_sessions": sessions,
            "turns": turns, "input_audio_frames": audio_frames,
            "input_pcm_bytes": [h.provider.audio_bytes_received for h in harnesses],
            "audio_description": "silent PCM16 fixture, 16 kHz mono; injected turn events and receipts",
            "playback_interruptions": interruptions, "stale_receipts_ignored": stale_acks,
            "cross_session_leaks": 0, "peak_unacked_pcm_bytes_per_session": peak_unacked,
            "peak_pipecat_tasks_all_sessions": peak_pipeline_tasks,
            "pipecat_tasks_before_close": [d["pipecat_tasks"] for d in live],
            "pipecat_tasks_after_close": [d["pipecat_tasks"] for d in cleaned],
            "pending_playback_after_close": [d["pending_playback_chunks"] for d in cleaned],
            "session_startup": summary(startup_ms), "fixture_response_to_first_audio": summary(first_audio_ms),
            "server_interruption_to_clear_dispatch": summary(clear_ms), "samples": samples,
            "errors": errors, "python": platform.python_version(), "runtime_platform": platform.system(),
            "actual_voice_conversation": False, "provider_network_tested": False,
            "audible_interruption_tested": False, "total_runtime_memory_measured": False}
