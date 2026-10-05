"""One live Pipecat pipeline per Python Durable Object. No container or server."""
import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import secrets
import time
import platform
import pyodide
from urllib.parse import urlparse, parse_qs
from js import WebSocketPair
from pyodide.ffi import create_proxy
from workers import WorkerEntrypoint, DurableObject, Response, Request
from loguru import logger
import sys
logger.remove()
logger.add(sys.stderr, level="WARNING")
from conversation import ConversationSession
from providers import WorkersProviders, ProviderConnectionError
from sfu_transport import SfuTransport, SfuError, configured as sfu_configured


def reply(value, status=200):
    return Response(json.dumps(value), status=status, headers={"Content-Type": "application/json", "Cache-Control": "no-store"})


def enabled(env, name):
    return str(getattr(env, name, "false")).lower() == "true"


async def json_body(request, limit):
    """Reject oversized signaling before parsing or allocating SFU resources."""
    if int(request.headers.get("Content-Length", "0") or "0") > limit:
        raise ValueError("Request body is too large")
    raw = await request.text()
    if len(raw.encode("utf-8")) > limit:
        raise ValueError("Request body is too large")
    value = json.loads(raw) if raw else {}
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        path = urlparse(request.url).path
        if path == "/api/health":
            return reply({"runtime": "Python Worker", "python": platform.python_version(), "pyodide": pyodide.__version__, "pipecat": "1.11.0-patched", "voice_validated": False})
        if path in ("/api/session", "/api/access") and request.method == "POST":
            if path == "/api/access":
                # Compatibility for older clients; new calls need no shared key.
                return reply({"ok": True})
            try:
                options = await json_body(request, 1024)
            except (ValueError, TypeError):
                return reply({"error": "Expected a small JSON session request"}, 400)
            transport = options.get("transport", "websocket")
            if transport not in ("websocket", "webrtc"):
                return reply({"error": "Choose websocket or webrtc transport"}, 400)
            if transport == "webrtc" and not sfu_configured(self.env):
                return reply({"error": "The WebRTC example needs a Realtime SFU app ID and secret configured on the Worker.",
                              "code": "sfu_not_configured"}, 503)
            session_id, token = secrets.token_hex(16), secrets.token_urlsafe(32)
            stub = self.env.CONVERSATIONS.get(self.env.CONVERSATIONS.idFromName(session_id))
            await stub.fetch(Request("https://conversation/init", method="POST", body=json.dumps({"id": session_id, "token": token, "transport": transport})))
            return reply({"id": session_id, "token": token, "transport": transport}, 201)
        if path.startswith("/api/session/"):
            session_id = path.split("/")[3]
            if len(session_id) != 32 or any(c not in "0123456789abcdef" for c in session_id):
                return reply({"error": "Invalid conversation ID"}, 400)
            stub = self.env.CONVERSATIONS.get(self.env.CONVERSATIONS.idFromName(session_id))
            return await stub.fetch(request)
        if path.startswith("/api/"):
            return reply({"error": "Not found"}, 404)
        return await self.env.ASSETS.fetch(request)


class Conversation(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.state = None
        self.session = None
        self.socket = None
        self.listeners = []
        self.pump = None
        self.startup = None
        self.startup_stop = None
        self.starting_connection = False
        self.incoming = None
        self.lock = asyncio.Lock()
        self.save_lock = asyncio.Lock()
        self.last_diagnostics = None
        self.fixture = False
        self.sfu = None
        self.media_secret = None

    async def save(self, state):
        async with self.save_lock:
            self.state = state
            await self.ctx.storage.put("conversation", json.dumps(state))

    async def send(self, message):
        if self.socket is not None and self.socket.readyState == 1:
            self.socket.send(json.dumps(message))

    async def fetch(self, request):
        parsed = urlparse(request.url)
        # SFU dials these endpoints while adapter creation is awaiting a network
        # response. Never hold the control lock across signaling or callbacks.
        if "/media/" in parsed.path:
            return self.media_upgrade(request, parsed)
        if parsed.path.endswith("/sfu"):
            if not self.authorized(request, parsed):
                return reply({"error": "Unknown conversation or invalid capability"}, 403)
            if request.method != "POST":
                return reply({"error": "Expected POST"}, 405)
            if not self.sfu or not self.session or self.session.closed:
                return reply({"error": "Start the WebRTC call before signaling"}, 409)
            bridge = self.sfu
            try:
                payload = await json_body(request, 70000)
                return reply(await bridge.signal(payload.get("action"), payload))
            except SfuError as exc:
                return reply({"error": str(exc)}, 409)
            except (ValueError, TypeError):
                return reply({"error": "Invalid WebRTC signaling request"}, 400)
            except Exception:
                return reply({"error": "Realtime signaling failed; end this call and try again"}, 502)
        return await self.fetch_control(request)

    def authorized(self, request, parsed):
        token = request.headers.get("X-Session-Token") or parse_qs(parsed.query).get("token", [""])[0]
        return bool(self.state and token and hmac.compare_digest(
            hashlib.sha256(token.encode()).hexdigest(), self.state.get("token_hash", "")))

    def media_mac(self, path):
        return hmac.new(self.media_secret, path.encode(), hashlib.sha256).hexdigest()

    def media_upgrade(self, request, parsed):
        if not self.sfu or not self.media_secret:
            return reply({"error": "Media endpoint has expired"}, 410)
        supplied = parse_qs(parsed.query).get("media_token", [""])[0]
        if not hmac.compare_digest(supplied.encode(), self.media_mac(parsed.path).encode()):
            return reply({"error": "Invalid media capability"}, 403)
        suffix = parsed.path.split("/media/", 1)[1]
        if suffix == "input":
            role, generation = "input", None
        elif suffix.startswith("output/") and suffix[7:].isdigit() and len(suffix[7:]) <= 12:
            role, generation = "output", int(suffix[7:])
        else:
            return reply({"error": "Unknown media endpoint"}, 404)
        if request.headers.get("Upgrade", "").lower() != "websocket":
            return reply({"error": "Expected WebSocket upgrade"}, 426)
        client, server = WebSocketPair.new().object_values()
        try:
            self.sfu.attach_socket(role, server, generation)
        except SfuError as exc:
            for socket in (server, client):
                with contextlib.suppress(Exception):
                    socket.close(1000, "Media endpoint rejected")
            return reply({"error": str(exc)}, 410)
        return Response(None, status=101, web_socket=client)

    async def fetch_control(self, request):
        async with self.lock:
            if self.state is None:
                stored = await self.ctx.storage.get("conversation")
                self.state = json.loads(stored) if stored else {}
            parsed = urlparse(request.url)
            if parsed.path == "/init" and request.method == "POST":
                if self.state:
                    return reply({"error": "Already initialized"}, 409)
                init = await request.json()
                await self.save({"id": init["id"], "token_hash": hashlib.sha256(init["token"].encode()).hexdigest(),
                                 "transport": init.get("transport", "websocket"), "messages": [],
                                 "generation": 0, "status": "created", "updated_at": time.time()})
                await self.ctx.storage.setAlarm(int((time.time()+86400)*1000))
                return reply({"ok": True})
            if not self.authorized(request, parsed):
                return reply({"error": "Unknown conversation or invalid capability"}, 403)
            if parsed.path.endswith("/diagnostics"):
                return reply(self.session.diagnostics() if self.session else self.last_diagnostics or {"live": False, "status": self.state["status"]})
            if parsed.path.endswith("/probe") and enabled(self.env, "ENABLE_TEST_ROUTES"):
                from runtime_probe import run_probe
                return reply(await run_probe())
            if parsed.path.endswith("/restart") and enabled(self.env, "ENABLE_TEST_ROUTES") and request.method == "POST":
                await self.shutdown("controlled_restart", recoverable=True)
                self.ctx.abort("Controlled spike restart after application state persisted")
            if request.headers.get("Upgrade", "").lower() != "websocket":
                return reply({"error": "Expected WebSocket upgrade"}, 426)
            origin = request.headers.get("Origin")
            if origin and urlparse(origin).netloc != parsed.netloc:
                return reply({"error": "Cross-origin connection rejected"}, 403)
            if self.state.get("status") == "ended":
                return reply({"error": "Conversation ended; start a new session"}, 410)
            if self.socket is not None:
                return reply({"error": "Conversation already connected"}, 409)
            if self.state.get("transport") == "webrtc":
                self.media_secret = secrets.token_bytes(32)
                prefix = f"/api/session/{self.state['id']}/media/"
                def endpoint(suffix):
                    path = prefix + suffix
                    return f"wss://{parsed.netloc}{path}?media_token={self.media_mac(path)}"
                bridge = None
                async def audio(pcm):
                    if self.sfu is bridge and self.session and not self.session.closed:
                        await self.session.audio(pcm)
                async def event(message):
                    if self.sfu is bridge:
                        await self.send(message)
                        if message.get("recoverable") is False:
                            await self.provider_failed()
                bridge = SfuTransport(self.env, audio, event, input_endpoint=endpoint("input"),
                                      output_endpoint_factory=lambda generation: endpoint(f"output/{generation}"))
                self.sfu = bridge
            client, server = WebSocketPair.new().object_values()
            server.accept()
            self.socket = server
            self.incoming = asyncio.Queue(maxsize=128)
            self.listen("message", self.on_message)
            self.listen("close", lambda event: self.enqueue({"type": "disconnect"}))
            self.listen("error", lambda event: self.enqueue({"type": "disconnect"}))
            self.fixture = enabled(self.env, "ENABLE_TEST_ROUTES") and parse_qs(parsed.query).get("fixture") == ["1"]
            if self.fixture:
                from runtime_probe import FixtureProviders
                factory = lambda callback: FixtureProviders(callback, label=self.state["id"])
            else:
                factory = lambda callback: WorkersProviders(self.env, callback)
            self.session = ConversationSession(self.state, factory, self.send, self.save,
                on_fatal=self.provider_failed,
                audio_transport=self.sfu)
            self.startup_stop = None
            self.starting_connection = True
            self.pump = asyncio.create_task(self.run())
            # Nonhibernating socket + instance references retain live tasks.
            # Workers owns asyncio: no asyncio.run(), signals, or new threads.
            return Response(None, status=101, web_socket=client)

    def listen(self, event, callback):
        proxy = create_proxy(callback)
        self.listeners.append((event, proxy))
        self.socket.addEventListener(event, proxy)

    async def provider_failed(self):
        self.enqueue({"type": "provider_failed"})

    def enqueue(self, value):
        if self.incoming is None:
            return
        if (self.starting_connection
                and value.get("type") in ("end", "disconnect")):
            # Stop waiting immediately; the provider owns cleanup of any late
            # JavaScript response. Do not open another provider while waiting.
            if self.startup_stop is not None:
                return
            self.startup_stop = value["type"]
            if self.startup is not None and not self.startup.done():
                self.startup.cancel()
            return
        if self.incoming.full():
            with contextlib.suppress(Exception):
                self.socket.close(4001, "Inbound queue exceeded limit")
            while not self.incoming.empty():
                self.incoming.get_nowait()
            self.incoming.put_nowait({"type": "disconnect"})
        else:
            self.incoming.put_nowait(value)

    def on_message(self, event):
        try:
            if not isinstance(event.data, str) or len(event.data) > 24000:
                raise ValueError("Message must be JSON no larger than 24 KB")
            self.enqueue(json.loads(event.data))
        except Exception:
            self.enqueue({"type": "invalid"})

    async def run(self):
        reason, recoverable = "disconnect", True
        try:
            if self.startup_stop:
                raise asyncio.CancelledError
            self.startup = asyncio.create_task(self.session.start())
            try:
                await self.startup
                if self.startup_stop:
                    raise asyncio.CancelledError
            finally:
                self.startup = None
                self.starting_connection = False
            while True:
                try:
                    message = await asyncio.wait_for(self.incoming.get(), 30)
                except asyncio.TimeoutError:
                    reason = "abandoned_timeout"
                    break
                self.session.last_activity = time.monotonic()
                if time.monotonic() - self.session.began > 900:
                    reason, recoverable = "fifteen_minute_limit", False
                    break
                kind = message.get("type")
                if self.sfu and kind in ("audio", "played"):
                    raise ValueError("WebRTC media must use the SFU; playback receipts are unavailable")
                if kind == "audio":
                    if message.get("sample_rate") != 16000:
                        raise ValueError("Input sample rate must be 16000")
                    await self.session.audio(base64.b64decode(message["data"], validate=True))
                elif kind == "played":
                    await self.session.played(message.get("generation"), message.get("chunk_id"))
                elif kind == "interrupt":
                    await self.session.interrupt()
                elif kind == "fixture_event" and self.fixture:
                    # Explicitly gated synthetic I/O; never selected by browser client.
                    await self.session.provider_event(message["event"])
                elif kind == "ping":
                    d = self.session.diagnostics()
                    await self.send({"type": "pong", "audio": {k: d.get(k, 0) for k in
                        ("input_audio_chunks", "input_audio_bytes", "forwarded_audio_bytes")}})
                elif kind == "end":
                    reason, recoverable = "ended", False
                    await self.send({"type": "ended"})
                    break
                elif kind == "disconnect":
                    break
                elif kind == "provider_failed":
                    reason, recoverable = "terminal_provider_failure", False
                    break
                else:
                    raise ValueError("Unknown message type")
        except asyncio.CancelledError:
            if self.startup_stop == "end":
                reason, recoverable = "ended", False
                await self.send({"type": "ended"})
            elif self.startup_stop == "disconnect":
                reason = "disconnect"
            else:
                raise
        except ProviderConnectionError as exc:
            reason, recoverable = "provider_startup_rejected", False
            await self.send({"type": "error", "message": str(exc), "recoverable": False})
        except Exception as exc:
            reason = "connection_error"
            await self.send({"type": "error", "message": str(exc)})
        finally:
            self.starting_connection = False
            await self.shutdown(reason, recoverable=recoverable)

    async def shutdown(self, reason, *, recoverable):
        current = asyncio.current_task()
        if self.pump and self.pump is not current:
            task, self.pump = self.pump, None
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.media_secret = None
        self.sfu = None
        if self.session:
            session, self.session = self.session, None
            await session.close(reason)
            self.last_diagnostics = session.diagnostics()
        if self.state:
            self.state["status"] = "disconnected" if recoverable else "ended"
            await self.save(self.state)
        if self.socket:
            socket, self.socket = self.socket, None
            with contextlib.suppress(Exception):
                socket.close(1012 if recoverable else 1000, reason)
            for event, proxy in self.listeners:
                with contextlib.suppress(Exception):
                    socket.removeEventListener(event, proxy)
                    proxy.destroy()
            self.listeners.clear()
        self.incoming = None

    async def alarm(self):
        if self.session:
            await self.ctx.storage.setAlarm(int((time.time()+86400)*1000))
        else:
            await self.ctx.storage.deleteAll()
            self.state = {}
