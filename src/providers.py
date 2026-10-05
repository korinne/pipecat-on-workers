"""Thread-free Workers AI adapters. No Python network/provider SDKs are used.

Nova input: signed little-endian PCM16, mono, 16 kHz.
TTS output: signed little-endian PCM16, mono, 24 kHz. Each synthesis owns its
socket, so canceling a generation cannot feed audio into a later generation.
The caller must also use playback generation IDs and explicitly close generators
when stopping early. A socket close is cancellation of delivery, not proof that
the remote model stopped computing or charging.
"""

import asyncio
import base64
import contextlib
import inspect
import json
import math
import re
import struct
import time

from js import Object, Uint8Array
from pyodide.ffi import create_proxy, to_js
from gpt_stream import GPTStream, GPTStreamError, MODEL as GPT_MODEL, MAX_TOKENS as GPT_MAX_TOKENS, REASONING_EFFORT as GPT_REASONING_EFFORT

STT_MODEL = "@cf/deepgram/nova-3"
TURN_MODEL = "@cf/pipecat-ai/smart-turn-v2"
LLM_MODEL = GPT_MODEL
TTS_MODEL = "@cf/deepgram/aura-2-en"
INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000
MAX_ERROR_BODY_BYTES = 8192
ERROR_BODY_TIMEOUT = 1.0
ERROR_BODY_CLEANUP_TIMEOUT = 0.25
STT_CONNECT_BUDGET = 18.0
STT_KEEPALIVE_INTERVAL = 3.0
TURN_TIMEOUT = 2.0
TURN_MAX_AUDIO_BYTES = 8 * INPUT_SAMPLE_RATE * 2
MAX_RETRY_AFTER = 300.0


def _js(value):
    return to_js(value, dict_converter=Object.fromEntries)


def _raw(value):
    return getattr(value, "js_object", value)


def _bytes(value):
    if isinstance(value, bytes):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    return bytes(Uint8Array.new(value).to_py())


class ProviderError(RuntimeError):
    pass


class ProviderConnectionError(ProviderError):
    """Sanitized connection failure; raw provider text is never user-visible."""

    def __init__(self, provider, *, status=None, code=None, retry_after=None, reason=None):
        self.provider = provider
        self.status = int(status) if type(status) in (int, float) and 100 <= status <= 599 and status == int(status) else None
        self.code = code if type(code) is int and code in (3036, 3040) else None
        self.retry_after = retry_after if type(retry_after) in (int, float) and 0 <= retry_after <= MAX_RETRY_AFTER else None
        # Capacity retries happen inside the one bounded connection operation.
        # Exhaustion must not trigger a second retry loop in the browser.
        self.recoverable = False
        service = "Speech recognition" if provider == "stt" else "Speech synthesis"
        if self.code == 3036:
            message = f"{service} is unavailable because the Workers AI quota is exhausted. Check the account's usage or plan before trying again."
        elif self.code == 3040:
            message = f"{service} is temporarily at capacity. Please try again shortly."
        elif self.status == 429:
            message = f"{service} is unavailable because the provider limited this request (HTTP 429). Please try again later."
        elif reason == "timeout":
            message = f"{service} connection timed out. Please try again."
        else:
            suffix = f" (HTTP {self.status})" if self.status is not None else ""
            message = f"{service} connection could not be established{suffix}. Please try again."
        super().__init__(message)


def _retry_after(response):
    try:
        value = response.headers.get("Retry-After")
        if not isinstance(value, str) or len(value) > 32:
            return None
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value.strip()):
            return None
        seconds = float(value)
        # Preserve an over-budget delay as the bounded maximum; treating it as
        # absent would incorrectly fall back to an earlier 1/2-second retry.
        return min(seconds, MAX_RETRY_AFTER)
    except Exception:
        return None


def _connection_code(value):
    if not isinstance(value, dict):
        return None
    candidates = [value]
    if isinstance(value.get("error"), dict):
        candidates.append(value["error"])
    if isinstance(value.get("errors"), list):
        candidates.extend(item for item in value["errors"] if isinstance(item, dict))
    for item in candidates:
        for key in ("internalCode", "code"):
            code = item.get(key)
            if type(code) in (int, float) and code in (3036, 3040):
                return int(code)
    return None


class _Socket:
    """Own callback proxies and an explicitly bounded receive queue."""

    def __init__(self, ws, provider, max_bytes=1048576):
        self.ws = ws
        self.provider = provider
        self.queue = asyncio.Queue(maxsize=256)
        self.max_bytes = max_bytes
        self.queued_bytes = 0
        self.closed = False
        self.listeners = []
        self.failure = None
        try:
            ws.binaryType = "arraybuffer"
            self._listen("message", self._message)
            self._listen("close", self._closed)
            self._listen("error", self._error)
            ws.accept()
        except BaseException:
            self.close()
            raise

    def _listen(self, event, callback):
        proxy = create_proxy(callback)
        self.listeners.append((event, proxy))
        self.ws.addEventListener(event, proxy)

    def _put(self, value):
        if self.closed:
            return
        size = len(value) if isinstance(value, (str, bytes)) else 0
        if self.queue.full() or self.queued_bytes + size > self.max_bytes:
            self.failure = ProviderError(f"{self.provider} receive queue exceeded its bound")
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queued_bytes = 0
            self.queue.put_nowait(self.failure)
            self.close()
            return
        self.queued_bytes += size
        self.queue.put_nowait(value)

    def _message(self, event):
        try:
            value = event.data
            self._put(value if isinstance(value, str) else _bytes(value))
        except Exception as exc:
            self._put(ProviderError(f"{self.provider} frame conversion failed: {exc}"))

    def _closed(self, event):
        self._put(ProviderError(f"{self.provider} WebSocket closed ({event.code})"))

    def _error(self, event):
        self._put(ProviderError(f"{self.provider} WebSocket transport error"))

    def send(self, value):
        if self.closed or self.ws.readyState != 1:
            raise ProviderError(f"{self.provider} WebSocket is not open")
        self.ws.send(value if isinstance(value, str) else _js(bytes(value)))

    async def receive(self, timeout=30):
        value = await asyncio.wait_for(self.queue.get(), timeout)
        if isinstance(value, (str, bytes)):
            self.queued_bytes -= len(value)
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        if self.closed:
            return
        self.closed = True
        with contextlib.suppress(Exception):
            self.ws.close(1000, "Generation complete or canceled")
        for event, proxy in self.listeners:
            with contextlib.suppress(Exception):
                self.ws.removeEventListener(event, proxy)
            with contextlib.suppress(Exception):
                proxy.destroy()
        self.listeners.clear()


class WorkersProviders:
    def __init__(self, env, on_event):
        self.env = env
        self.on_event = on_event
        self.stt = None
        self.closed = False
        self.tasks = set()
        self.cleanup_tasks = set()
        self.sockets = set()
        # Pyodide JsProxy readers can be unhashable. Retain each proxy by Python
        # identity until its generator finishes cancellation and releases it.
        self.readers = {}
        self.requests = set()
        self._discarding_requests = set()
        self.connection_generation = 0
        self.dropped_audio_bytes = 0
        self.last_audio = time.monotonic()
        self._start_lock = asyncio.Lock()
        self._pump_task = None
        # A canceled Python wait cannot abort the binding promise. Keep this
        # slot occupied until that promise settles, including after timeout.
        self._turn_request = None

    async def _emit(self, event):
        if not self.closed:
            result = self.on_event(event)
            if inspect.isawaitable(result):
                await result

    def _task(self, coro, *, cleanup=False):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        if cleanup:
            self.cleanup_tasks.add(task)
            task.add_done_callback(self.cleanup_tasks.discard)
        return task

    def _discard_late_result(self, task):
        """A canceled Python wait does not abort a JavaScript promise."""
        if task.cancelled():
            self._discarding_requests.discard(task)
            return
        try:
            result = _raw(task.result())
            socket = getattr(result, "webSocket", None)
            if socket:
                try:
                    socket.accept()
                finally:
                    socket.close(1000, "Request canceled before provider connected")
            else:
                stream = result.body if hasattr(result, "body") else result
                if hasattr(stream, "cancel"):
                    # Own bounded disposal separately so close cannot cancel it
                    # before the coroutine first invokes stream.cancel().
                    async def finish():
                        with contextlib.suppress(Exception):
                            await asyncio.wait_for(stream.cancel("Request canceled"),
                                                   ERROR_BODY_CLEANUP_TIMEOUT)
                    self._task(finish(), cleanup=True)
        except Exception:
            # Retrieving task.result also consumes an upstream failure after the
            # original request has already been canceled or timed out.
            pass
        finally:
            self._discarding_requests.discard(task)

    def _discard_when_done(self, request):
        if request not in self._discarding_requests:
            self._discarding_requests.add(request)
            request.add_done_callback(self._discard_late_result)

    async def _run(self, model, parameters, options=None, timeout=30):
        if model == TURN_MODEL and self._turn_request is not None and not self._turn_request.done():
            raise ProviderError("Turn detection is still finishing a previous request. Please repeat your turn shortly.")
        args = [model, _js(parameters)]
        if options is not None:
            args.append(_js(options))
        request = asyncio.ensure_future(self.env.AI.run(*args))
        if model == TURN_MODEL:
            self._turn_request = request

            def release_turn_request(completed):
                if self._turn_request is completed:
                    self._turn_request = None

            request.add_done_callback(release_turn_request)
        self.requests.add(request)
        request.add_done_callback(self.requests.discard)
        try:
            # asyncio.wait leaves the owned binding promise alive on caller
            # cancellation. Python 3.14 shield logs its later abort rejection
            # even when our late-result owner retrieves that exception.
            done, _ = await asyncio.wait({request}, timeout=timeout)
            if not done:
                raise asyncio.TimeoutError()
            return request.result()
        except (asyncio.CancelledError, asyncio.TimeoutError):
            self._discard_when_done(request)
            raise

    async def _read_connection_error(self, response, *, deadline=None):
        """Read only a small JSON error; retain the reader through disposal."""
        body = getattr(response, "body", None)
        if not body:
            return None
        reader = None
        try:
            reader = body.getReader()
            self.readers[id(reader)] = reader
            budget = ERROR_BODY_TIMEOUT
            if deadline is not None:
                budget = min(budget, max(0, deadline-time.monotonic()-ERROR_BODY_CLEANUP_TIMEOUT))
            if budget <= 0:
                return None

            async def read_json():
                data = bytearray()
                while True:
                    item = await reader.read()
                    if item.done:
                        return _connection_code(json.loads(data.decode("utf-8")))
                    size = len(item.value) if isinstance(item.value, (bytes, bytearray, memoryview)) else getattr(item.value, "byteLength", None)
                    if isinstance(size, (int, float)) and size > MAX_ERROR_BODY_BYTES-len(data):
                        return None
                    chunk = _bytes(item.value)
                    if len(data) + len(chunk) > MAX_ERROR_BODY_BYTES:
                        return None
                    data.extend(chunk)

            return await asyncio.wait_for(read_json(), budget)
        except Exception:
            # Invalid, oversized, unavailable and slow bodies retain HTTP-only
            # classification. Cancellation must propagate to the startup owner.
            return None
        finally:
            try:
                target = reader if reader is not None else body
                if hasattr(target, "cancel"):
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(target.cancel("Rejected provider connection"),
                                               ERROR_BODY_CLEANUP_TIMEOUT)
            finally:
                if reader is not None:
                    try:
                        with contextlib.suppress(Exception):
                            reader.releaseLock()
                    finally:
                        self.readers.pop(id(reader), None)

    async def _socket(self, model, parameters, name, *, deadline=None):
        if self.closed:
            raise ProviderError("Conversation providers are closed")
        timeout = 20 if deadline is None else min(20, deadline-time.monotonic()-ERROR_BODY_CLEANUP_TIMEOUT)
        if timeout <= 0:
            raise ProviderConnectionError(name, reason="timeout")
        try:
            response = await self._run(model, parameters, {"websocket": True}, timeout=timeout)
        except asyncio.TimeoutError:
            raise ProviderConnectionError(name, reason="timeout") from None
        response = _raw(response)
        ws = getattr(response, "webSocket", None)
        # Pyodide maps JavaScript null to a falsey JsNull, not Python None.
        if not ws:
            status = getattr(response, "status", None)
            retry_after = _retry_after(response)
            code = await self._read_connection_error(response, deadline=deadline)
            raise ProviderConnectionError(name, status=status, code=code, retry_after=retry_after)
        socket = _Socket(ws, name)
        if self.closed:
            socket.close()
            raise ProviderError("Conversation ended during provider connection")
        self.sockets.add(socket)
        return socket

    async def _connect_stt(self):
        deadline = time.monotonic() + STT_CONNECT_BUDGET
        for attempt in range(1, 4):
            if self.closed:
                raise ProviderError("Conversation providers are closed")
            try:
                # WebSocket parameters are strings; the REST model catalog's
                # boolean/number types do not apply to this handshake.
                self.stt = await self._socket(STT_MODEL, {
                    "encoding": "linear16", "sample_rate": str(INPUT_SAMPLE_RATE),
                    "channels": "1", "language": "en-US", "interim_results": "true",
                    "vad_events": "true", "endpointing": "200",
                }, "stt", deadline=deadline)
                break
            except ProviderConnectionError as exc:
                if exc.status != 429 or exc.code != 3040 or attempt == 3 or self.closed:
                    raise
                delay = exc.retry_after if exc.retry_after is not None else float(attempt)
                if deadline-time.monotonic() <= delay + ERROR_BODY_CLEANUP_TIMEOUT:
                    raise
                await self._emit({"type": "ProviderStatus", "provider": "stt", "status": "retrying",
                                  "attempt": attempt+1, "delay_ms": round(delay*1000)})
                if self.closed:
                    raise ProviderError("Conversation providers are closed")
                await asyncio.sleep(delay)
        self.connection_generation += 1
        self.last_audio = time.monotonic()
        await self._emit({"type": "ProviderStatus", "provider": "stt", "status": "connected",
                          "connection_generation": self.connection_generation})

    async def start(self):
        async with self._start_lock:
            if self.closed:
                raise ProviderError("Conversation providers are closed")
            if self._pump_task is not None:
                return
            await self._connect_stt()
            self._pump_task = self._task(self._pump_stt())
            self._task(self._keepalive())

    async def send_audio(self, pcm):
        if len(pcm) % 2:
            raise ValueError("PCM16 audio must have an even number of bytes")
        if self.stt is None or self.stt.closed:
            self.dropped_audio_bytes += len(pcm)
            return False
        self.stt.send(pcm)
        self.last_audio = time.monotonic()
        return True

    async def _keepalive(self):
        # Control messages do not advance Nova's audio clock. Injecting silence
        # here would shift its timestamps relative to the turn audio buffer.
        while not self.closed:
            await asyncio.sleep(STT_KEEPALIVE_INTERVAL)
            if self.stt is not None and time.monotonic() - self.last_audio >= STT_KEEPALIVE_INTERVAL:
                with contextlib.suppress(ProviderError):
                    self.stt.send(json.dumps({"type": "KeepAlive"}))

    async def _pump_stt(self):
        retries = 0
        while not self.closed:
            try:
                try:
                    value = await self.stt.receive(timeout=45)
                except asyncio.TimeoutError:
                    # Nova does not acknowledge KeepAlive messages. A muted
                    # socket can legitimately have no recognition events.
                    if time.monotonic() - self.last_audio >= STT_KEEPALIVE_INTERVAL:
                        continue
                    raise
                if not isinstance(value, str):
                    raise ProviderError("Speech recognition returned an invalid event.")
                event = json.loads(value)
                if not isinstance(event, dict) or not isinstance(event.get("type"), str) or not event["type"]:
                    raise ProviderError("Speech recognition returned an invalid event.")
                event["connection_generation"] = self.connection_generation
                if event.get("type") in {"Error", "error"}:
                    raise ProviderError("Speech recognition reported a stream error.")
                await self._emit(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.closed:
                    return
                if self.stt is not None:
                    old = self.stt
                    self.stt = None
                    old.close()
                    self.sockets.discard(old)
                recoverable = retries < 2 and getattr(exc, "recoverable", True)
                message = str(exc) if isinstance(exc, ProviderConnectionError) else "Speech recognition stream failed. Please repeat your turn."
                await self._emit({"type": "ProviderError", "provider": "stt", "message": message,
                                  "recoverable": recoverable, "audio_gap": True})
                # At most two reconnects per call; lost audio is not replayed and a
                # partial utterance must be repeated. Audio timestamps restart upstream.
                if not recoverable:
                    return
                retries += 1
                await asyncio.sleep(0.25 * retries)
                try:
                    await self._connect_stt()
                except Exception as reconnect_error:
                    message = str(reconnect_error) if isinstance(reconnect_error, ProviderConnectionError) else "Speech recognition could not reconnect. Please start a new call."
                    await self._emit({"type": "ProviderError", "provider": "stt",
                                      "message": message, "recoverable": False})
                    return

    async def analyze_turn(self, pcm):
        """Check one bounded 16 kHz PCM16 mono snapshot; never infer on failure.

        The hosted model receives base64 little-endian float32 samples. The
        model's input schema permits this representation; live acceptance and
        Python/JavaScript conversion still require the Workers integration check.
        """
        if self.closed:
            raise ProviderError("Conversation providers are closed")
        if not isinstance(pcm, (bytes, bytearray, memoryview)):
            raise ValueError("Turn audio must be nonempty PCM16 bytes")
        byte_length = pcm.nbytes if isinstance(pcm, memoryview) else len(pcm)
        if not byte_length or byte_length % 2:
            raise ValueError("Turn audio must be nonempty PCM16 bytes")
        if byte_length > TURN_MAX_AUDIO_BYTES:
            raise ValueError("Turn audio exceeds the eight-second analysis window")
        pcm = bytes(pcm)
        encoded = bytearray(len(pcm) * 2)
        for index, (sample,) in enumerate(struct.iter_unpack("<h", pcm)):
            struct.pack_into("<f", encoded, index * 4, sample / 32768.0)
        try:
            result = _raw(await self._run(TURN_MODEL, {
                "audio": base64.b64encode(encoded).decode("ascii"), "dtype": "float32",
            }, timeout=TURN_TIMEOUT))
            if hasattr(result, "to_py"):
                result = result.to_py()
        except asyncio.TimeoutError:
            raise ProviderError("Turn detection timed out. Please repeat your turn.") from None
        except ProviderError:
            raise
        except Exception:
            raise ProviderError("Turn detection request failed. Please repeat your turn.") from None
        if self.closed:
            raise ProviderError("Conversation providers are closed")
        if not isinstance(result, dict) or type(result.get("is_complete")) is not bool:
            raise ProviderError("Turn detection returned an invalid decision. Please repeat your turn.")
        probability = result.get("probability")
        if type(probability) not in (int, float) or not 0 <= probability <= 1 or not math.isfinite(probability):
            raise ProviderError("Turn detection returned an invalid probability. Please repeat your turn.")
        return {"is_complete": result["is_complete"], "probability": float(probability)}

    async def generate(self, messages, *, max_tokens=GPT_MAX_TOKENS):
        """Stream only GPT-OSS answer text; incomplete answers fail explicitly."""
        if self.closed:
            raise ProviderError("Conversation providers are closed")
        from js import AbortController
        controller = AbortController.new()
        reader = None
        parser = GPTStream()
        started = time.monotonic()
        result = {"outcome": "pending", "first_answer_seconds": None,
                  "max_tokens": max_tokens, "reasoning_effort": GPT_REASONING_EFFORT}
        self.last_generation = result
        try:
            stream = _raw(await self._run(LLM_MODEL, {
                "messages": messages, "stream": True, "max_tokens": max_tokens,
                "reasoning_effort": GPT_REASONING_EFFORT,
            }, {"signal": controller.signal}, timeout=45))
            if hasattr(stream, "body"):
                stream = stream.body
            if not hasattr(stream, "getReader"):
                raise GPTStreamError("invalid_stream")
            reader = stream.getReader()
            self.readers[id(reader)] = reader
            while not self.closed:
                remaining = 45 - (time.monotonic() - started)
                if remaining <= 0:
                    raise asyncio.TimeoutError()
                item = await asyncio.wait_for(reader.read(), remaining)
                for text in parser.feed(b"" if item.done else _bytes(item.value), eof=bool(item.done)):
                    if self.closed:
                        result["outcome"] = "canceled"
                        return
                    if result["first_answer_seconds"] is None and text.strip():
                        result["first_answer_seconds"] = time.monotonic() - started
                    yield text
                if item.done or parser.done:
                    result["outcome"] = "completed"
                    return
            result["outcome"] = "canceled"
        except (asyncio.CancelledError, GeneratorExit):
            result["outcome"] = "canceled"
            raise
        except GPTStreamError as error:
            result["outcome"] = error.outcome
            failure = ProviderError(str(error))
            failure.outcome = error.outcome
            raise failure from None
        except asyncio.TimeoutError:
            result["outcome"] = "timeout"
            raise ProviderError("The model timed out before completing an answer. Please try again.") from None
        except Exception:
            result["outcome"] = "request_or_read_error"
            raise ProviderError("The model request failed before completing an answer. Please try again.") from None
        finally:
            result.update(parser.diagnostics())
            result["elapsed_seconds"] = time.monotonic() - started
            # The signal covers pre-header cancellation. Reader disposal covers
            # an acquired body; late _run results remain owned by the provider.
            with contextlib.suppress(Exception):
                controller.abort("Generation complete or canceled")
            if reader is not None:
                try:
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(reader.cancel("Generation complete or canceled"), 2)
                finally:
                    try:
                        with contextlib.suppress(Exception):
                            reader.releaseLock()
                    finally:
                        self.readers.pop(id(reader), None)


    async def synthesize(self, text):
        """Yield raw 24 kHz mono PCM from a generation-specific Aura socket."""
        if not text.strip():
            return
        socket = await self._socket(TTS_MODEL, {
            "speaker": "luna", "encoding": "linear16", "sample_rate": str(OUTPUT_SAMPLE_RATE),
            "container": "none",
        }, "tts")
        try:
            socket.send(json.dumps({"type": "Speak", "text": text}))
            socket.send(json.dumps({"type": "Flush"}))
            while True:
                value = await socket.receive(timeout=30)
                if isinstance(value, bytes):
                    yield value
                else:
                    event = json.loads(value)
                    if event.get("type") == "Flushed":
                        return
                    if event.get("type") in {"Error", "error"}:
                        raise ProviderError(f"TTS error: {event.get('description', event.get('message', 'unknown'))}")
        finally:
            # A fresh socket next time is a generation barrier, stronger than
            # relying on provider Clear timing on a reused connection.
            with contextlib.suppress(Exception):
                socket.send(json.dumps({"type": "Clear"}))
            socket.close()
            self.sockets.discard(socket)

    async def close(self):
        self.closed = True
        current = asyncio.current_task()
        pending = [task for task in self.tasks if task is not current and task not in self.cleanup_tasks]
        for task in pending:
            task.cancel()
        for socket in tuple(self.sockets):
            socket.close()
        self.sockets.clear()
        self.stt = None
        # Requests still awaiting their first JS response need late-result cleanup.
        # GPT requests also carry AbortSignal. Keep late-result ownership because
        # local abort does not prove that the binding promise or remote work stopped.
        for request in tuple(self.requests):
            self._discard_when_done(request)
        for reader in tuple(self.readers.values()):
            with contextlib.suppress(Exception):
                await asyncio.wait_for(reader.cancel("Conversation ended"), 2)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        cleanup = [task for task in self.cleanup_tasks if task is not current]
        if cleanup:
            await asyncio.gather(*cleanup, return_exceptions=True)
        # Done callbacks remove finished tasks. Late promise disposal may have
        # created new cleanup tasks during the await above; keep them owned.

    def diagnostics(self):
        return {"provider_tasks": sum(not t.done() for t in self.tasks),
                "provider_sockets": len(self.sockets), "provider_readers": len(self.readers),
                "pending_provider_requests": len(self.requests),
                "pending_turn_requests": int(self._turn_request is not None and not self._turn_request.done()),
                "queued_provider_bytes": sum(s.queued_bytes for s in self.sockets),
                "stt_connection_generation": self.connection_generation,
                "dropped_audio_bytes": self.dropped_audio_bytes}
