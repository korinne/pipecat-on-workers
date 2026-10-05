"""Adversarial Nova ordering through Pipecat queues; all model I/O is synthetic."""
import asyncio
import pathlib
import sys
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from runtime_probe import Harness, wait_for
from smart_turn import NovaTurnCoordinator, ReadyTurnFrame, MAX_AUDIO, MAX_BUFFER


class SmartTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_completion_waits_for_final_transcript(self):
        h = await Harness().start()
        try:
            await h.begin("", 0)
            await h.finish("interim", 0, final=False)
            await wait_for(lambda: h.session.turn.decision is not None, "detector complete")
            self.assertEqual(h.provider.generations, [])
            await h.finish("Correct final text.", 0, pause=False)
            await h.response()
            self.assertEqual(h.provider.generations[0][-1]["content"], "Correct final text.")
            self.assertEqual(next(m["probability"] for m in h.session.metrics
                                  if m["event"] == "smart_turn"), 0.9)
        finally:
            await h.close()

    async def test_reordered_final_ranges_wait_for_coverage(self):
        h = await Harness().start()
        try:
            await h.begin("", 0)
            await h.input_segment(1)
            await h.finish("second half.", 1)
            await wait_for(lambda: h.session.turn.decision is not None, "detector complete")
            self.assertEqual(h.provider.generations, [])
            await h.finish("First half and", 0, pause=False)
            await h.response()
            self.assertEqual(h.provider.generations[0][-1]["content"], "First half and second half.")
        finally:
            await h.close()

    async def test_empty_endpoint_preserves_accumulated_final_text(self):
        h = await Harness().start()
        try:
            await h.begin("", 0)
            await h.finish("Already finalized.", 0, pause=False)
            event = h.result("", 0)
            event.update(start=0.5, duration=0)
            await h.session.provider_event(event)
            await h.response()
            self.assertEqual(h.provider.generations[0][-1]["content"], "Already finalized.")
        finally:
            await h.close()

    async def test_queued_completion_is_rejected_after_resume(self):
        h = await Harness().start()
        entered, release = asyncio.Event(), asyncio.Event()
        @h.session.user.event_handler("on_before_process_frame")
        async def hold_ready(processor, frame):
            if isinstance(frame, ReadyTurnFrame) and not entered.is_set():
                entered.set()
                await release.wait()
        try:
            await h.turn("Please explain", 0)
            await asyncio.wait_for(entered.wait(), 2)
            await h.begin("this part.", 1, resume=True)
            release.set()
            await asyncio.sleep(0.02)
            self.assertEqual(h.provider.generations, [])
            self.assertFalse(any(m["role"] == "user" for m in h.session.context.get_messages()))
            await h.finish("this part.", 1)
            await h.response()
            self.assertEqual(len(h.provider.generations), 1)
            self.assertEqual(h.provider.generations[0][-1]["content"], "Please explain this part.")
        finally:
            release.set()
            await h.close()

    async def test_late_detector_result_after_cancellation_is_stale(self):
        h = await Harness().start()
        entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def ignores_first_cancel(pcm):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
            return {"is_complete": True}
        h.provider.analyze_turn = ignores_first_cancel
        try:
            await h.turn("Still thinking", 0)
            await asyncio.wait_for(entered.wait(), 2)
            await h.begin("now finished.", 1, resume=True)
            await asyncio.wait_for(cancelled.wait(), 2)
            release.set()
            await wait_for(lambda: not h.session.turn.detectors, "late detector settlement")
            self.assertEqual(h.provider.generations, [])
            await h.finish("now finished.", 1)
            await h.response()
            self.assertEqual(h.provider.generations[0][-1]["content"], "Still thinking now finished.")
        finally:
            release.set()
            await h.close()

    async def test_delayed_old_pause_cannot_stop_resumed_speech(self):
        h = await Harness().start()
        try:
            await h.begin("", 0)
            await h.begin("", 1, resume=True)
            await h.finish("Earlier words", 0)
            await asyncio.sleep(0.02)
            self.assertTrue(h.session.turn.speaking)
            self.assertEqual(h.provider.turn_checks, [])
            await h.finish("and the rest.", 1)
            await h.response()
            self.assertEqual(h.provider.generations[0][-1]["content"], "Earlier words and the rest.")
        finally:
            await h.close()

    async def test_future_transcript_invalidates_pending_pause(self):
        h = await Harness().start()
        h.provider.turn_gate = asyncio.Event()
        try:
            await h.turn("First words.", 0)
            await asyncio.wait_for(h.provider.turn_started.wait(), 2)
            await h.input_segment(1)
            await h.finish("Later words.", 1, pause=False)
            h.provider.turn_gate.set()
            await asyncio.sleep(0.02)
            self.assertEqual(h.provider.generations, [])
            self.assertTrue(any(m.get("reason") == "transcript_beyond_pause" for m in h.session.metrics))
            h.provider.turn_gate = None
            await h.turn("Fresh request.", 2)
            await h.response()
            self.assertEqual(h.provider.generations[0][-1]["content"], "Fresh request.")
        finally:
            await h.close()

    async def test_reconnect_rejects_queued_completion_and_old_events(self):
        h = await Harness().start()
        entered, release = asyncio.Event(), asyncio.Event()
        @h.session.user.event_handler("on_before_process_frame")
        async def hold_ready(processor, frame):
            if isinstance(frame, ReadyTurnFrame) and not entered.is_set():
                entered.set()
                await release.wait()
        try:
            await h.turn("Obsolete connection.", 0)
            await asyncio.wait_for(entered.wait(), 2)
            old = h.result("Obsolete connection.", 0)
            await h.provider.reconnect()
            release.set()
            await h.session.provider_event(old)
            await h.turn("Current connection.", 0)
            await h.response()
            self.assertEqual(len(h.provider.generations), 1)
            self.assertEqual(h.provider.generations[0][-1]["content"], "Current connection.")
        finally:
            release.set()
            await h.close()

    async def test_missing_final_text_times_out_without_partial_context(self):
        h = await Harness(turn_timeout_secs=0.03).start()
        try:
            await h.begin("", 0)
            await h.finish("Only interim text", 0, final=False)
            await wait_for(lambda: any(e["type"] == "error" for e in h.events), "readiness deadline")
            self.assertEqual(h.provider.generations, [])
            self.assertFalse(any(m["role"] == "user" for m in h.session.context.get_messages()))
            await h.finish("Too late final", 0)
            await asyncio.sleep(0.02)
            self.assertEqual(h.provider.generations, [])
        finally:
            await h.close()

    async def test_malformed_ranges_cannot_add_context(self):
        for kind in ("crossing", "future", "nan", "overlap"):
            with self.subTest(kind=kind):
                h = await Harness().start()
                try:
                    await h.turn("Completed request.", 0)
                    await h.response()
                    await h.begin("", 1)
                    event = h.result("Unsafe text", 1)
                    if kind == "crossing":
                        event.update(start=0.4, duration=0.6)
                    elif kind == "future":
                        event.update(duration=2)
                    elif kind == "nan":
                        event.update(start=float("nan"))
                    else:
                        await h.finish("First range", 1, pause=False)
                        event.update(start=0.75, duration=0.25)
                    await h.session.provider_event(event)
                    await asyncio.sleep(0.02)
                    self.assertEqual(len(h.provider.generations), 1)
                    self.assertTrue(any(e["type"] == "error" for e in h.events))
                    self.assertEqual([m["content"] for m in h.session.context.get_messages()
                                      if m["role"] == "user"], ["Completed request."])
                finally:
                    await h.close()

    async def test_snapshot_excludes_audio_after_transcript_cursor_and_bounds_memory(self):
        snapshots, frames = [], []
        class Provider:
            async def analyze_turn(self, pcm):
                snapshots.append(pcm)
                return {"is_complete": True}
        async def queue(frame): frames.append(frame)
        async def send(event): pass
        c = NovaTurnCoordinator(Provider(), queue, send, lambda *a: None)
        try:
            await c.connected(1)
            await c.event({"type":"SpeechStarted", "timestamp":0, "connection_generation":1})
            c.append_audio(b'\x01\x00' * 8000 + b'\x02\x00' * 8000)
            await c.event({"type":"Results", "start":0, "duration":0.5, "is_final":True,
                           "speech_final":True, "channel":{"alternatives":[{"transcript":"done"}]},
                           "connection_generation":1})
            await wait_for(lambda: bool(snapshots), "snapshot")
            self.assertEqual(snapshots, [b'\x01\x00' * 8000])
            c.append_audio(bytes(MAX_AUDIO * 2))
            self.assertEqual(len(c.audio), MAX_BUFFER)
        finally:
            await c.close()

if __name__ == "__main__":
    unittest.main()
