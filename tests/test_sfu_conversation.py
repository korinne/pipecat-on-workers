"""Real Pipecat pipeline with synthetic providers and an SFU transport double.

These checks verify application semantics, not network audio or human playback.
"""
import asyncio
import copy
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from conversation import ConversationSession
from runtime_probe import FixtureProviders, Harness, wait_for


class Transport:
    def __init__(self, timeline, *, hold=False):
        self.timeline = timeline
        self.sent = []
        self.finished = []
        self.cleared = []
        self.closed = False
        self.hold = hold
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.gate = asyncio.Event()

    async def send_audio(self, pcm, generation):
        self.started.set()
        try:
            if self.hold:
                await self.gate.wait()
            self.sent.append((bytes(pcm), generation))
            return True
        except asyncio.CancelledError:
            self.cancelled.set()
            raise

    async def finish_generation(self, generation):
        self.finished.append(generation)
        self.timeline.append(("eos", generation))
        return True

    async def clear(self, generation):
        self.cleared.append(generation)
        self.timeline.append(("transport_clear", generation))

    async def close(self): self.closed = True
    def diagnostics(self): return {"sfu_delivery_confirmed": False, "sfu_test_closed": self.closed}


class SfuHarness(Harness):
    def __init__(self, *, pcm_chunks=3, hold=False):
        self.events, self.saved, self.timeline = [], [], []
        self.transport = Transport(self.timeline, hold=hold)

        async def send(event):
            self.events.append(copy.deepcopy(event))
            self.timeline.append((event["type"], event.get("generation")))

        async def save(state): self.saved.append(copy.deepcopy(state))

        def factory(callback):
            self.provider = FixtureProviders(callback, pcm_chunks=pcm_chunks)
            return self.provider

        self.session = ConversationSession({}, factory, send, save, audio_transport=self.transport)


class SfuConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_response_commits_standard_context_without_receipts(self):
        # More audio than the WS credit window: SFU must not wait for WS acks.
        harness = await SfuHarness(pcm_chunks=90).start()
        try:
            await harness.turn("hello", 1)
            await wait_for(lambda: bool(harness.transport.finished), "SFU EOS")
            self.assertEqual(len(harness.transport.sent), 450)
            self.assertTrue(all(len(pcm) == 960 for pcm, _ in harness.transport.sent))
            self.assertEqual(harness.audio(), [])
            self.assertEqual(harness.session.pending, {})
            self.assertEqual(harness.session.unacked_bytes, 0)
            self.assertEqual(harness.assistant(), ["fixture reply to hello."])
            self.assertTrue(any(event["type"] == "transcript" and event.get("role") == "assistant" for event in harness.events))
            # Receipts cannot alter standard assistant context on either route.
            await harness.session.played(harness.session.generation, harness.session.next_chunk)
            self.assertEqual(harness.assistant(), ["fixture reply to hello."])
            self.assertTrue(any(message["role"] == "assistant" for saved in harness.saved for message in saved["messages"]))
            self.assertNotIn("Audio playback cannot be confirmed", harness.session.context.get_messages()[0]["content"])
            self.assertEqual(harness.session.diagnostics()["transport"], "webrtc")
            self.assertFalse(harness.session.diagnostics()["sfu_delivery_confirmed"])
            eos_index = next(i for i, value in enumerate(harness.timeline) if value[0] == "eos")
            self.assertTrue(any(value[0] == "status" for value in harness.timeline[eos_index + 1:]))
        finally:
            await harness.close()
        self.assertTrue(harness.transport.closed)
        self.assertTrue(harness.provider.closed)
        self.assertEqual(harness.session.diagnostics()["pipecat_tasks"], 0)

    async def test_interruption_cancels_pending_sfu_send_before_eos_and_clears_browser_first(self):
        harness = await SfuHarness(hold=True).start()
        try:
            await harness.turn("hello", 1)
            await asyncio.wait_for(harness.transport.started.wait(), 2)
            old_generation = harness.session.generation
            await harness.session.interrupt()
            await wait_for(lambda: harness.session.generation > old_generation, "SFU interruption")
            await asyncio.wait_for(harness.transport.cancelled.wait(), 2)
            self.assertEqual(harness.transport.finished, [])
            self.assertEqual(harness.transport.sent, [])
            self.assertEqual(harness.assistant(), [])
            new_generation = harness.session.generation
            clear_index = harness.timeline.index(("clear", new_generation))
            transport_index = harness.timeline.index(("transport_clear", new_generation))
            self.assertLess(clear_index, transport_index)
            harness.transport.hold = False
            await harness.turn("try again", 2)
            await wait_for(lambda: bool(harness.transport.finished), "recovery SFU EOS")
            self.assertTrue(all(generation > old_generation for _, generation in harness.transport.sent))
        finally:
            await harness.close()

    async def test_end_closes_transport_while_output_is_pending(self):
        harness = await SfuHarness(hold=True).start()
        await harness.turn("hello", 1)
        await asyncio.wait_for(harness.transport.started.wait(), 2)
        await harness.close()
        self.assertTrue(harness.transport.closed)
        self.assertTrue(harness.transport.cancelled.is_set())
        self.assertEqual(harness.transport.finished, [])
        self.assertEqual(harness.session.diagnostics()["pipecat_tasks"], 0)


if __name__ == "__main__": unittest.main()
