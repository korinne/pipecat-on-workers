"""Storage failures through the actual speech pipeline and both media adapters.

Only storage/provider/SFU leaves are controlled. These checks do not establish
live storage failures, provider behavior or audible playback.
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

from conversation import ContextPersistenceError
from runtime_probe import Harness, wait_for
from sfu_cleanup_fixture import CleanupHarness


def harness(route):
    return Harness(pcm_chunks=1) if route == "websocket" else CleanupHarness()


def assistants(messages):
    return [m["content"] for m in messages if m["role"] == "assistant"]


class ContextPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def settled(self, h):
        await wait_for(lambda: h.session.close_task is not None and h.session.close_task.done(),
                       "owned fatal close", timeout=6)
        await wait_for(lambda: h.session.fatal_close_task is None or h.session.fatal_close_task.done(),
                       "fatal owner completion", timeout=3)
        self.assertTrue(h.provider.closed)
        self.assertTrue(h.session.transport.closed)
        self.assertEqual(h.session.diagnostics()["pipecat_tasks"], 0)

    async def completed(self, h, count=1):
        await wait_for(lambda: len(h.provider.generations) == count and not h.session.responding and
                       any(e.get("type") == "status" and e.get("state") == "listening" and
                           e.get("generation") == h.session.generation for e in h.events),
                       "durably completed response", timeout=5)

    async def test_transient_assistant_save_retries_before_success_on_both_routes(self):
        for route in ("websocket", "sfu"):
            with self.subTest(route=route):
                h = harness(route)
                await h.start()
                save, failures = h.session.save, []
                async def transient(state):
                    if assistants(state["messages"]) and len(failures) < 2:
                        failures.append(copy.deepcopy(state))
                        raise OSError("controlled transient write failure")
                    await save(state)
                h.session.save = transient
                try:
                    await h.turn("Remember the color blue.", 0)
                    await self.completed(h)
                    self.assertEqual(len(failures), 2)
                    self.assertFalse(h.session.closed)
                    self.assertFalse(h.session.persistence_failed)
                    self.assertEqual(h.saved[-1]["messages"], h.session.context.get_messages())
                    first = assistants(h.saved[-1]["messages"])
                    await h.turn("What color?", 1)
                    await self.completed(h, 2)
                    self.assertEqual(assistants(h.provider.generations[1]), first)
                finally:
                    await h.close()

    async def test_exhausted_assistant_save_ends_and_restores_durable_context_on_both_routes(self):
        for route in ("websocket", "sfu"):
            with self.subTest(route=route):
                h = harness(route)
                await h.start()
                save, rejected, owners = h.session.save, [], []
                async def failing(state):
                    if assistants(state["messages"]):
                        rejected.append(copy.deepcopy(state))
                        raise OSError("controlled permanent write failure")
                    await save(state)
                async def owner():
                    owners.append(asyncio.current_task())
                    await h.session.close("owner_fatal")
                h.session.save, h.session.on_fatal = failing, owner
                try:
                    await h.turn("Remember the color blue.", 0)
                    await self.settled(h)
                    self.assertEqual(len(rejected), 3)
                    self.assertEqual(len(owners), 1)
                    self.assertEqual(assistants(h.saved[-1]["messages"]), [])
                    self.assertEqual(h.session.context.get_messages(), h.saved[-1]["messages"])
                    self.assertEqual(h.session.state, h.saved[-1])
                    self.assertTrue(h.session.diagnostics()["context_persistence_failed"])
                    self.assertEqual(h.session.diagnostics()["context_pending_writes"], 0)
                    self.assertTrue(any(e.get("code") == "context_persistence_failed" for e in h.events))
                    self.assertTrue(any(e.get("type") == "clear" and e.get("reason") ==
                                        "context_persistence_failed" for e in h.events))
                    self.assertFalse(any(e.get("state") == "listening" for e in h.events))
                    await h.session.provider_event({"type":"SpeechStarted", "timestamp":1})
                    await h.session.audio(bytes(3200))
                    self.assertEqual(len(h.provider.generations), 1)
                    with self.assertRaises(ContextPersistenceError):
                        await h.session.persist()
                    self.assertEqual(len(rejected), 3)
                    reconnected = Harness(state=h.saved[-1])
                    await reconnected.start()
                    try:
                        self.assertEqual(reconnected.session.context.get_messages(), h.saved[-1]["messages"])
                        self.assertEqual(reconnected.assistant(), [])
                    finally:
                        await reconnected.close()
                finally:
                    await h.close()

    async def test_preinference_save_failure_never_recommends_retry_or_calls_model(self):
        for route in ("websocket", "sfu"):
            with self.subTest(route=route):
                h = harness(route)
                await h.start()
                save, rejected = h.session.save, []
                async def fail_user(state):
                    if any(m["role"] == "user" for m in state["messages"]):
                        rejected.append(state)
                        raise OSError("controlled user context save failure")
                    await save(state)
                h.session.save = fail_user
                try:
                    await h.turn("This user context must be saved first.", 0)
                    await self.settled(h)
                    self.assertEqual(len(rejected), 3)
                    self.assertEqual(h.provider.generations, [])
                    errors = [e for e in h.events if e.get("type") == "error"]
                    self.assertEqual(len(errors), 1)
                    self.assertEqual(errors[0]["code"], "context_persistence_failed")
                    self.assertFalse(errors[0]["recoverable"])
                    self.assertEqual(h.session.context.get_messages(), h.saved[-1]["messages"])
                finally:
                    await h.close()

    async def test_startup_write_failure_never_announces_ready(self):
        h = Harness()
        attempts = []
        async def fail(state):
            attempts.append(state)
            raise OSError("controlled startup write failure")
        h.session.save = fail
        with self.assertRaises(ContextPersistenceError):
            await h.start()
        await self.settled(h)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(h.saved, [])
        self.assertFalse(any(e.get("type") == "ready" for e in h.events))

    async def test_final_save_failure_does_not_skip_resources_or_recurse(self):
        for route in ("websocket", "sfu"):
            with self.subTest(route=route):
                h = harness(route)
                await h.start()
                await h.turn("Hello there.", 0)
                await self.completed(h)
                acknowledged = copy.deepcopy(h.saved[-1])
                attempts = []
                async def fail(state):
                    attempts.append(state)
                    raise OSError("controlled End write failure")
                h.session.save = fail
                await asyncio.wait_for(h.session.close("ended"), 4)
                await self.settled(h)
                self.assertEqual(len(attempts), 3)
                self.assertEqual(h.session.state, acknowledged)
                self.assertEqual(h.session.context.get_messages(), acknowledged["messages"])
                self.assertEqual(h.saved[-1], acknowledged)
                await h.session.close("again")
                self.assertEqual(len(attempts), 3)

    async def test_unacknowledged_write_is_owned_without_overlapping_retry(self):
        h = Harness()
        await h.start()
        attempts, cancelled, release = [], asyncio.Event(), asyncio.Event()
        save = h.session.save
        async def stalled(state):
            attempts.append(state)
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()  # Models a remote write ignoring cancellation.
            await save(state)
        h.session.save = stalled
        try:
            with patch("conversation.PERSIST_TIMEOUT", .05):
                with self.assertRaises(ContextPersistenceError):
                    await h.session.persist()
                await self.settled(h)
            self.assertTrue(cancelled.is_set())
            self.assertEqual(len(attempts), 1)
            self.assertTrue(h.session.diagnostics()["context_persistence_uncertain"])
            self.assertEqual(h.session.diagnostics()["context_pending_writes"], 1)
            self.assertEqual(len(h.saved), 1)
            release.set()
            await wait_for(lambda: h.session.diagnostics()["context_pending_writes"] == 0,
                           "late write ownership settlement")
            self.assertEqual(len(h.saved), 2)
            self.assertTrue(h.session.closed)
            self.assertTrue(h.session.persistence_failed)
        finally:
            release.set()
            await h.close()

    async def test_cancelled_write_settles_before_replacement_snapshot(self):
        h = Harness()
        await h.start()
        save = h.session.save
        entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        active, peak, writes = 0, 0, []
        async def first_stalls(state):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            writes.append(copy.deepcopy(state))
            try:
                if len(writes) == 1:
                    entered.set()
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        cancelled.set()
                        await release.wait()
                await save(state)
            finally:
                active -= 1
        h.session.save = first_stalls
        first = asyncio.create_task(h.session.persist())
        try:
            await entered.wait()
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            await cancelled.wait()
            h.session.context.add_message({"role":"user", "content":"A newer snapshot."})
            replacement = asyncio.create_task(h.session.persist())
            await asyncio.sleep(.03)
            self.assertEqual(len(writes), 1)
            release.set()
            await replacement
            self.assertEqual(peak, 1)
            self.assertEqual(len(writes), 2)
            self.assertEqual(h.saved[-1]["messages"][-1]["content"], "A newer snapshot.")
            self.assertFalse(h.session.persistence_failed)
        finally:
            release.set()
            await h.close()


if __name__ == "__main__":
    unittest.main()
