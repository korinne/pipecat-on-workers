"""Regression checks for terminal STT failure and a new provider audio clock."""
import asyncio
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from runtime_probe import Harness, wait_for
from conversation import MAX_PENDING_RECEIPTS, MAX_UNACKED_BYTES
from speech_pipeline import MAX_QUEUED_AUDIO_BYTES


class ProviderFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_receiver_bounds_output_and_allows_interruption_recovery(self):
        h = await Harness(pcm_chunks=1000).start()
        try:
            await h.turn("long speech segment", 0)
            await wait_for(lambda: len(h.session.pending) == MAX_PENDING_RECEIPTS,
                           "slow receiver credit exhaustion", timeout=8)
            self.assertLessEqual(h.session.unacked_bytes, MAX_UNACKED_BYTES)
            self.assertLessEqual(h.session.queued_output_bytes, MAX_QUEUED_AUDIO_BYTES)
            before = len(h.audio())
            await asyncio.sleep(0.05)
            self.assertEqual(len(h.audio()), before)
            self.assertEqual(h.assistant(), [])
            # Credit released by three old packets admits exactly three more.
            await h.acknowledge(h.audio()[:3])
            await wait_for(lambda: len(h.audio()) == before + 3, "released output credit")
            await asyncio.sleep(0.05)
            self.assertEqual(len(h.audio()), before + 3)
            self.assertEqual(len(h.session.pending), MAX_PENDING_RECEIPTS)
            old_audio = list(h.audio())
            await h.session.interrupt()
            await asyncio.wait_for(h.provider.synthesis_cancelled.wait(), 4)
            await wait_for(lambda: not h.session.pending, "interruption releases credit")
            self.assertEqual(h.session.unacked_bytes, 0)
            self.assertEqual(h.assistant(), [])
            await h.acknowledge(old_audio)
            self.assertEqual(h.assistant(), [])
            h.provider.pcm_chunks = 3
            await h.turn("normal follow-up", 1)
            await h.response(2)
            self.assertEqual(h.assistant(), ["fixture reply to normal follow-up."])
            self.assertEqual([e for e in h.events if e["type"] == "error"], [])
        finally:
            await h.close()

    async def test_terminal_failure_without_owner_closes_pending_pipeline(self):
        h = await Harness(hold_generation=True).start()
        try:
            await h.turn("pending request", 0)
            await asyncio.wait_for(h.provider.generation_started.wait(), 4)
            await h.session.provider_event({"type": "ProviderError", "provider": "stt",
                "message": "Reconnect exhausted", "recoverable": False, "audio_gap": True})
            self.assertTrue(h.session.closed)
            self.assertTrue(h.provider.closed)
            self.assertTrue(h.provider.generation_cancelled.is_set())
            self.assertEqual(h.session.diagnostics()["pipecat_tasks"], 0)
            errors = [e["message"] for e in h.events if e["type"] == "error"]
            self.assertIn("start a new call", errors[-1])
            self.assertNotIn("repeat the last utterance", errors[-1])
        finally:
            await h.close()

    async def test_terminal_failure_requests_owner_shutdown(self):
        h = await Harness(hold_generation=True).start()
        requested = asyncio.Event()

        async def on_fatal():
            requested.set()

        h.session.on_fatal = on_fatal
        try:
            await h.turn("pending request", 0)
            await asyncio.wait_for(h.provider.generation_started.wait(), 4)
            await h.session.provider_event({"type": "ProviderError", "provider": "stt",
                "message": "Reconnect exhausted", "recoverable": False})
            self.assertTrue(requested.is_set())
            # The entrypoint owns socket closure and calls close after dequeuing
            # provider_failed; mimic that explicit ownership boundary here.
            await h.session.close("terminal_provider_failure")
            self.assertTrue(h.provider.generation_cancelled.is_set())
            self.assertEqual(h.session.diagnostics()["pipecat_tasks"], 0)
        finally:
            await h.close()

    async def test_reconnect_resets_audio_clock_without_losing_final_transcript(self):
        h = await Harness().start()
        try:
            await h.turn("before reconnect", 0)
            await h.response()
            await h.acknowledge()
            await h.begin("unfinished fragment", 1)
            await h.finish("unfinished fragment", 1, final=False, pause=False)
            before = h.session.generation
            await h.session.provider_event({"type": "ProviderError", "provider": "stt",
                "message": "Socket lost", "recoverable": True, "audio_gap": True})
            await wait_for(lambda: h.session.generation > before, "recoverable error interruption")
            self.assertFalse(h.session.closed)
            await h.provider.reconnect()
            await h.begin("after reconnect", 0)
            final = h.result("after reconnect", 0)
            await h.session.provider_event(final)
            await h.response(2)
            await h.session.provider_event(final)
            await asyncio.sleep(0.02)
            users = [m["content"] for m in h.session.context.get_messages() if m["role"] == "user"]
            self.assertEqual(users, ["before reconnect", "after reconnect"])
            self.assertEqual(len(h.provider.generations), 2)
            self.assertNotIn("unfinished fragment", str(h.provider.generations[-1]))
        finally:
            await h.close()
