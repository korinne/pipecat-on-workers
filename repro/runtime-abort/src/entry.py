"""Minimal Python DO WebSocket/JSON experiment: no Pipecat or provider dependencies."""
import asyncio
import base64
import json
import platform
from urllib.parse import urlparse
from js import WebSocketPair, queueMicrotask
from pyodide.ffi import create_proxy
from workers import DurableObject, Response, WorkerEntrypoint


def reply(value):
    return Response(json.dumps(value), headers={"Content-Type": "application/json"})


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        name = urlparse(request.url).path.split("/")[1] or "health"
        stub = self.env.SOCKETS.get(self.env.SOCKETS.idFromName(name))
        return await stub.fetch(request)


class SocketDO(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.count = 0
        self.byte_count = 0
        self.errors = 0
        self.peak_queue = 0
        self.socket = None
        self.listeners = []
        self.queue = None
        self.task = None

    async def fetch(self, request):
        path = urlparse(request.url).path
        if path.endswith("/abort"):
            self.ctx.abort("Minimal controlled abort experiment")
        if path.endswith("/abort-js"):
            # Experimental comparison: do not enter Python in the abort callback.
            ctx = self.ctx._ctx
            queueMicrotask(ctx.abort.bind(ctx, "Minimal native JS abort experiment"))
            return Response("Abort queued")
        if request.headers.get("Upgrade", "").lower() != "websocket":
            return reply(self.stats())
        if self.socket:
            return Response("Already connected", status=409)
        client, server = WebSocketPair.new().object_values()
        server.accept()
        self.socket = server
        self.queue = asyncio.Queue(maxsize=8192)
        for kind, callback in (("message", self.on_message), ("close", self.on_close)):
            proxy = create_proxy(callback)
            self.listeners.append((kind, proxy))
            server.addEventListener(kind, proxy)
        self.task = asyncio.create_task(self.run())
        server.send(json.dumps({"type": "ready", "python": platform.python_version()}))
        return Response(None, status=101, web_socket=client)

    def stats(self):
        return {"messages": self.count, "decoded_bytes": self.byte_count,
                "errors": self.errors, "peak_queue": self.peak_queue,
                "python": platform.python_version(), "connected": self.socket is not None}

    def on_close(self, event):
        if self.queue is not None:
            self.queue.put_nowait({"type": "end"})

    def on_message(self, event):
        # Keep this boundary identical to the original failing callback shape.
        message = json.loads(event.data)
        self.queue.put_nowait(message)
        self.peak_queue = max(self.peak_queue, self.queue.qsize())

    async def run(self):
        try:
            while True:
                message = await self.queue.get()
                if message["type"] == "end":
                    self.socket.send(json.dumps({"type": "ended", **self.stats()}))
                    break
                if message["type"] == "audio":
                    self.byte_count += len(base64.b64decode(message["data"], validate=True))
                    self.count += 1
                    if self.count % 1000 == 0:
                        self.socket.send(json.dumps({"type": "progress", **self.stats()}))
        except Exception as exc:
            self.errors += 1
            self.socket.send(json.dumps({"type": "error", "message": str(exc)}))
        finally:
            socket, self.socket = self.socket, None
            for kind, proxy in self.listeners:
                socket.removeEventListener(kind, proxy)
                proxy.destroy()
            self.listeners.clear()
            socket.close(1000, "Completed")
            self.queue = None
