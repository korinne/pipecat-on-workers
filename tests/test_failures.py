"""Regression checks for terminal STT failure and resumed provider turn numbering."""
import asyncio
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from runtime_probe import Harness, wait_for
from conversation import MAX_PENDING_RECEIPTS


class ProviderFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_speech_receipts_are_bounded_and_uncommitted(self):
        for leave_last_unplayed in (False, True):
            with self.subTest(leave_last_unplayed=leave_last_unplayed):
                h = await Harness(pcm_chunks=1000).start()
                original_send = h.session.send
                peak_pending = 0

                async def send_with_receipts(event):
                    nonlocal peak_pending
                    await original_send(event)
                    if event["type"] == "audio":
                        peak_pending = max(peak_pending, len(h.session.pending))
                        if not (leave_last_unplayed and len(h.audio()) == MAX_PENDING_RECEIPTS):
                            await h.session.played(event["generation"], event["chunk_id"])

                h.session.send = send_with_receipts
                try:
                    await h.turn("overlong speech segment", 0)
                    await wait_for(lambda: any(e["type"] == "clear" and
                        e.get("reason") == "response_error" for e in h.events), "receipt-limit failure")
                    self.assertEqual(len(h.audio()), MAX_PENDING_RECEIPTS)
                    self.assertEqual(peak_pending, MAX_PENDING_RECEIPTS)
                    errors = [e["message"] for e in h.events if e["type"] == "error"]
                    self.assertEqual(len(errors), 1)
                    self.assertIn("256-chunk receipt limit", errors[0])
                    self.assertEqual(h.session.pending, {})
                    self.assertEqual(h.session.unacked_bytes, 0)
                    self.assertEqual(h.provider.live_generations, 0)
                    self.assertEqual(h.provider.live_syntheses, 0)
                    self.assertTrue(h.provider.synthesis_cancelled.is_set())
                    self.assertEqual(h.assistant(), [])
                    self.assertFalse(any(m["role"] == "assistant"
                        for state in h.saved for m in state.get("messages", [])))
                    # Late receipts cannot revive the discarded sentence.
                    await h.acknowledge()
                    self.assertEqual(h.assistant(), [])
                    # The failed response does not make the next turn unusable.
                    h.session.send = original_send
                    h.provider.pcm_chunks = 3
                    await h.turn("normal follow-up", 1)
                    await h.response(2)
                    self.assertEqual(h.assistant(), [])
                    await h.acknowledge()
                    self.assertEqual(h.assistant(), ["fixture reply to normal follow-up."])
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

    async def test_reconnect_resets_turn_index_without_losing_final_transcript(self):
        h = await Harness().start()
        try:
            await h.turn("before reconnect", 0)
            await h.response()
            await h.acknowledge()
            await h.begin("unfinished fragment", 1)
            await h.session.provider_event({"type": "TurnInfo", "event": "Update",
                "transcript": "unfinished fragment", "turn_index": 1, "connection_generation": 1})
            before = h.session.generation
            await h.session.provider_event({"type": "ProviderError", "provider": "stt",
                "message": "Socket lost", "recoverable": True, "audio_gap": True})
            await wait_for(lambda: h.session.generation > before, "recoverable error interruption")
            self.assertFalse(h.session.closed)
            await h.session.provider_event({"type": "TurnInfo", "event": "StartOfTurn",
                "transcript": "", "turn_index": 0, "connection_generation": 2})
            final = {"type": "TurnInfo", "event": "EndOfTurn",
                "transcript": "after reconnect", "turn_index": 0, "connection_generation": 2}
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
