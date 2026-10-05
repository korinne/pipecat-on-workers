"""Synthetic causal traces; no microphone, network, or model requests."""

import asyncio
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from runtime_probe import Harness, wait_for
from sfu_codec import encode_packet
from sfu_transport import SfuTransport
from smart_turn import NovaTurnCoordinator, ReadyTurnFrame
from test_sfu_transport import FakeApi, FakeSocket


class CoordinatorTrace:
    """Real coordinator with controlled leaf I/O and an unconsumed frame queue."""

    def __init__(self):
        self.metrics, self.frames, self.messages = [], [], []
        self.gate = None
        self.started = asyncio.Event()

        async def queue(frame):
            self.frames.append(frame)

        async def send(message):
            self.messages.append(message)

        self.turn = NovaTurnCoordinator(self, queue, send,
            lambda kind, values: self.metrics.append({"event": kind, **values}))

    async def start(self):
        await self.turn.connected(1)
        return self

    async def analyze_turn(self, pcm):
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        return {"is_complete": True, "probability": .9}

    async def event(self, kind, **values):
        await self.turn.event({"type": kind, "connection_generation": 1, **values})

    async def result(self, text, *, final, pause, end=.5, word_end=.4):
        await self.event("Results", start=0, duration=end, is_final=final,
            speech_final=pause, channel={"alternatives": [{"transcript": text,
                "words": [{"start": 0, "end": word_end, "word": text}]}]})

    def named(self, name):
        return [item for item in self.metrics if item["event"] == name]


class CausalCoordinatorDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejected_onset_records_raw_time_cursor_and_pre_discard_state(self):
        h = await CoordinatorTrace().start()
        secret = "PRIVATE SYNTHETIC TRANSCRIPT"
        try:
            h.turn.append_audio(bytes(16000))
            await h.event("SpeechStarted", timestamp=0)
            await h.result(secret, final=True, pause=False)
            revision = h.turn.revision
            offset = len(h.metrics)
            await h.event("SpeechStarted", timestamp=1.25)
            trace = h.metrics[offset:]
            self.assertEqual([item["event"] for item in trace],
                             ["nova_event", "nova_event_rejected", "turn_discarded"])
            self.assertEqual(trace[0]["nova_timestamp_secs"], 1.25)
            self.assertEqual(trace[0]["audio_cursor_sample"], 8000)
            self.assertEqual(trace[1]["validation"], "onset_beyond_audio")
            discarded = trace[2]
            self.assertEqual(discarded["reason"], "invalid_nova_event")
            self.assertEqual(discarded["revision"], revision)
            self.assertEqual(discarded["next_revision"], revision + 1)
            self.assertTrue(discarded["active"])
            self.assertTrue(discarded["speaking"])
            self.assertEqual(discarded["final_segments"], 1)
            self.assertEqual(discarded["transcript_chars"], len(secret))
            self.assertEqual(discarded["consumed_sample"], 0)
            self.assertFalse(h.turn.active)
            self.assertEqual(h.turn.consumed, 8000)
            self.assertEqual(len([m for m in h.messages if m["type"] == "error"]), 1)
            self.assertFalse(any(isinstance(frame, ReadyTurnFrame) for frame in h.frames))
            self.assertNotIn(secret, json.dumps(h.metrics, allow_nan=False))
        finally:
            await h.turn.close()

    async def test_invalid_numeric_timestamps_cannot_break_error_handling(self):
        # These all reached the coordinator's bounded rejection path before
        # diagnostics were added. Tracing must not raise first or retain NaN.
        for timestamp in (-.125, float("nan"), float("inf"), 10 ** 1000):
            with self.subTest(timestamp_type=type(timestamp).__name__):
                h = await CoordinatorTrace().start()
                try:
                    h.turn.append_audio(bytes(16000))
                    await h.event("SpeechStarted", timestamp=0)
                    await h.event("SpeechStarted", timestamp=timestamp)
                    self.assertEqual(len(h.named("nova_event_rejected")), 1)
                    self.assertIn(h.named("nova_event_rejected")[0]["validation"],
                                  ("timestamp", "event_shape"))
                    self.assertEqual(h.named("turn_discarded")[-1]["reason"], "invalid_nova_event")
                    self.assertFalse(h.turn.active)
                    if timestamp == -.125:
                        self.assertEqual(h.named("nova_event")[-1]["nova_timestamp_secs"], -.125)
                    json.dumps(h.metrics, allow_nan=False)
                finally:
                    await h.turn.close()

    async def test_pause_trace_distinguishes_word_audio_and_later_final_coverage(self):
        h = await CoordinatorTrace().start()
        secret = "PRIVATE QUESTION"
        try:
            h.turn.append_audio(bytes(16000))
            await h.event("SpeechStarted", timestamp=0)
            await h.result(secret, final=False, pause=True)
            await wait_for(lambda: bool(h.named("smart_turn_outcome")), "hosted fixture outcome")
            snapshot = h.named("turn_snapshot")[0]
            self.assertEqual(snapshot["transcript_end_sample"], 8000)
            self.assertEqual(snapshot["audio_start_sample"], 0)
            self.assertEqual(snapshot["audio_end_sample"], 6400)
            self.assertEqual(snapshot["pause_end_sample"], 8000)
            self.assertFalse(snapshot["transcript_covered"])
            self.assertEqual(snapshot["final_segments"], 0)
            request = h.named("smart_turn_request")[0]
            self.assertEqual(request["snapshot_samples"], 6400)
            self.assertEqual(request["request_revision"], snapshot["revision"])
            self.assertFalse(any(isinstance(frame, ReadyTurnFrame) for frame in h.frames))
            await h.result(secret, final=True, pause=False)
            finals = [item for item in h.named("nova_event") if item.get("is_final")]
            self.assertEqual(len(finals), 1)
            self.assertFalse(finals[0]["speech_final"])
            self.assertEqual(finals[0]["first_word_start_secs"], 0)
            self.assertEqual(finals[0]["last_word_end_secs"], .4)
            self.assertEqual(finals[0]["event_transcript_chars"], len(secret))
            self.assertTrue(h.turn.trace_state()["transcript_covered"])
            self.assertEqual(len([frame for frame in h.frames if isinstance(frame, ReadyTurnFrame)]), 1)
            names = [item["event"] for item in h.metrics]
            self.assertLess(names.index("turn_snapshot"), names.index("smart_turn_request"))
            self.assertLess(names.index("smart_turn_request"), names.index("smart_turn_outcome"))
            self.assertNotIn(secret, json.dumps(h.metrics, allow_nan=False))
        finally:
            await h.turn.close()

    async def test_resumption_links_cancellation_to_original_request_revision(self):
        h = await CoordinatorTrace().start()
        h.gate = asyncio.Event()
        try:
            h.turn.append_audio(bytes(16000))
            await h.event("SpeechStarted", timestamp=0)
            await h.result("PRIVATE INCOMPLETE CLAUSE", final=True, pause=True)
            await asyncio.wait_for(h.started.wait(), 1)
            revision = h.turn.revision
            h.turn.append_audio(bytes(16000))
            await h.event("SpeechStarted", timestamp=.5)
            await wait_for(lambda: any(item.get("outcome") == "cancelled"
                                      for item in h.named("smart_turn_outcome")), "cancellation trace")
            cancelled = h.named("turn_cancelled")[-1]
            self.assertTrue(cancelled["detector_cancelled"])
            self.assertTrue(cancelled["deadline_cancelled"])
            self.assertEqual(cancelled["revision"], revision + 1)
            outcome = h.named("smart_turn_outcome")[-1]
            self.assertEqual(outcome["request_revision"], revision)
            self.assertEqual(outcome["revision"], revision + 1)
            self.assertTrue(h.turn.speaking)
            self.assertFalse(any(isinstance(frame, ReadyTurnFrame) for frame in h.frames))
            self.assertNotIn("PRIVATE INCOMPLETE CLAUSE", json.dumps(h.metrics, allow_nan=False))
        finally:
            h.gate.set()
            await h.turn.close()


class CausalSessionDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_audio_trace_samples_progress_and_counts_only_forwarded_pcm(self):
        h = await Harness().start()
        original_send = h.provider.send_audio
        calls = 0

        async def alternate_forwarding(pcm):
            nonlocal calls
            calls += 1
            return await original_send(pcm) if calls % 2 else False

        h.provider.send_audio = alternate_forwarding
        try:
            for _ in range(125):
                await h.session.audio(bytes(640))
            trace = [item for item in h.session.metrics if item["event"] == "input_audio_progress"]
            self.assertEqual([item["input_audio_chunks"] for item in trace], [1, 51, 101])
            self.assertEqual([item["input_audio_bytes"] for item in trace], [640, 32640, 64640])
            self.assertEqual([item["forwarded_audio_bytes"] for item in trace], [640, 16640, 32640])
            self.assertEqual([item["audio_cursor_sample"] for item in trace], [320, 8320, 16320])
            self.assertEqual(h.session.forwarded_audio_bytes, 63 * 640)
            self.assertEqual(h.session.turn.cursor, 63 * 320)
            json.dumps(trace, allow_nan=False)
        finally:
            await h.close()

    async def test_real_event_ring_keeps_only_latest_500_sanitized_entries(self):
        h = await Harness().start()
        secret = "PRIVATE OLD CONNECTION TEXT"
        try:
            for index in range(520):
                await h.session.provider_event({"type": "Results", "connection_generation": 0,
                    "start": index, "duration": 0, "is_final": True, "speech_final": False,
                    "channel": {"alternatives": [{"transcript": secret, "words": []}]}})
            self.assertEqual(len(h.session.metrics), 500)
            self.assertEqual(h.session.metrics[0]["nova_start_secs"], 20)
            self.assertEqual(h.session.metrics[-1]["nova_start_secs"], 519)
            self.assertTrue(all(item["event"] == "nova_event" and not item["current_connection"]
                                for item in h.session.metrics))
            self.assertEqual(h.provider.generations, [])
            self.assertNotIn(secret, json.dumps(h.session.metrics, allow_nan=False))
        finally:
            await h.close()


class CausalSfuDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_packet_counters_distinguish_accepted_decoded_and_duplicate_audio(self):
        api, audio, events = FakeApi(), [], []

        async def event(message):
            events.append(message)

        transport = SfuTransport({"REALTIME_SFU_APP_ID": "fixture-app",
            "REALTIME_SFU_APP_SECRET": "PRIVATE SECRET"}, audio.append, event,
            input_endpoint="wss://fixture/input?token=PRIVATE CAPABILITY",
            output_endpoint_factory=lambda generation: "wss://fixture/output",
            request=api, clock=lambda: 0, socket_factory=FakeSocket)
        api.transport = transport
        try:
            await transport.signal("publish", {"sessionDescription": {"type": "offer", "sdp": "v=0\r\n"}, "mid": "0"})
            await transport.signal("input_ready", {})
            packet = b"\x08\x01\x10\x7b" + encode_packet(bytes(3840))
            transport.input_socket.queue.put_nowait(packet)
            transport.input_socket.queue.put_nowait(packet)
            await wait_for(lambda: transport.diagnostics()["sfu_dropped_packets"] == 1, "duplicate packet")
            info = transport.diagnostics()
            self.assertEqual(info["sfu_input_packets"], 2)
            self.assertEqual(info["sfu_input_bytes"], 7680)
            self.assertEqual(info["sfu_decoded_pcm_bytes"], 1280)
            self.assertEqual(sum(map(len, audio)), info["sfu_decoded_pcm_bytes"])
            self.assertEqual(info["sfu_last_sequence"], 1)
            self.assertEqual(info["sfu_last_packet_timestamp"], 123)
            self.assertEqual(info["sfu_input_connections"], 1)
            self.assertEqual(info["sfu_first_callback_ms"], 0)
            self.assertEqual(info["sfu_first_pcm_ms"], 0)
            self.assertTrue(info["sfu_input_pcm_ready"])
            self.assertNotIn("PRIVATE", json.dumps(info, allow_nan=False))
        finally:
            await transport.close()
