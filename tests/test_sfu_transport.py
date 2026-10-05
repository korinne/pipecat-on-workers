import asyncio
import pathlib
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from sfu_codec import decode_packet, encode_packet
from sfu_transport import SfuError, SfuTransport


class FakeSocket:
    def __init__(self, ws, role, max_bytes):
        self.role = role
        self.closed = False
        self.sent = []
        self.queue = asyncio.Queue()

    def send(self, data):
        if self.closed:
            raise RuntimeError("closed")
        self.sent.append(data)

    async def receive(self, timeout=30):
        return await self.queue.get()

    def close(self):
        self.closed = True


class FakeApi:
    def __init__(self):
        self.calls = []
        self.sessions = 0
        self.adapters = 0
        self.transport = None
        self.close_status = 200
        self.hold_output = None
        self.hold_cleanup = None
        self.cleanup_started = asyncio.Event()
        self.output_started = asyncio.Event()
        self.session_tracks = {}

    async def __call__(self, url, method, payload, secret):
        self.calls.append((method, url, payload))
        if url.endswith("sessions/new"):
            self.sessions += 1
            self.session_tracks[f"session-{self.sessions}"] = []
            return 201, {"sessionId": f"session-{self.sessions}"}
        if url.endswith("tracks/new"):
            track = payload["tracks"][0]
            is_input = track["location"] == "local"
            session = url.split("/sessions/")[1].split("/")[0]
            self.session_tracks[session] = [{"mid": "0", "status": "active"}]
            return 200, {"sessionDescription": {"type": "answer" if is_input else "offer", "sdp": "v=0\r\n"},
                         "requiresImmediateRenegotiation": not is_input,
                         "tracks": [{"mid": "0", "trackName": track["trackName"]}]}
        if url.endswith("renegotiate"):
            return 200, {}
        if url.endswith("adapters/websocket/new"):
            track = payload["tracks"][0]
            self.adapters += 1
            result = {"adapterId": f"adapter-{self.adapters}", "endpoint": track["endpoint"],
                      "trackName": track["trackName"]}
            if track["location"] == "local":
                self.output_started.set()
                generation = int(track["trackName"].split("-")[-1])
                if self.hold_output:
                    await self.hold_output.wait()
                if (not self.transport.closed and self.transport.output
                        and self.transport.output["generation"] == generation
                        and not self.transport.output["retired"]):
                    self.transport.attach_socket("output", object(), generation)
                result["sessionId"] = f"publisher-{self.adapters}"
            else:
                self.transport.attach_socket("input", object())
            return 200, {"tracks": [result]}
        if url.endswith("adapters/websocket/close"):
            self.cleanup_started.set()
            if self.hold_cleanup:
                await self.hold_cleanup.wait()
            if self.close_status != 200:
                return self.close_status, {"tracks": [{"adapterId": payload["tracks"][0]["adapterId"],
                                                       "errorCode": "adapter_not_found" if self.close_status == 503 else "unavailable"}]}
            return 200, {"tracks": payload["tracks"]}
        if url.endswith("tracks/close"):
            session = url.split("/sessions/")[1].split("/")[0]
            self.session_tracks[session] = []
            return 200, {"tracks": payload["tracks"]}
        if method == "GET" and "/sessions/" in url:
            return 200, {"tracks": self.session_tracks.get(url.rsplit("/", 1)[1], [])}
        raise AssertionError(f"Unexpected request {method} {url}")


class SfuTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api = FakeApi()
        self.events = []
        self.audio = []
        self.clock = 0
        self.sleeps = []

        async def sleep(delay):
            self.sleeps.append(delay)
            self.clock += delay
            await asyncio.sleep(0)

        async def event(message):
            self.events.append(message)

        self.transport = SfuTransport({"REALTIME_SFU_APP_ID": "app", "REALTIME_SFU_APP_SECRET": "private-secret"},
            self.audio.append, event, input_endpoint="wss://example/input?token=private-input",
            output_endpoint_factory=lambda generation: f"wss://example/output/{generation}?token=private-output",
            request=self.api, clock=lambda: self.clock, sleep=sleep, socket_factory=FakeSocket)
        self.api.transport = self.transport

    async def asyncTearDown(self):
        if self.api.hold_cleanup:
            self.api.hold_cleanup.set()
        if self.api.hold_output:
            self.api.hold_output.set()
        await self.transport.close()

    async def cleanup_settled(self):
        for _ in range(200):
            task = self.transport.cleanup_task
            if not self.transport.requests and not self.transport.cleanup_requests and (not task or task.done()):
                return
            await asyncio.sleep(.001)
        self.fail("cleanup did not settle")

    async def publish(self):
        result = await self.transport.signal("publish", {"sessionDescription": {"type": "offer", "sdp": "v=0\r\n"}, "mid": "0"})
        self.assertEqual(result["sessionDescription"]["type"], "answer")
        self.assertEqual(self.transport.diagnostics()["sfu_owned_adapters"], 0)
        await self.transport.signal("input_ready", {})

    async def ready_output(self, generation):
        for _ in range(30):
            if any(event.get("type") == "sfu_track" and event.get("generation") == generation for event in self.events):
                break
            await asyncio.sleep(0)
        else:
            self.fail("output publication not announced")
        result = await self.transport.signal("subscribe", {"generation": generation})
        self.assertEqual(result["sessionDescription"]["type"], "offer")
        await self.transport.signal("renegotiate", {"role": "output", "generation": generation,
            "sessionDescription": {"type": "answer", "sdp": "v=0\r\n"}})
        result = await self.transport.signal("playback_ready", {"generation": generation})
        self.assertFalse(result["deliveryConfirmed"])

    async def test_publish_waits_for_input_ready_and_decodes_audio(self):
        await self.publish()
        socket = self.transport.input_socket
        packet = encode_packet(struct.pack("<hh", 1000, 1000) * 960)
        socket.queue.put_nowait(b"\x08\x01" + packet)
        socket.queue.put_nowait(b"\x08\x01" + packet)
        for _ in range(5):
            await asyncio.sleep(0)
        self.assertEqual(len(self.audio), 1)
        self.assertEqual(len(self.audio[0]), 640)
        self.assertEqual(self.transport.diagnostics()["sfu_dropped_packets"], 1)
        self.assertEqual(self.transport.diagnostics()["sfu_input_bytes"], 3840)

    async def test_full_output_is_paced_and_never_emits_a_played_receipt(self):
        sending = asyncio.create_task(self.transport.send_audio(struct.pack("<h", 4000) * 2400, 2))
        await self.ready_output(2)
        self.assertTrue(await sending)
        socket = self.transport.output["socket"]
        self.assertEqual(len(socket.sent), 5)
        self.assertEqual([len(decode_packet(packet)[2]) for packet in socket.sent], [3840] * 5)
        self.assertEqual(len(self.sleeps), 4)
        self.assertAlmostEqual(sum(self.sleeps), .08)
        self.assertEqual(self.transport.diagnostics()["sfu_submitted_bytes"], 19200)
        self.assertFalse(self.transport.diagnostics()["sfu_delivery_confirmed"])
        serialized = str(self.events)
        self.assertNotIn("private-", serialized)
        self.assertNotIn("played", serialized)
        self.assertTrue(await self.transport.finish_generation(2))
        self.assertEqual(decode_packet(socket.sent[-1])[2], b"")
        self.assertAlmostEqual(sum(self.sleeps), .1)
        self.assertFalse(await self.transport.finish_generation(2))
        self.assertFalse(await self.transport.send_audio(bytes(960), 2))

    async def test_interrupt_retires_old_adapter_and_receiver(self):
        sending = asyncio.create_task(self.transport.send_audio(bytes(4800), 2))
        await self.ready_output(2)
        await sending
        old_socket = self.transport.output["socket"]
        await self.transport.clear(3)
        self.assertTrue(old_socket.closed)
        await self.cleanup_settled()
        self.assertEqual(self.transport.diagnostics()["sfu_owned_adapters"], 0)
        self.assertEqual(self.transport.diagnostics()["sfu_owned_tracks"], 0)
        self.assertFalse(await self.transport.send_audio(bytes(960), 2))
        with self.assertRaises(SfuError):
            self.transport.attach_socket("output", object(), 2)
        next_send = asyncio.create_task(self.transport.send_audio(bytes(960), 4))
        await self.ready_output(4)
        await next_send
        self.assertIsNot(self.transport.output["socket"], old_socket)
        self.assertEqual(self.api.sessions, 2)

    async def test_late_allocation_after_interrupt_is_closed(self):
        self.api.hold_output = asyncio.Event()
        sending = asyncio.create_task(self.transport.send_audio(bytes(960), 2))
        await self.api.output_started.wait()
        await self.transport.clear(3)
        self.api.hold_output.set()
        self.assertFalse(await sending)
        await self.cleanup_settled()
        self.assertEqual(self.transport.diagnostics()["sfu_owned_adapters"], 0)
        self.assertFalse(any(event["type"] == "sfu_track" for event in self.events))

    async def test_close_waits_for_late_allocation_and_cleans_it(self):
        self.api.hold_output = asyncio.Event()
        sending = asyncio.create_task(self.transport.send_audio(bytes(960), 2))
        await self.api.output_started.wait()
        closing = asyncio.create_task(self.transport.close())
        await asyncio.sleep(0)
        self.api.hold_output.set()
        await closing
        await asyncio.gather(sending, return_exceptions=True)
        self.assertEqual(self.transport.diagnostics()["sfu_owned_adapters"], 0)
        self.assertEqual(self.transport.diagnostics()["sfu_pending_requests"], 0)

    async def test_already_closed_adapter_is_absent_but_other_failure_retained(self):
        self.transport.adapters["a"] = ("input", None)
        self.api.close_status = 500
        await self.transport.close()
        self.assertIn("a", self.transport.adapters)
        closes = [call for call in self.api.calls if call[1].endswith("adapters/websocket/close")]
        self.assertEqual(len(closes), 3)
        await self.transport.close()
        self.assertEqual(len([call for call in self.api.calls if call[1].endswith("adapters/websocket/close")]), 3)
        self.transport.adapters["already-absent"] = ("input", None)
        self.api.close_status = 503
        await self.transport.close()
        self.assertNotIn("already-absent", self.transport.adapters)
        self.assertIn("a", self.transport.adapters)
        self.assertTrue(self.transport.diagnostics()["sfu_cleanup_unresolved"])

    async def test_signaling_rejects_unowned_resource_and_early_ready(self):
        with self.assertRaises(SfuError):
            await self.transport.signal("input_ready", {})
        with self.assertRaises(SfuError):
            await self.transport.signal("subscribe", {"generation": 1, "sessionId": "not-owned"})
        sending = asyncio.create_task(self.transport.send_audio(bytes(960), 2))
        for _ in range(20):
            await asyncio.sleep(0)
        with self.assertRaises(SfuError):
            await self.transport.signal("playback_ready", {"generation": 2})
        await self.ready_output(2)
        await sending

    async def test_cleanup_discovers_track_whose_allocation_response_was_lost(self):
        self.transport.sessions["known-session"] = ("input", None)
        self.api.session_tracks["known-session"] = [{"mid": "7", "status": "active"}]
        await self.transport.close()
        self.assertEqual(self.api.session_tracks["known-session"], [])
        self.assertEqual(self.transport.diagnostics()["sfu_owned_sessions"], 0)
        self.assertEqual(self.transport.diagnostics()["sfu_owned_tracks"], 0)

    async def test_queued_publish_does_not_allocate_after_close(self):
        await self.transport.input_lock.acquire()
        publishing = asyncio.create_task(self.transport.signal("publish", {
            "sessionDescription": {"type": "offer", "sdp": "v=0\r\n"}, "mid": "0"}))
        await asyncio.sleep(0)
        await self.transport.close()
        self.transport.input_lock.release()
        with self.assertRaisesRegex(SfuError, "ended"):
            await publishing
        self.assertEqual(self.api.calls, [])

    async def test_output_ready_timeout_retires_allocated_resources(self):
        with patch("sfu_transport.READY_TIMEOUT", .001):
            with self.assertRaisesRegex(SfuError, "timed out"):
                await self.transport.send_audio(bytes(960), 2)
        self.assertIsNone(self.transport.output)
        await self.cleanup_settled()
        self.assertEqual(self.transport.diagnostics()["sfu_owned_adapters"], 0)

    async def test_lost_creation_response_remains_explicitly_unconfirmed(self):
        async def failing_request(*args):
            raise SfuError("Realtime connection timed out")

        self.transport.request = failing_request
        with self.assertRaisesRegex(SfuError, "timed out"):
            await self.transport.signal("publish", {
                "sessionDescription": {"type": "offer", "sdp": "v=0\r\n"}, "mid": "0"})
        self.assertEqual(self.transport.diagnostics()["sfu_unconfirmed_allocations"], 1)

    async def test_held_cleanup_does_not_block_new_output_or_allow_stale_media(self):
        sending = asyncio.create_task(self.transport.send_audio(bytes(960), 2))
        await self.ready_output(2)
        await sending
        old_socket = self.transport.output["socket"]
        self.api.hold_cleanup = asyncio.Event()
        await asyncio.wait_for(self.transport.clear(3), .1)
        await self.api.cleanup_started.wait()
        self.assertTrue(old_socket.closed)
        self.assertFalse(await self.transport.send_audio(bytes(960), 2))
        next_send = asyncio.create_task(self.transport.send_audio(bytes(960), 4))
        await self.ready_output(4)
        self.assertTrue(await asyncio.wait_for(next_send, .1))
        self.assertEqual(len(old_socket.sent), 1)
        self.assertGreater(self.transport.diagnostics()["sfu_owned_adapters"], 1)
        self.api.hold_cleanup.set()
        await self.cleanup_settled()
        self.assertEqual(len(self.transport.adapters), 1)

    async def test_end_bounds_wait_and_late_cleanup_keeps_its_owner(self):
        sending = asyncio.create_task(self.transport.send_audio(bytes(960), 2))
        await self.ready_output(2)
        await sending
        self.api.hold_cleanup = asyncio.Event()
        with patch("sfu_transport.CLOSE_TIMEOUT", .01), patch("sfu_transport.CLEANUP_REQUEST_TIMEOUT", .005):
            await asyncio.wait_for(self.transport.close(), .1)
        self.assertTrue(self.transport.closed)
        self.assertTrue(self.transport.cleanup_pending())
        checkpoint = self.transport.cleanup_checkpoint()
        self.assertTrue(checkpoint["adapters"])
        self.assertGreaterEqual(checkpoint["pending_cleanup_requests"], 1)
        self.assertNotIn("private", str(checkpoint))
        self.api.hold_cleanup.set()
        await self.cleanup_settled()
        self.assertFalse(self.transport.cleanup_pending())

    async def test_repeated_interruptions_bound_retained_remote_resources(self):
        self.api.close_status = 500
        with patch("sfu_transport.MAX_OWNED_RESOURCES", 6):
            for generation in (2, 4, 6):
                sending = asyncio.create_task(self.transport.send_audio(bytes(960), generation))
                await self.ready_output(generation)
                await sending
                await self.transport.clear(generation + 1)
                await self.cleanup_settled()
            self.assertEqual(len(self.transport.adapters), 3)
            # Exhausted owners remain explicit and eventually prevent creating
            # an unlimited sequence of replacements when cleanup stays broken.
            self.transport.adapters.update({f"retained-{i}": ("output", 0) for i in range(3)})
            before = len([call for call in self.api.calls if call[1].endswith("/new")])
            with self.assertRaisesRegex(SfuError, "resources remain unresolved"):
                await self.transport.send_audio(bytes(960), 8)
            self.assertEqual(len([call for call in self.api.calls if call[1].endswith("/new")]), before)
        self.api.close_status = 200

    async def test_api_timeout_retains_late_allocation_and_reconciles_after_end(self):
        self.api.hold_output = asyncio.Event()
        with patch("sfu_transport.API_TIMEOUT", .005):
            with self.assertRaisesRegex(SfuError, "status is pending"):
                await self.transport.send_audio(bytes(960), 2)
        with patch("sfu_transport.CLOSE_TIMEOUT", .005):
            await self.transport.close()
        self.assertEqual(self.transport.diagnostics()["sfu_pending_requests"], 1)
        self.api.hold_output.set()
        await self.cleanup_settled()
        self.assertFalse(self.transport.cleanup_pending())

    async def test_failed_close_and_unknown_allocation_survive_checkpoint(self):
        self.transport.adapters["owned"] = ("output", 1)
        self.transport.unconfirmed_allocations = 1
        self.api.close_status = 500
        await self.transport.close()
        checkpoint = self.transport.cleanup_checkpoint()
        self.assertEqual(checkpoint["adapters"], ["owned"])
        self.assertEqual(checkpoint["unconfirmed_allocations"], 1)
        self.assertFalse(self.transport.cleanup_requests)
        self.assertLessEqual(len(self.api.calls), 3)

    async def test_pending_allocations_are_bounded_and_owned_until_settlement(self):
        release = asyncio.Event()
        original = self.api

        async def held(url, method, payload, secret):
            if url.endswith("sessions/new"):
                await release.wait()
            return await original(url, method, payload, secret)

        self.transport.request = held
        with patch("sfu_transport.API_TIMEOUT", .001):
            for _ in range(8):
                with self.assertRaisesRegex(SfuError, "status is pending"):
                    await self.transport._api("POST", "sessions/new", record=lambda body:
                        self.transport._record_session("output", 1, body))
            with self.assertRaisesRegex(SfuError, "resources remain unresolved"):
                await self.transport._api("POST", "sessions/new")
        self.assertEqual(self.transport.diagnostics()["sfu_pending_requests"], 8)
        await self.transport.clear(2)
        release.set()
        await self.cleanup_settled()
        self.assertFalse(self.transport.cleanup_pending())
        self.assertEqual(self.api.sessions, 8)

    async def test_final_retry_drains_result_that_settles_during_session_inspection(self):
        calls = {"adapter": 0, "session": 0}
        release = asyncio.Event()

        async def request(url, method, payload, secret):
            if url.endswith("adapters/websocket/close"):
                calls["adapter"] += 1
                if calls["adapter"] < 3:
                    return 500, {}
                await release.wait()
                return 200, {"tracks": [{"adapterId": "owned-adapter"}]}
            calls["session"] += 1
            if calls["session"] < 3:
                return 500, {}
            release.set()
            await asyncio.sleep(.001)
            return 200, {"tracks": []}

        self.transport.request = request
        self.transport.adapters["owned-adapter"] = ("output", 1)
        self.transport.sessions["owned-session"] = ("output", 1)
        with patch("sfu_transport.CLEANUP_REQUEST_TIMEOUT", .005):
            await self.transport.clear(2)
            await self.cleanup_settled()
        self.assertEqual(self.transport.adapters, {})
        self.assertEqual(self.transport.cleanup_results, {})
        self.assertEqual(calls, {"adapter": 3, "session": 3})


if __name__ == "__main__":
    unittest.main()
