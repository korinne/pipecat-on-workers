"""Short real-Pipecat checks for bounded, cancellable provider EOT grace."""
import asyncio
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from conversation import MAX_PENDING_USER_CHARS, MAX_PENDING_USER_FRAGMENTS
from runtime_probe import Harness


class TurnGraceTests(unittest.IsolatedAsyncioTestCase):
    def assert_no_pending_turn(self, h):
        diagnostic = h.session.diagnostics()
        for key in ("pending_turn_tasks", "pending_user_fragments", "pending_user_chars"):
            self.assertEqual(diagnostic[key], 0, key)

    async def test_resume_cancels_commit_and_joins_one_turn(self):
        h = await Harness(turn_end_grace_ms=50).start()
        try:
            await h.turn("Please tell me about", 0)
            generation = h.session.generation
            await h.session.provider_event({"type": "TurnInfo", "event": "StartOfTurn",
                                           "turn_index": 1, "connection_generation": 1})
            await asyncio.sleep(0.08)
            self.assertEqual(h.provider.generations, [])
            self.assertEqual(h.session.generation, generation)
            self.assertEqual(h.session.diagnostics()["pending_turn_tasks"], 0)
            self.assertEqual(h.session.diagnostics()["pending_user_fragments"], 1)
            await h.finish("the moon.", 1)
            await h.response()
            users = [m["content"] for m in h.provider.generations[0] if m["role"] == "user"]
            self.assertEqual(users, ["Please tell me about the moon."])
            finals = [e for e in h.events if e["type"] == "transcript" and
                      e.get("role") == "user" and e.get("final")]
            self.assertEqual([e["text"] for e in finals], users)
            self.assert_no_pending_turn(h)
        finally:
            await h.close()

    async def test_true_end_commits_once_despite_duplicate_end_events(self):
        h = await Harness(turn_end_grace_ms=30).start()
        try:
            await h.turn("A complete request.", 0)
            await h.finish("A complete request.", 0)
            self.assertEqual(h.provider.generations, [])
            self.assertEqual(h.session.diagnostics()["pending_user_fragments"], 1)
            await h.response()
            await h.finish("A complete request.", 0)
            await asyncio.sleep(0.05)
            self.assertEqual(len(h.provider.generations), 1)
            users = [m["content"] for m in h.provider.generations[0] if m["role"] == "user"]
            self.assertEqual(users, ["A complete request."])
            self.assert_no_pending_turn(h)
        finally:
            await h.close()

    async def test_close_cancels_timer_and_discards_fragment(self):
        h = await Harness(turn_end_grace_ms=30).start()
        await h.turn("Do not commit after close.", 0)
        self.assertEqual(h.session.diagnostics()["pending_turn_tasks"], 1)
        await h.close()
        await asyncio.sleep(0.05)
        self.assertEqual(h.provider.generations, [])
        self.assertFalse(any(e["type"] == "transcript" and e.get("final") for e in h.events))
        self.assert_no_pending_turn(h)

    async def test_provider_failure_discards_pending_fragment(self):
        for recoverable in (True, False):
            with self.subTest(recoverable=recoverable):
                h = await Harness(turn_end_grace_ms=30).start()
                try:
                    await h.turn("Previous completed request.", 0)
                    await h.response()
                    await h.acknowledge()
                    await h.turn("Discard this partial turn.", 1)
                    await h.session.provider_event({"type": "ProviderError", "provider": "stt",
                        "message": "Test failure", "recoverable": recoverable})
                    await asyncio.sleep(0.05)
                    # Stopping an empty Pipecat turn must not replay prior context.
                    self.assertEqual(len(h.provider.generations), 1)
                    self.assert_no_pending_turn(h)
                    if recoverable:
                        await h.turn("Recovered request.", 2)
                        await h.response(2)
                        users = [m["content"] for m in h.provider.generations[1] if m["role"] == "user"]
                        self.assertEqual(users, ["Previous completed request.", "Recovered request."])
                finally:
                    await h.close()

    async def test_pending_text_and_fragment_limits_fail_without_partial_commit(self):
        for too_many_fragments in (False, True):
            with self.subTest(too_many_fragments=too_many_fragments):
                h = await Harness(turn_end_grace_ms=1000).start()
                try:
                    await h.begin("bounded turn", 0)
                    count = MAX_PENDING_USER_FRAGMENTS + 1 if too_many_fragments else 1
                    text = "fragment" if too_many_fragments else "x" * (MAX_PENDING_USER_CHARS + 1)
                    for index in range(count):
                        await h.finish(text, index)
                    self.assertTrue(any(e["type"] == "error" and "text limit" in e["message"]
                                        for e in h.events))
                    self.assertEqual(h.provider.generations, [])
                    self.assert_no_pending_turn(h)
                    # The bounded abort also allows another genuine user turn.
                    await h.turn("Short recovery.", count)
                    await h.response()
                    users = [m["content"] for m in h.provider.generations[0] if m["role"] == "user"]
                    self.assertEqual(users, ["Short recovery."])
                finally:
                    await h.close()
