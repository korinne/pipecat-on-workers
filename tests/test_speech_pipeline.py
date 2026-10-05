"""Pinned speech/context behavior through real vendored Pipecat and both adapters.

Provider inference, SFU REST/media leaves and browser delivery are controlled.
No package-support, physical-playback or live-provider claim follows from these
checks. The interruption expectations are the unchanged Task 1 reference's.
"""
import asyncio
import copy
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "acceptance"))

from conversation import CONTEXT_SCHEMA, SYSTEM
from pipecat.frames.frames import LLMFullResponseEndFrame, LLMTextFrame, TTSAudioRawFrame, TTSTextFrame
from pipecat.services.tts_service import TTSService
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.processors.aggregators.llm_response_universal import LLMAssistantAggregator
from runtime_probe import Harness, wait_for
from sfu_cleanup_fixture import CleanupHarness

FIRST = "Tuesday morning is available."
SECOND = "Thursday afternoon is also available."
ANSWER = FIRST + " " + SECOND
QUESTION = "Give me two choices."
FOLLOWUP = "What were those choices?"
PCM = b"\x10\x00" * 480  # 20 ms nonzero PCM16, mono, 24 kHz.


class SpeechHarness:
    """Add controllable provider boundaries to the real application pipeline."""
    def __init__(self, route, *, mode="complete", block_write=None, state=None):
        self.route, self.mode, self.block_write = route, mode, block_write
        self.inner = Harness(state=state) if route == "websocket" else CleanupHarness()
        if state is not None and route == "sfu":
            # Reuse the actual SFU I/O fixture with a new saved-context session.
            from conversation import ConversationSession
            from runtime_probe import FixtureProviders
            old = self.inner.session
            def factory(callback):
                self.inner.provider = FixtureProviders(callback)
                return self.inner.provider
            self.inner.session = ConversationSession(copy.deepcopy(state), factory, old.send, old.save,
                                                     audio_transport=self.inner.transport)
        self.session, self.provider = self.inner.session, self.inner.provider
        self.events, self.saved = self.inner.events, self.inner.saved
        self.attempts = 0
        self.writes = []
        self.blocked, self.release, self.write_cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.first_write, self.first_text = asyncio.Event(), asyncio.Event()
        self.failed = asyncio.Event()
        self.injected_failures = []
        self.generation_closed = asyncio.Event()
        self.synthesis_closed = asyncio.Event()
        send = self.session.send
        async def observe(event):
            await send(event)
            if event.get("type") == "transcript" and event.get("role") == "assistant" and event.get("text") == FIRST:
                self.first_text.set()
            if event.get("type") == "error":
                self.failed.set()
        self.session.send = observe
        adapter_write = self.session.transport.send_audio
        async def write(pcm, generation):
            self.attempts += 1
            if self.attempts == self.block_write:
                self.blocked.set()
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.write_cancelled.set()
                    raise
            accepted = await adapter_write(pcm, generation)
            if accepted:
                self.writes.append((bytes(pcm), generation))
                self.first_write.set()
            return accepted
        self.session.transport.send_audio = write

        async def generate(messages):
            self.provider.generations.append(copy.deepcopy(messages))
            self.provider.generation_started.set()
            self.provider.live_generations += 1
            try:
                if self.mode == "generation_before_text":
                    self.injected_failures.append(self.mode)
                    raise RuntimeError("controlled generation failure")
                if self.mode == "generation_after_first_text":
                    yield FIRST + " Unfinished"
                    await asyncio.wait_for(self.first_text.wait(), 4)
                    self.injected_failures.append(self.mode)
                    raise RuntimeError("controlled generation failure after sentence")
                yield FIRST if self.mode == "tail" else ANSWER
            finally:
                self.provider.live_generations -= 1
                self.generation_closed.set()
        self.provider.generate = generate

        async def synthesize(text):
            self.provider.syntheses.append(text)
            number = len(self.provider.syntheses)
            self.provider.synthesis_started.set()
            self.provider.live_syntheses += 1
            try:
                if self.mode == "tts_before_audio" and number == 1:
                    self.injected_failures.append(self.mode)
                    raise RuntimeError("controlled synthesis failure before audio")
                if self.mode == "tts_second_sentence" and number == 2:
                    await asyncio.wait_for(self.first_text.wait(), 4)
                    self.injected_failures.append(self.mode)
                    raise RuntimeError("controlled second sentence failure")
                if self.mode == "tts_empty" and number == 1:
                    return
                if self.mode == "tail":
                    yield PCM[:480]
                    return
                for _ in range(3):
                    yield PCM
                    if self.mode == "tts_after_audio" and number == 1:
                        await asyncio.wait_for(self.first_write.wait(), 4)
                        self.injected_failures.append(self.mode)
                        raise RuntimeError("controlled synthesis failure after audio")
                    await asyncio.sleep(0)
            finally:
                self.provider.live_syntheses -= 1
                self.synthesis_closed.set()
        self.provider.synthesize = synthesize

    async def start(self):
        await self.inner.start()
        return self

    async def turn(self, text=QUESTION, index=0):
        await self.inner.turn(text, index)

    def assistant(self):
        return [m["content"] for m in self.session.context.get_messages() if m["role"] == "assistant"]

    async def complete(self, count=1):
        await wait_for(lambda: len(self.provider.generations) >= count and not self.session.responding
                       and any(e.get("type") == "status" and e.get("state") == "listening"
                               and e.get("generation") == self.session.generation for e in self.events),
                       "speech pipeline completion", timeout=6)

    async def settled_failure(self):
        await asyncio.wait_for(self.failed.wait(), 5)
        await wait_for(lambda: not self.session.responding and self.session.context_ready.is_set()
                       and self.provider.live_generations == 0 and self.provider.live_syntheses == 0,
                       "failure context and provider cleanup", timeout=5)

    async def interrupt(self):
        generation = self.session.generation
        await self.session.interrupt()
        await wait_for(lambda: self.session.generation > generation and self.session.context_ready.is_set()
                       and not self.session.responding, "assistant interruption commit")

    async def close(self):
        self.release.set()
        await self.inner.close()


class SpeechPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def assert_followup(self, h, retained):
        h.mode, h.block_write = "complete", None
        await h.turn(FOLLOWUP, 1)
        await h.complete(2)
        expected = [{"role":"system","content":SYSTEM}, {"role":"user","content":QUESTION}]
        if retained:
            expected.append({"role":"assistant","content":retained})
        expected.append({"role":"user","content":FOLLOWUP})
        self.assertEqual(h.provider.generations[1], expected)

    async def completed(self, route):
        h = await SpeechHarness(route).start()
        try:
            self.assertIsInstance(h.session.tts, TTSService)
            self.assertIsInstance(h.session.output, BaseOutputTransport)
            self.assertIsInstance(h.session.assistant, LLMAssistantAggregator)
            await h.turn()
            await h.complete()
            self.assertEqual(h.assistant(), [ANSWER])
            self.assertEqual(len(h.writes), 6)
            self.assertEqual(h.saved[-1]["messages"], h.session.context.get_messages())
            self.assertEqual(h.saved[-1]["context_schema"], CONTEXT_SCHEMA)
            if route == "websocket":
                self.assertGreater(h.session.unacked_bytes, 0)
                before = copy.deepcopy(h.session.context.get_messages())
                await h.inner.acknowledge()
                self.assertEqual(h.session.context.get_messages(), before)
            await self.assert_followup(h, ANSWER)
        finally:
            await h.close()

    async def interrupted(self, route, boundary):
        blocked, retained, writes = {
            "before_output": (1, None, 0),
            "mid_first": (2, None, 1),
            "after_first": (4, FIRST, 3),
            "tail": (1, FIRST, 0),
        }[boundary]
        h = await SpeechHarness(route, mode="tail" if boundary == "tail" else "complete", block_write=blocked).start()
        try:
            await h.turn()
            await asyncio.wait_for(h.blocked.wait(), 5)
            await h.interrupt()
            self.assertTrue(h.write_cancelled.is_set())
            self.assertEqual(len(h.writes), writes)
            self.assertEqual(h.assistant(), [retained] if retained else [])
            self.assertEqual([m["content"] for m in h.saved[-1]["messages"] if m["role"] == "assistant"],
                             [retained] if retained else [])
            await self.assert_followup(h, retained)
        finally:
            await h.close()

    async def failed(self, route, mode):
        retained = FIRST if mode in ("tts_second_sentence", "generation_after_first_text") else None
        h = await SpeechHarness(route, mode=mode).start()
        try:
            await h.turn()
            await h.settled_failure()
            self.assertEqual(h.injected_failures, [] if mode == "tts_empty" else [mode],
                             "Fixture timeout must not masquerade as provider failure")
            self.assertEqual(h.assistant(), [retained] if retained else [])
            self.assertEqual([m["content"] for m in h.saved[-1]["messages"] if m["role"] == "assistant"],
                             [retained] if retained else [])
            stage = "generation" if mode.startswith("generation_") else "synthesis"
            self.assertTrue(any(m["event"] == "response_failed" and m["stage"] == stage
                                for m in h.session.metrics))
            self.assertFalse(any(e.get("type") == "status" and e.get("state") == "listening"
                                 for e in h.events), "Failed response reported successful completion")
            transcripts = [e["text"] for e in h.events if e.get("type") == "transcript"
                           and e.get("role") == "assistant"]
            self.assertEqual(transcripts, [retained] if retained else [])
            if mode in ("tts_before_audio", "tts_empty", "generation_before_text"):
                self.assertEqual(h.writes, [])
            elif mode == "tts_after_audio":
                self.assertEqual(len(h.writes), 1)
            await self.assert_followup(h, retained)
        finally:
            await h.close()

    async def output_write_timeout(self, route):
        h = SpeechHarness(route, block_write=1)
        h.session.output._params.audio_out_write_timeout_secs = .01
        await h.start()
        try:
            await h.turn()
            await h.settled_failure()
            self.assertTrue(h.blocked.is_set())
            self.assertTrue(h.write_cancelled.is_set())
            self.assertEqual(h.writes, [])
            self.assertEqual(h.assistant(), [])
            self.assertEqual([m for m in h.saved[-1]["messages"] if m["role"] == "assistant"], [])
            self.assertTrue(any(m["event"] == "response_failed" and m["stage"] == "output"
                                for m in h.session.metrics))
            self.assertFalse(any(e.get("type") == "status" and e.get("state") == "listening"
                                 for e in h.events), "Output timeout was reported as success")
            self.assertFalse(any(e.get("type") == "transcript" and e.get("role") == "assistant"
                                 for e in h.events), "Unsent sentence escaped after output timeout")
            h.session.output._params.audio_out_write_timeout_secs = 40
            await self.assert_followup(h, None)
        finally:
            await h.close()

    async def output_completion_failure(self, route, behavior):
        h = await SpeechHarness(route).start()
        original = h.session.transport.finish_generation
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def finish(generation):
            entered.set()
            if behavior == "exception":
                raise RuntimeError("controlled output completion failure")
            if behavior == "false":
                return False
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
        h.session.transport.finish_generation = finish
        try:
            with patch("speech_pipeline.OUTPUT_FINISH_TIMEOUT", .03):
                await h.turn()
                await h.settled_failure()
            self.assertTrue(entered.is_set())
            if behavior == "timeout":
                self.assertTrue(cancelled.is_set())
            self.assertEqual(len(h.writes), 6)
            self.assertEqual(h.assistant(), [ANSWER])
            self.assertEqual([m["content"] for m in h.saved[-1]["messages"] if m["role"] == "assistant"], [ANSWER])
            failures = [m for m in h.session.metrics if m["event"] == "response_failed"]
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0]["stage"], "output_completion")
            self.assertEqual(failures[0]["exception_type"], "TimeoutError" if behavior == "timeout" else "RuntimeError")
            self.assertFalse(any(e.get("type") == "status" and e.get("state") == "listening"
                                 for e in h.events), "Failed output completion was reported as success")
            h.session.transport.finish_generation = original
            await self.assert_followup(h, ANSWER)
        finally:
            h.session.transport.finish_generation = original
            await h.close()

    async def restored(self, route):
        state = {"context_schema":CONTEXT_SCHEMA,"generation":8,"messages":[
            {"role":"system","content":"Obsolete receipt-dependent instructions."},
            {"role":"user","content":"Earlier request."},
            {"role":"assistant","content":FIRST},
            {"role":"tool","content":"Obsolete tool prompt."},
            {"role":"assistant","content":None},
        ]}
        h = await SpeechHarness(route, state=state).start()
        try:
            self.assertFalse(h.session.history_reset)
            await h.turn(FOLLOWUP)
            await h.complete()
            self.assertEqual(h.provider.generations[0], [
                {"role":"system","content":SYSTEM},
                {"role":"user","content":"Earlier request."},
                {"role":"assistant","content":FIRST},
                {"role":"user","content":FOLLOWUP},
            ])
            self.assertEqual(h.assistant(), [FIRST, ANSWER])
            self.assertNotIn("Obsolete", str(h.saved[-1]["messages"]))
        finally:
            await h.close()

    async def old_saved(self, route):
        h = await SpeechHarness(route, state={"generation":8,"messages":[
            {"role":"system","content":"Obsolete prompt."},
            {"role":"user","content":"Incomplete legacy request."},
            {"role":"assistant","content":"Legacy partial answer."},
        ]}).start()
        try:
            self.assertTrue(h.session.history_reset)
            reset = next(e for e in h.events if e["type"] == "reset")
            self.assertEqual(reset["history"], [])
            self.assertIn("older history format", reset["message"])
            await h.turn()
            await h.complete()
            self.assertEqual(h.provider.generations[0], [{"role":"system","content":SYSTEM},
                                                        {"role":"user","content":QUESTION}])
            self.assertEqual(h.assistant(), [ANSWER])
        finally:
            await h.close()

    async def retired_tts_context(self, route):
        h = await SpeechHarness(route).start()
        try:
            await h.turn()
            await h.complete()
            old_context = next(iter(h.session.tts.context_generations))
            await h.turn(FOLLOWUP, 1)
            await h.complete(2)
            self.assertNotIn(old_context, h.session.tts.context_generations)
            before = (len(h.writes), copy.deepcopy(h.session.context.get_messages()), len(h.events))
            late_text = TTSTextFrame("Late stale text.", aggregated_by="sentence", context_id=old_context)
            late_audio = TTSAudioRawFrame(PCM, sample_rate=24000, num_channels=1, context_id=old_context)
            # TTS-generated frames identify their originating synthesis context;
            # old IDs must never inherit the new turn's generation metadata.
            await h.session.tts.push_frame(late_text)
            await h.session.tts.push_frame(late_audio)
            await asyncio.sleep(.05)
            self.assertEqual((len(h.writes), h.session.context.get_messages(), len(h.events)), before)
            self.assertNotEqual(late_text.metadata.get("generation"), h.session.generation)
            await h.turn("One more follow-up.", 2)
            await h.complete(3)
            self.assertEqual(h.provider.generations[2], [
                {"role":"system","content":SYSTEM},
                {"role":"user","content":QUESTION},
                {"role":"assistant","content":ANSWER},
                {"role":"user","content":FOLLOWUP},
                {"role":"assistant","content":ANSWER},
                {"role":"user","content":"One more follow-up."},
            ])
        finally:
            await h.close()

    async def stale(self, route):
        h = await SpeechHarness(route).start()
        try:
            await h.turn()
            await h.complete()
            old_generation = h.session.generation
            await h.interrupt()
            before = (len(h.writes), copy.deepcopy(h.session.context.get_messages()), len(h.provider.syntheses))
            stale_frames = [TTSAudioRawFrame(PCM, sample_rate=24000, num_channels=1),
                            TTSTextFrame("Stale answer must not return.", aggregated_by="sentence"), LLMFullResponseEndFrame()]
            for frame in stale_frames:
                frame.metadata["generation"] = old_generation
                await h.session.output.queue_frame(frame)
            token = LLMTextFrame("Stale model text must not be synthesized.")
            token.metadata["generation"] = old_generation
            await h.session.tts.queue_frame(token)
            await asyncio.sleep(.05)
            self.assertEqual((len(h.writes),h.session.context.get_messages(),len(h.provider.syntheses)), before)
            self.assertFalse(any("Stale" in e.get("text", "") for e in h.events))
            await self.assert_followup(h, ANSWER)
        finally:
            await h.close()


def install_cases():
    for route in ("websocket", "sfu"):
        cases = [("completed",()),("restored",()),("old_saved",()),("stale",()),("retired_tts_context",()),("output_write_timeout",())]
        cases += [("interrupted",(boundary,)) for boundary in ("before_output","mid_first","after_first","tail")]
        cases += [("output_completion_failure",(behavior,)) for behavior in ("exception","false","timeout")]
        cases += [("failed",(mode,)) for mode in ("tts_before_audio","tts_after_audio","tts_second_sentence",
                                                   "tts_empty","generation_before_text","generation_after_first_text")]
        for helper, args in cases:
            async def run(self, route=route, helper=helper, args=args):
                await getattr(self, helper)(route, *args)
            name = "test_" + route + "_" + helper + ("_" + args[0] if args else "")
            run.__name__ = name
            setattr(SpeechPipelineTests, name, run)

install_cases()

if __name__ == "__main__":
    unittest.main(verbosity=2)
