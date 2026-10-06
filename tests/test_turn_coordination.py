"""Nova/Smart Turn fixtures through actual Pipecat frame queues and context."""
import asyncio
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from conversation import MAX_PENDING_USER_CHARS, MAX_PENDING_USER_FRAGMENTS
from runtime_probe import Harness, wait_for


class TurnCoordinationTests(unittest.IsolatedAsyncioTestCase):
    def assert_no_pending_turn(self, h):
        diagnostic = h.session.diagnostics()
        for key in ("pending_turn_tasks", "pending_user_fragments", "pending_user_chars"):
            self.assertEqual(diagnostic[key], 0, key)

    async def test_incomplete_pause_then_resume_joins_one_turn(self):
        h = await Harness().start()
        h.provider.turn_decisions = [False, True]
        try:
            await h.turn("Please tell me about", 0)
            await wait_for(lambda: len(h.provider.turn_checks) == 1, "incomplete decision")
            await asyncio.sleep(0.02)
            self.assertEqual(h.provider.generations, [])
            generation = h.session.generation
            await h.begin("the moon.", 1, resume=True)
            self.assertEqual(h.session.generation, generation)
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

    async def test_true_end_commits_once_despite_duplicate_pause_events(self):
        h = await Harness().start()
        h.provider.turn_gate = asyncio.Event()
        try:
            await h.turn("A complete request.", 0)
            await h.finish("A complete request.", 0)
            await asyncio.wait_for(h.provider.turn_started.wait(), 2)
            self.assertEqual(h.provider.generations, [])
            self.assertEqual(len(h.provider.turn_checks), 1)
            h.provider.turn_gate.set()
            await h.response()
            await h.finish("A complete request.", 0)
            await asyncio.sleep(0.03)
            self.assertEqual(len(h.provider.generations), 1)
            self.assertEqual(len(h.provider.turn_checks), 1)
            users = [m["content"] for m in h.provider.generations[0] if m["role"] == "user"]
            self.assertEqual(users, ["A complete request."])
            self.assert_no_pending_turn(h)
        finally:
            await h.close()

    async def test_close_cancels_detector_and_discards_fragment(self):
        h = await Harness().start()
        h.provider.turn_gate = asyncio.Event()
        await h.turn("Do not commit after close.", 0)
        await asyncio.wait_for(h.provider.turn_started.wait(), 2)
        await h.close()
        h.provider.turn_gate.set()
        await asyncio.sleep(0.02)
        self.assertTrue(h.provider.turn_cancelled.is_set())
        self.assertEqual(h.provider.generations, [])
        self.assertFalse(any(e["type"] == "transcript" and e.get("final") for e in h.events))
        self.assert_no_pending_turn(h)

    async def test_provider_failure_discards_pending_fragment(self):
        for recoverable in (True, False):
            with self.subTest(recoverable=recoverable):
                h = await Harness().start()
                try:
                    await h.turn("Previous completed request.", 0)
                    await h.response()
                    await h.acknowledge()
                    h.provider.turn_started.clear()
                    h.provider.turn_gate = asyncio.Event()
                    await h.turn("Discard this partial turn.", 1)
                    await asyncio.wait_for(h.provider.turn_started.wait(), 2)
                    await h.session.provider_event({"type": "ProviderError", "provider": "stt",
                        "message": "Test failure", "recoverable": recoverable})
                    await asyncio.sleep(0.03)
                    self.assertEqual(len(h.provider.generations), 1)
                    self.assert_no_pending_turn(h)
                    if recoverable:
                        h.provider.turn_gate = None
                        await h.provider.reconnect()
                        await h.turn("Recovered request.", 0)
                        await h.response(2)
                        users = [m["content"] for m in h.provider.generations[1] if m["role"] == "user"]
                        self.assertEqual(users, ["Previous completed request.", "Recovered request."])
                finally:
                    await h.close()

    async def test_pending_text_and_fragment_limits_fail_without_partial_commit(self):
        for too_many_fragments in (False, True):
            with self.subTest(too_many_fragments=too_many_fragments):
                h = await Harness().start()
                try:
                    await h.begin("bounded turn", 0)
                    count = MAX_PENDING_USER_FRAGMENTS + 1 if too_many_fragments else 1
                    text = "fragment" if too_many_fragments else "x" * (MAX_PENDING_USER_CHARS + 1)
                    for index in range(count):
                        if index:
                            await h.input_segment(index)
                        await h.finish(text, index, pause=False)
                    self.assertTrue(any(e["type"] == "error" for e in h.events))
                    self.assertEqual(h.provider.generations, [])
                    await wait_for(lambda: h.session.diagnostics()["pending_turn_tasks"] == 0,
                                   "bounded turn cleanup")
                    self.assert_no_pending_turn(h)
                    await h.turn("Short recovery.", count)
                    await h.response()
                    users = [m["content"] for m in h.provider.generations[0] if m["role"] == "user"]
                    self.assertEqual(users, ["Short recovery."])
                finally:
                    await h.close()

    async def test_resume_while_detector_waits_cancels_old_completion(self):
        h = await Harness().start()
        h.provider.turn_gate = asyncio.Event()
        try:
            await h.turn("Please explain", 0)
            await asyncio.wait_for(h.provider.turn_started.wait(), 2)
            await h.begin("the difference.", 1, resume=True)
            await asyncio.wait_for(h.provider.turn_cancelled.wait(), 2)
            self.assertEqual(h.provider.generations, [])
            h.provider.turn_gate = None
            await h.finish("the difference.", 1)
            await h.response()
            users = [m["content"] for m in h.provider.generations[0] if m["role"] == "user"]
            self.assertEqual(users, ["Please explain the difference."])
            self.assertEqual(len(h.provider.generations), 1)
        finally:
            await h.close()

    async def test_detector_failure_does_not_answer_partial_turn_and_recovers(self):
        h = await Harness().start()
        h.provider.turn_decisions = [RuntimeError("fixture detector failure"), True]
        try:
            await h.turn("Do not answer this.", 0)
            await wait_for(lambda: any(e["type"] == "error" for e in h.events), "detector failure")
            self.assertEqual(h.provider.generations, [])
            self.assertEqual([m for m in h.session.context.get_messages() if m["role"] == "user"], [])
            await h.turn("A fresh request.", 1)
            await h.response()
            users = [m["content"] for m in h.provider.generations[0] if m["role"] == "user"]
            self.assertEqual(users, ["A fresh request."])
        finally:
            await h.close()

    async def test_finalization_deadline_discards_pending_turn(self):
        h = await Harness(turn_timeout_secs=0.04).start()
        h.provider.turn_gate = asyncio.Event()
        try:
            await h.turn("Do not answer a timeout.", 0)
            await wait_for(lambda: any(e["type"] == "error" for e in h.events), "turn deadline")
            h.provider.turn_gate.set()
            await asyncio.sleep(0.03)
            self.assertEqual(h.provider.generations, [])
            self.assert_no_pending_turn(h)
            h.provider.turn_gate = None
            await h.turn("Recovered after deadline.", 1)
            await h.response()
            users = [m["content"] for m in h.provider.generations[0] if m["role"] == "user"]
            self.assertEqual(users, ["Recovered after deadline."])
        finally:
            await h.close()

    async def test_watchdog_cannot_answer_incomplete_transcript(self):
        h = await Harness(turn_timeout_secs=0.12, user_turn_stop_timeout=0.02).start()
        h.provider.turn_decisions = [False]
        try:
            await h.turn("An unfinished thought", 0)
            await asyncio.sleep(0.06)
            self.assertEqual(h.provider.generations, [])
            self.assertEqual([m for m in h.session.context.get_messages() if m["role"] == "user"], [])
            await wait_for(lambda: any(e["type"] == "error" for e in h.events), "incomplete recovery")
            self.assert_no_pending_turn(h)
        finally:
            await h.close()
