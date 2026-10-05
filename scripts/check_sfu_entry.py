"""Actual Worker/DO routing with fake SDK and media I/O; no real credentials."""
import asyncio
import hashlib
import json
from types import SimpleNamespace as N
import unittest

from check_access_auth import entry, Request, Response, Conversations


class Socket:
    def __init__(self): self.readyState = 1; self.closes = []
    def close(self, code=1000, reason=""):
        self.closes.append((code, reason)); self.readyState = 3
    def send(self, message): pass


class Pair:
    last = None
    @classmethod
    def new(cls):
        cls.last = (Socket(), Socket())
        return N(object_values=lambda: cls.last)


class Bridge:
    def __init__(self): self.attached = []; self.generation = 2; self.closed = False
    async def close(self): self.closed = True
    def cleanup_pending(self): return False
    def cleanup_checkpoint(self): return {}
    def diagnostics(self): return {"sfu_cleanup_tasks": 0}
    def attach_socket(self, role, socket, generation):
        if role == "output" and generation != self.generation:
            raise entry.SfuError("That assistant audio generation has ended")
        self.attached.append((role, socket, generation))
    async def signal(self, action, payload): return {"ok": True}


class SfuEntryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.saved = []
        async def put(key, value): self.saved.append(value)
        self.obj = entry.Conversation(N(storage=N(put=put)), N())
        self.token = "synthetic-browser-capability"
        self.sid = "a" * 32
        self.obj.state = {"id": self.sid, "token_hash": hashlib.sha256(self.token.encode()).hexdigest(), "status": "active"}
        self.obj.sfu = Bridge()
        self.obj.media_secret = b"test-media-secret-do-not-use" * 2
        self.obj.session = N(closed=False)
        self.base = f"https://voice.example/api/session/{self.sid}"
        self.old_pair, entry.WebSocketPair = entry.WebSocketPair, Pair

    async def asyncTearDown(self): entry.WebSocketPair = self.old_pair

    def media_url(self, suffix="input"):
        path = f"/api/session/{self.sid}/media/{suffix}"
        return "https://voice.example" + path + "?media_token=" + self.obj.media_mac(path)

    async def media(self, url):
        return await self.obj.fetch(Request(url, headers={"Upgrade": "websocket"}))

    async def test_media_capability_is_separate_and_scoped_to_exact_path(self):
        result = await self.media(self.media_url())
        self.assertEqual(result.status, 101)
        for url in (self.base + "/media/input?token=" + self.token,
                    self.base + "/media/input?media_token=" + self.token,
                    self.media_url().replace("/input?", "/output/2?"),
                    self.media_url().replace(self.sid, "b" * 32)):
            self.assertEqual((await self.media(url)).status, 403)
        self.assertEqual(len(self.obj.sfu.attached), 1)
        self.assertEqual((await self.media(self.media_url("output/2"))).status, 101)

    async def test_media_capability_expires_on_shutdown_and_rotation(self):
        previous = self.media_url()
        self.obj.media_secret = b"new-test-secret" * 3
        self.assertEqual((await self.media(previous)).status, 403)
        fresh = self.media_url()
        self.obj.session = None
        await self.obj.shutdown("ended", recoverable=False)
        self.assertEqual((await self.media(fresh)).status, 410)
        self.assertIsNone(self.obj.media_secret)

    async def test_stale_generation_media_is_rejected(self):
        result = await self.media(self.media_url("output/1"))
        self.assertEqual(result.status, 410)
        self.assertEqual(self.obj.sfu.attached, [])
        self.assertTrue(all(socket.closes for socket in Pair.last))

    async def test_invalid_sfu_configuration_fails_before_accepting_control_socket(self):
        self.obj.state["transport"] = "webrtc"
        self.obj.sfu = None
        Pair.last = None
        with self.assertRaises(entry.SfuError):
            await self.obj.fetch(Request(self.base + "?token=" + self.token,
                                         headers={"Upgrade": "websocket"}))
        self.assertIsNone(Pair.last)
        self.assertIsNone(self.obj.socket)
        self.assertEqual(self.obj.listeners, [])

    async def test_signaling_and_media_callbacks_do_not_take_control_lock(self):
        async def signal(action, payload):
            response = await self.media(self.media_url())
            self.assertEqual(response.status, 101)
            return {"ok": True}
        self.obj.sfu.signal = signal
        await self.obj.lock.acquire()
        try:
            response = await asyncio.wait_for(self.obj.fetch(Request(self.base + "/sfu", method="POST",
                headers={"X-Session-Token": self.token}, body=json.dumps({"action": "input_ready"}))), .2)
            self.assertEqual(response.status, 200)
        finally:
            self.obj.lock.release()

    async def test_signaling_requires_browser_capability_and_small_json(self):
        for headers, body, expected in (({}, "{}", 403),
            ({"X-Session-Token": self.token}, "[]", 400),
            ({"X-Session-Token": self.token}, "x" * 70001, 400)):
            response = await self.obj.fetch(Request(self.base + "/sfu", method="POST", headers=headers, body=body))
            self.assertEqual(response.status, expected)

    async def test_missing_sfu_config_does_not_allocate_conversation(self):
        conversations = Conversations()
        worker = entry.Default(None, N(CONVERSATIONS=conversations))
        response = await worker.fetch(Request("https://voice.example/api/session", method="POST",
            body=json.dumps({"transport": "webrtc"})))
        self.assertEqual(response.status, 503)
        self.assertEqual(json.loads(response.body)["code"], "sfu_not_configured")
        self.assertEqual(conversations.lookups, 0)

    async def test_unknown_transport_does_not_allocate_conversation(self):
        conversations = Conversations()
        worker = entry.Default(None, N(CONVERSATIONS=conversations))
        response = await worker.fetch(Request("https://voice.example/api/session", method="POST",
            body=json.dumps({"transport": "anything"})))
        self.assertEqual(response.status, 400)
        self.assertEqual(conversations.lookups, 0)

    async def test_arbitrary_browser_resource_ids_are_not_used_by_real_transport(self):
        calls = []
        async def api(url, method, payload, secret):
            calls.append((url, method, payload))
            if url.endswith("sessions/new"):
                return 201, {"sessionId": "owned-session"}
            if url.endswith("tracks/new"):
                return 200, {"sessionDescription": {"type": "answer", "sdp": "v=0\r\n"},
                    "tracks": [{"mid": "0", "trackName": "microphone"}]}
            if method == "GET": return 200, {"tracks": []}
            return 200, {"tracks": payload["tracks"]}

        bridge = entry.SfuTransport(N(REALTIME_SFU_APP_ID="owned-app", REALTIME_SFU_APP_SECRET="test-secret"),
            lambda pcm: None, lambda message: None, input_endpoint="wss://voice.example/owned-input",
            output_endpoint_factory=lambda generation: "wss://voice.example/owned-output", request=api)
        self.obj.sfu = bridge
        try:
            response = await self.obj.fetch(Request(self.base + "/sfu", method="POST",
                headers={"X-Session-Token": self.token}, body=json.dumps({"action": "publish",
                    "sessionDescription": {"type": "offer", "sdp": "v=0\r\n"}, "mid": "0",
                    "sessionId": "attacker-session", "appId": "attacker-app",
                    "trackName": "attacker-track", "endpoint": "wss://attacker.example/"})))
            self.assertEqual(response.status, 200)
            self.assertEqual(len(calls), 2)
            self.assertNotIn("attacker", str(calls))
            self.assertIn("/sessions/owned-session/tracks/new", calls[-1][0])
            self.assertEqual(calls[-1][2]["tracks"], [{"location": "local", "mid": "0", "trackName": "microphone"}])
        finally:
            await bridge.close()

    async def test_control_websocket_rejects_audio_and_played_in_sfu_mode(self):
        for kind in ("audio", "played"):
            received = []
            class Session:
                closed = False
                began = entry.time.monotonic()
                async def start(self): pass
                async def audio(self, pcm): received.append(pcm)
                async def played(self, generation, chunk): received.append((generation, chunk))
                async def close(self, reason): self.closed = True
                def diagnostics(self): return {"closed": self.closed}
            obj = entry.Conversation(self.obj.ctx, N())
            obj.state = dict(self.obj.state)
            obj.sfu, obj.session, obj.socket = Bridge(), Session(), Socket()
            obj.incoming = asyncio.Queue()
            obj.incoming.put_nowait({"type": kind, "generation": 2, "chunk_id": 1, "data": "AAAA", "sample_rate": 16000})
            await asyncio.wait_for(obj.run(), .2)
            self.assertEqual(received, [])
            self.assertIsNone(obj.session)

    async def test_retired_transport_remains_owned_until_late_cleanup_settles(self):
        class PendingBridge(Bridge):
            def __init__(self):
                super().__init__()
                self.pending = True
            def cleanup_pending(self): return self.pending
            def cleanup_checkpoint(self):
                return {"adapters": ["owned-adapter"], "tracks": [], "sessions": [],
                        "pending_requests": 1, "pending_cleanup_requests": 0,
                        "unconfirmed_allocations": 0}
        bridge = self.obj.sfu = PendingBridge()
        self.obj.state["transport"] = "webrtc"
        self.obj.session = None
        await self.obj.shutdown("disconnect", recoverable=True)
        self.assertTrue(bridge.closed)
        self.assertEqual(self.obj.retired_transports, [bridge])
        self.assertEqual(self.obj.state["sfu_cleanup"][0]["adapters"], ["owned-adapter"])
        self.assertEqual(self.obj.diagnostics()["sfu_pending_requests"], 1)
        bridge.pending = False
        self.obj.cleanup_changed(bridge)
        await self.obj.cleanup_save_task
        self.assertEqual(self.obj.diagnostics()["sfu_pending_requests"], 0)
        self.assertFalse(self.obj.diagnostics()["sfu_cleanup_unresolved"])
        self.assertEqual(self.obj.state["sfu_cleanup"], [])

    async def test_restarted_object_preserves_unknown_cleanup_at_expiry(self):
        record = {"adapters": ["old-adapter"], "tracks": [], "sessions": [],
                  "pending_requests": 1, "unconfirmed_allocations": 0}
        state = dict(self.obj.state, transport="webrtc", status="disconnected", sfu_cleanup=[record])
        alarms, deleted = [], []
        async def get(key): return json.dumps(state)
        async def set_alarm(value): alarms.append(value)
        async def delete_all(): deleted.append(True)
        obj = entry.Conversation(N(storage=N(get=get, setAlarm=set_alarm, deleteAll=delete_all)), N())
        await obj.alarm()
        self.assertTrue(alarms)
        self.assertEqual(deleted, [])
        restored = obj.diagnostics()["sfu_cleanup_records"][0]
        self.assertEqual(restored["adapters"], record["adapters"])
        self.assertEqual(restored["runtime_status"], "owner_restarted")
        self.assertEqual(restored["unsettled_requests_at_restart"], 1)
        self.assertEqual(obj.diagnostics()["sfu_pending_requests"], 0)
        self.assertTrue(obj.diagnostics()["sfu_cleanup_unresolved"])

    async def test_unresolved_connection_limit_blocks_reconnect_before_allocation(self):
        self.obj.session = self.obj.sfu = None
        self.obj.state["transport"] = "webrtc"
        self.obj.saved_cleanup = [{"adapters": [f"owned-{index}"]} for index in range(8)]
        result = await self.obj.fetch(Request(self.base + "?token=" + self.token, headers={"Upgrade": "websocket"}))
        self.assertEqual(result.status, 503)
        self.assertIsNone(self.obj.socket)

    async def test_cleanup_persistence_failure_is_visible_without_forgetting_ids(self):
        async def fail_put(key, value): raise RuntimeError("storage unavailable")
        self.obj.ctx.storage.put = fail_put
        self.obj.session = None
        self.obj.saved_cleanup = [{"adapters": ["owned-after-failure"]}]
        self.obj.cleanup_changed(self.obj.sfu)
        await self.obj.cleanup_save_task
        diagnostics = self.obj.diagnostics()
        self.assertTrue(diagnostics["sfu_cleanup_persistence_failed"])
        self.assertEqual(diagnostics["sfu_cleanup_records"][0]["adapters"], ["owned-after-failure"])


if __name__ == "__main__": unittest.main()
