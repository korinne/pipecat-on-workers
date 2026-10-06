"""Real SFU ownership and conversion with controlled REST and socket leaves."""
import asyncio
import copy

from conversation import ConversationSession
from runtime_probe import FixtureProviders, Harness
from sfu_codec import decode_packet
from sfu_transport import SfuTransport


class MediaSocket:
    def __init__(self, generation, role, max_bytes, *, submitted):
        self.generation = generation
        self.closed = False
        self.submitted = submitted

    def send(self, packet):
        if self.closed:
            raise RuntimeError("Synthetic media socket is closed")
        pcm = decode_packet(packet)[2]
        if pcm:
            self.submitted.append((pcm, self.generation))

    async def receive(self, timeout=30):
        await asyncio.Future()

    def close(self):
        self.closed = True


class CleanupHarness(Harness):
    def __init__(self):
        self.events, self.saved, self.timeline = [], [], []
        self.cleanup_entered, self.cleanup_release = asyncio.Event(), asyncio.Event()
        self.cleanup_release.set()
        self.allocations = 0
        self.remote_tracks = {}
        self.submitted = []

        async def send(event):
            self.events.append(copy.deepcopy(event))
            self.timeline.append((event["type"], event.get("generation")))

        async def save(state):
            self.saved.append(copy.deepcopy(state))

        async def request(url, method, payload, secret):
            if url.endswith("adapters/websocket/close"):
                self.cleanup_entered.set()
                await self.cleanup_release.wait()
                return 200, {"tracks": payload["tracks"]}
            if url.endswith("adapters/websocket/new"):
                self.allocations += 1
                track = payload["tracks"][0]
                generation = int(track["trackName"].split("-")[-1])
                self.transport.attach_socket("output", generation, generation)
                return 200, {"tracks": [{"adapterId": f"adapter-{self.allocations}",
                                         "sessionId": f"publisher-{self.allocations}"}]}
            if url.endswith("sessions/new"):
                self.allocations += 1
                return 200, {"sessionId": f"receiver-{self.allocations}"}
            if url.endswith("tracks/new"):
                session = url.split("/sessions/")[1].split("/")[0]
                self.remote_tracks[session] = [{"mid": "0", "status": "active"}]
                return 200, {"tracks": [{"mid": "0"}], "requiresImmediateRenegotiation": True,
                             "sessionDescription": {"type": "offer", "sdp": "v=0\r\n"}}
            if url.endswith("renegotiate"):
                return 200, {}
            if url.endswith("tracks/close"):
                session = url.split("/sessions/")[1].split("/")[0]
                self.remote_tracks[session] = []
                return 200, {"tracks": payload["tracks"]}
            if method == "GET":
                return 200, {"tracks": self.remote_tracks.get(url.rsplit("/", 1)[1], [])}
            raise AssertionError(f"Unexpected synthetic SFU request: {method}")

        async def event(message):
            await send(message)
            if message.get("type") == "sfu_track":
                generation = message["generation"]
                await self.transport.signal("subscribe", {"generation": generation})
                await self.transport.signal("renegotiate", {"generation": generation, "role": "output",
                    "sessionDescription": {"type": "answer", "sdp": "v=0\r\n"}})
                await self.transport.signal("playback_ready", {"generation": generation})

        self.transport = SfuTransport({"REALTIME_SFU_APP_ID": "fixture", "REALTIME_SFU_APP_SECRET": "fixture"},
            lambda pcm: None, event, input_endpoint="wss://fixture/input",
            output_endpoint_factory=lambda generation: f"wss://fixture/output/{generation}", request=request,
            socket_factory=lambda ws, role, max_bytes: MediaSocket(ws, role, max_bytes, submitted=self.submitted))
        self.transport.sent = self.submitted

        def factory(callback):
            self.provider = FixtureProviders(callback)
            return self.provider

        self.session = ConversationSession({}, factory, send, save, audio_transport=self.transport)

    async def close(self):
        self.cleanup_release.set()
        return await super().close()
