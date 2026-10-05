"""Cloudflare Realtime SFU bridge for the Python conversation Durable Object.

The browser speaks WebRTC to the SFU. Two SFU-initiated WebSockets carry PCM to
this object. Each assistant generation owns a new publication AND receiving
PeerConnection, so an interrupted generation cannot enter the next receiver.
Submission and connection readiness are deliberately never playback receipts.

Root routing authenticates media endpoint URLs before calling attach_socket.
The SFU App Secret and endpoint URLs never appear in returned signaling data.
"""

import asyncio
import inspect
import json
import re
import time
from urllib.parse import quote

from sfu_codec import InputResampler, OutputResampler, decode_packet, encode_packet

API_BASE = "https://rtc.live.cloudflare.com/v1/apps"
FRAME_BYTES = 3840  # 20 ms, 48000 Hz, stereo, PCM16
READY_TIMEOUT = 35  # One shared deadline for callback + browser SDP/ICE readiness.
MAX_SDP_BYTES = 65536
API_TIMEOUT = 16
CLEANUP_REQUEST_TIMEOUT = 3
CLOSE_TIMEOUT = 5
CLEANUP_ATTEMPTS = 3
MAX_PENDING_REQUESTS = 8
MAX_OWNED_RESOURCES = 32


class SfuError(RuntimeError):
    """Public-safe failure; raw API bodies may contain endpoint credentials."""


class _StaleGeneration(SfuError):
    pass


class _CleanupExhausted(SfuError):
    pass


def _setting(env, name):
    value = env.get(name) if isinstance(env, dict) else getattr(env, name, None)
    return value if isinstance(value, str) else ""


def configured(env):
    return bool(_setting(env, "REALTIME_SFU_APP_ID") and
                (_setting(env, "REALTIME_SFU_APP_SECRET") or
                 _setting(env, "REALTIME_SFU_BEARER_TOKEN")))


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise SfuError("Realtime returned an invalid resource identifier")
    return value


def _description(value, kind):
    if not isinstance(value, dict) or value.get("type") != kind:
        raise SfuError(f"A WebRTC {kind} is required")
    sdp = value.get("sdp")
    if not isinstance(sdp, str) or not 1 <= len(sdp.encode()) <= MAX_SDP_BYTES:
        raise SfuError("The WebRTC description is missing or too large")
    return {"type": kind, "sdp": sdp}


async def _request_json(url, method, payload, secret):
    # Import Workers/Pyodide FFI only at the network boundary. The codec and
    # transport ownership tests run on ordinary CPython without fake modules.
    from js import AbortController, Object, fetch
    from pyodide.ffi import to_js

    controller = AbortController.new()
    options = {"method": method, "headers": {"Authorization": f"Bearer {secret}",
                                             "Content-Type": "application/json"},
               "signal": controller.signal}
    if payload is not None:
        options["body"] = json.dumps(payload)
    try:
        response = await asyncio.wait_for(fetch(url, to_js(options, dict_converter=Object.fromEntries)), 12)
        text = await asyncio.wait_for(response.text(), 3)
        if len(text) > 262144:
            raise SfuError("Realtime returned an oversized response")
        try:
            body = json.loads(text)
        except (ValueError, TypeError):
            raise SfuError("Realtime returned an invalid response") from None
        if not isinstance(body, dict):
            raise SfuError("Realtime returned an invalid response")
        return int(response.status), body
    except asyncio.TimeoutError:
        raise SfuError("Realtime connection timed out") from None
    finally:
        controller.abort()


class SfuTransport:
    def __init__(self, env, on_audio, on_event, *, input_endpoint,
                 output_endpoint_factory, request=None, clock=None, sleep=None,
                 socket_factory=None, on_cleanup_change=None):
        if not configured(env):
            raise SfuError("Configure the Realtime SFU app ID and secret on the Worker")
        self.app_id = _identifier(_setting(env, "REALTIME_SFU_APP_ID"))
        self.secret = (_setting(env, "REALTIME_SFU_APP_SECRET") or
                       _setting(env, "REALTIME_SFU_BEARER_TOKEN"))
        self.on_audio, self.on_event = on_audio, on_event
        self.input_endpoint = input_endpoint
        self.output_endpoint_factory = output_endpoint_factory
        self.request = request or _request_json
        self.clock, self.sleep = clock or time.monotonic, sleep or asyncio.sleep
        self.socket_factory = socket_factory
        self.on_cleanup_change = on_cleanup_change
        self.cleanup_task = None
        self.cleanup_requests = {}
        self.cleanup_results = {}
        self.cleanup_attempts = {}
        self.closed = False
        self.generation_floor = 0
        self.input_session = None
        self.input_adapter = None
        self.input_needs_answer = False
        self.input_socket = None
        self.input_pump = None
        self.input_lock = asyncio.Lock()
        self.output_lock = asyncio.Lock()
        self.cleanup_lock = asyncio.Lock()
        self.output = None
        self.tasks = set()
        self.requests = set()
        self.request_units = {}
        self.adapters = {}  # ID -> (role, generation)
        self.tracks = {}  # (session, mid) -> (role, generation)
        self.sessions = {}  # IDs retained so uncertain track allocations can be inspected
        self.cleanup_failures = 0
        self.unconfirmed_allocations = 0
        self.received_bytes = self.submitted_bytes = self.dropped_packets = 0

    async def _emit(self, event):
        if not self.closed:
            result = self.on_event(event)
            if inspect.isawaitable(result):
                await result

    def _task(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        # Consume exceptions even if the call is interrupted before an await.
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return task

    def _alive(self, state):
        return (not self.closed and self.output is state and not state["retired"]
                and state["generation"] >= self.generation_floor)

    async def _api(self, method, path, payload=None, record=None):
        async def perform():
            try:
                status, body = await self.request(f"{API_BASE}/{quote(self.app_id)}/{path}",
                                                   method, payload, self.secret)
            except Exception:
                # Neither creation API is idempotent and a lost response can
                # conceal an allocation. Do not advertise proven zero remote
                # resources or retry creation blindly after an uncertain result.
                if method == "POST" and path in ("sessions/new", "adapters/websocket/new"):
                    self.unconfirmed_allocations += 1
                raise
            # Record allocations before interpreting errors or cancellation.
            if record:
                record(body)
            if not 200 <= status < 300 or body.get("errorCode"):
                raise SfuError(f"Realtime operation failed (HTTP {status})")
            return body

        allocating = method == "POST" and path.endswith("/new")
        units = (2 if path == "adapters/websocket/new" else 1) if allocating else 0
        owned = len(self.adapters) + len(self.tracks) + len(self.sessions)
        if (len(self.requests) >= MAX_PENDING_REQUESTS or allocating and
                (owned + sum(self.request_units.values()) + units > MAX_OWNED_RESOURCES
                 or self.unconfirmed_allocations)):
            raise SfuError("Realtime resources remain unresolved; end this call before retrying")
        task = asyncio.create_task(perform())
        self.requests.add(task)
        self.request_units[task] = units
        self._changed()

        def settled(done):
            self.requests.discard(done)
            self.request_units.pop(done, None)
            if not done.cancelled():
                done.exception()
                self._schedule_cleanup()
                self._changed()

        task.add_done_callback(settled)
        try:
            return await asyncio.wait_for(asyncio.shield(task), API_TIMEOUT)
        except asyncio.TimeoutError:
            # Keep the request owner and recorder alive. A timeout cannot prove
            # that the remote allocation failed or disappeared.
            raise SfuError("Realtime operation timed out; its resource status is pending") from None

    def _record_tracks(self, session, role, generation, result):
        for item in result.get("tracks", []):
            if isinstance(item, dict) and isinstance(item.get("mid"), str) and not item.get("errorCode"):
                self.tracks[(session, item["mid"])] = (role, generation)

    def _record_session(self, role, generation, result):
        if result.get("sessionId"):
            self.sessions[_identifier(result["sessionId"])] = (role, generation)

    def _record_adapters(self, role, generation, result):
        for item in result.get("tracks", []):
            if isinstance(item, dict) and item.get("adapterId"):
                self.adapters[_identifier(item["adapterId"])] = (role, generation)
                if item.get("sessionId"):
                    self.sessions[_identifier(item["sessionId"])] = (role, generation)

    @staticmethod
    def _one_track(result):
        tracks = result.get("tracks")
        if not isinstance(tracks, list) or len(tracks) != 1 or not isinstance(tracks[0], dict):
            raise SfuError("Realtime did not allocate the requested audio track")
        if tracks[0].get("errorCode"):
            raise SfuError("Realtime could not connect the requested audio track")
        return tracks[0]

    @staticmethod
    def _public_signal(result):
        output = {"requiresImmediateRenegotiation": bool(result.get("requiresImmediateRenegotiation"))}
        description = result.get("sessionDescription")
        if description:
            kind = description.get("type") if isinstance(description, dict) else None
            if kind not in ("offer", "answer"):
                raise SfuError("Realtime returned an invalid WebRTC description")
            output["sessionDescription"] = _description(description, kind)
        output["tracks"] = [{key: item[key] for key in ("mid", "trackName") if key in item}
                            for item in result.get("tracks", []) if not item.get("errorCode")]
        return output

    async def signal(self, action, payload):
        if self.closed:
            raise SfuError("This Realtime call has ended")
        if not isinstance(payload, dict):
            raise SfuError("Invalid Realtime signaling message")
        if action == "publish":
            return await self._publish(payload)
        if action == "input_ready":
            return await self._input_ready()
        if action in ("subscribe", "renegotiate", "playback_ready"):
            if action == "renegotiate" and payload.get("role") == "input":
                async with self.input_lock:
                    if self.closed:
                        raise SfuError("This Realtime call has ended")
                    if not self.input_session or not self.input_needs_answer:
                        raise SfuError("No microphone negotiation is pending")
                    result = await self._api("PUT", f"sessions/{self.input_session}/renegotiate",
                                             {"sessionDescription": _description(payload.get("sessionDescription"), "answer")})
                    self.input_needs_answer = False
                    return self._public_signal(result)
            generation = payload.get("generation")
            state = self.output
            if type(generation) is not int or not state or state["generation"] != generation or not self._alive(state):
                raise SfuError("That assistant audio generation has ended")
            async with state["signal_lock"]:
                if not self._alive(state):
                    raise SfuError("That assistant audio generation has ended")
                if action == "subscribe":
                    return await self._subscribe(state)
                if action == "renegotiate":
                    if payload.get("role") != "output" or not state["receiver"] or not state["needs_answer"]:
                        raise SfuError("No assistant audio negotiation is pending")
                    result = await self._api("PUT", f"sessions/{state['receiver']}/renegotiate",
                                             {"sessionDescription": _description(payload.get("sessionDescription"), "answer")})
                    state["needs_answer"] = False
                    state["negotiated"] = True
                    return self._public_signal(result)
                if not state["negotiated"]:
                    raise SfuError("Assistant audio negotiation is incomplete")
                # This only unblocks sending; it commits no transcript/history.
                state["ready"].set()
                return {"ok": True, "deliveryConfirmed": False}
        raise SfuError("Unknown Realtime signaling action")

    async def _publish(self, payload):
        offer = _description(payload.get("sessionDescription"), "offer")
        mid = payload.get("mid")
        if not isinstance(mid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", mid):
            raise SfuError("A microphone transceiver identifier is required")
        async with self.input_lock:
            if self.closed:
                raise SfuError("This Realtime call has ended")
            if self.input_session:
                raise SfuError("The microphone is already published")
            result = await self._api("POST", "sessions/new", record=lambda body: self._record_session("input", None, body))
            session = _identifier(result.get("sessionId"))
            if self.closed:
                self._schedule_cleanup()
                raise SfuError("This Realtime call has ended")
            self.input_session = session
            result = await self._api("POST", f"sessions/{session}/tracks/new",
                                     {"sessionDescription": offer, "tracks": [
                                         {"location": "local", "mid": mid, "trackName": "microphone"}]},
                                     lambda body: self._record_tracks(session, "input", None, body))
            self._one_track(result)
            if self.closed:
                self._schedule_cleanup()
                raise SfuError("This Realtime call has ended")
            self.input_needs_answer = bool(result.get("requiresImmediateRenegotiation"))
            return self._public_signal(result)

    async def _input_ready(self):
        async with self.input_lock:
            if self.closed:
                raise SfuError("This Realtime call has ended")
            if not self.input_session or self.input_needs_answer:
                raise SfuError("Microphone negotiation is incomplete")
            if self.input_adapter:
                return {"ok": True}
            result = await self._api("POST", "adapters/websocket/new", {"tracks": [{
                "location": "remote", "sessionId": self.input_session,
                "trackName": "microphone", "endpoint": self.input_endpoint,
                "outputCodec": "pcm"}]}, lambda body: self._record_adapters("input", None, body))
            adapter = self._one_track(result)
            if self.closed:
                self._schedule_cleanup()
                raise SfuError("This Realtime call has ended")
            self.input_adapter = _identifier(adapter.get("adapterId"))
            return {"ok": True}

    async def _subscribe(self, state):
        if state["receiver"]:
            raise SfuError("Assistant audio is already subscribed")
        result = await self._api("POST", "sessions/new", record=lambda body: self._record_session("output", state["generation"], body))
        receiver = _identifier(result.get("sessionId"))
        if not self._alive(state):
            self._schedule_cleanup()
            raise _StaleGeneration("That assistant audio generation has ended")
        state["receiver"] = receiver
        result = await self._api("POST", f"sessions/{receiver}/tracks/new", {"tracks": [{
            "location": "remote", "sessionId": state["publisher"],
            "trackName": state["track_name"]}]},
            lambda body: self._record_tracks(receiver, "output", state["generation"], body))
        self._one_track(result)
        if not self._alive(state):
            self._schedule_cleanup()
            raise _StaleGeneration("That assistant audio generation has ended")
        state["needs_answer"] = bool(result.get("requiresImmediateRenegotiation"))
        state["negotiated"] = not state["needs_answer"]
        return self._public_signal(result)

    def attach_socket(self, role, ws, generation=None):
        """Accept an already-authenticated SFU callback, owning its listeners."""
        state = self.output
        valid = not self.closed and (role == "input" and self.input_session or
                role == "output" and state and state["generation"] == generation and self._alive(state))
        if not valid:
            raise SfuError("This Realtime media endpoint is no longer active")
        if self.socket_factory:
            socket = self.socket_factory(ws, role, max_bytes=96000)
        else:
            from providers import _Socket
            socket = _Socket(ws, f"sfu_{role}", max_bytes=96000)
        if role == "input":
            if self.input_socket:
                self.input_socket.close()
            if self.input_pump:
                self.input_pump.cancel()
            self.input_socket = socket
            self.input_pump = self._task(self._read_input(socket))
        else:
            if state["socket"]:
                # Ingest has no automatic reconnect. Do not let a duplicate
                # callback replace a live generation's sender silently.
                socket.close()
                raise SfuError("Assistant media is already connected")
            state["socket"] = socket
            state["connected"].set()
            state["monitor"] = self._task(self._watch_output(state, socket))

    async def _read_input(self, socket):
        resampler = InputResampler()
        last_sequence = None
        try:
            while not self.closed and self.input_socket is socket:
                try:
                    message = await socket.receive(timeout=30)
                except asyncio.TimeoutError:
                    continue
                if isinstance(message, str):
                    raise SfuError("Realtime microphone sent an unexpected text message")
                sequence, _, payload = decode_packet(message)
                if sequence is not None and last_sequence is not None:
                    distance = (sequence - last_sequence) & 0xffffffff
                    if distance == 0 or distance > 0x7fffffff:
                        self.dropped_packets += 1
                        continue
                if sequence is not None:
                    last_sequence = sequence
                self.received_bytes += len(payload)
                pcm = resampler.convert(payload)
                if pcm:
                    result = self.on_audio(pcm)
                    if inspect.isawaitable(result):
                        await result
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self.closed and self.input_socket is socket:
                await self._emit({"type": "error", "message": "Realtime microphone media disconnected. End the call and start again.",
                                  "code": "sfu_input_disconnected", "recoverable": False})
        finally:
            socket.close()
            if self.input_socket is socket:
                self.input_socket = None

    async def _watch_output(self, state, socket):
        try:
            while self._alive(state):
                try:
                    await socket.receive(timeout=30)
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise
        except Exception:
            if self._alive(state) and not state["finished"]:
                state["failed"] = True
                state["ready"].set()
                await self._emit({"type": "error", "message": "Realtime assistant audio disconnected. End the call and start again.",
                                  "code": "sfu_output_disconnected", "recoverable": False})
        finally:
            socket.close()

    async def _prepare_output(self, state):
        generation = state["generation"]
        endpoint = self.output_endpoint_factory(generation)
        result = await self._api("POST", "adapters/websocket/new", {"tracks": [{
            "location": "local", "trackName": state["track_name"],
            "endpoint": endpoint, "inputCodec": "pcm"}]},
            lambda body: self._record_adapters("output", generation, body))
        adapter = self._one_track(result)
        state["publisher"] = _identifier(adapter.get("sessionId"))
        if not self._alive(state):
            self._schedule_cleanup()
            raise _StaleGeneration("That assistant audio generation has ended")
        await self._emit({"type": "sfu_track", "generation": generation})
        async def ready():
            await state["connected"].wait()
            await state["ready"].wait()
        await asyncio.wait_for(ready(), READY_TIMEOUT)
        if not self._alive(state):
            raise _StaleGeneration("That assistant audio generation has ended")
        if state["failed"]:
            raise SfuError("Realtime assistant audio disconnected")

    async def send_audio(self, pcm, generation):
        """Pace submitted audio; True means submitted, never heard or played."""
        if type(generation) is not int or generation < self.generation_floor or self.closed:
            return False
        pcm = bytes(pcm)
        if len(pcm) % 2 or len(pcm) > 48000:
            raise SfuError("Invalid assistant audio chunk")
        async with self.output_lock:
            if generation < self.generation_floor or self.closed:
                return False
            state = self.output
            if not state or state["generation"] != generation:
                await self.clear(generation)
                if self.closed or generation < self.generation_floor:
                    return False
                state = {"generation": generation, "retired": False, "failed": False, "finished": False,
                         "track_name": f"assistant-{generation}", "publisher": None,
                         "receiver": None, "needs_answer": False, "negotiated": False,
                         "socket": None, "monitor": None, "connected": asyncio.Event(),
                         "ready": asyncio.Event(), "signal_lock": asyncio.Lock(),
                         "resampler": OutputResampler(), "next_send": 0}
                self.output = state
                state["prepare"] = self._task(self._prepare_output(state))
            if state["finished"]:
                return False
            try:
                await asyncio.shield(state["prepare"])
            except asyncio.CancelledError:
                if not self._alive(state):
                    return False
                raise
            except _StaleGeneration:
                return False
            except asyncio.TimeoutError:
                await self.clear(generation + 1)
                raise SfuError("Realtime audio setup timed out") from None
            converted = state["resampler"].convert(pcm)
            for offset in range(0, len(converted), FRAME_BYTES):
                if not self._alive(state):
                    return False
                if state["failed"] or not state["socket"] or state["socket"].closed:
                    raise SfuError("Realtime assistant audio disconnected")
                delay = state["next_send"] - self.clock()
                if delay > 0:
                    await self.sleep(delay)
                if not self._alive(state):
                    return False
                frame = converted[offset:offset + FRAME_BYTES]
                state["socket"].send(encode_packet(frame))
                self.submitted_bytes += len(frame)
                state["next_send"] = self.clock() + len(frame) / 192000
            return True

    async def finish_generation(self, generation):
        """Send the example protocol's end-of-stream marker, not a receipt."""
        async with self.output_lock:
            state = self.output
            if not state or state["generation"] != generation or not self._alive(state) or state["finished"]:
                return False
            await asyncio.shield(state["prepare"])
            delay = state["next_send"] - self.clock()
            if delay > 0:
                await self.sleep(delay)
            if not self._alive(state) or not state["socket"] or state["socket"].closed:
                return False
            state["finished"] = True
            state["socket"].send(encode_packet(b""))
            return True

    async def clear(self, generation):
        if type(generation) is not int:
            raise SfuError("Invalid assistant audio generation")
        self.generation_floor = max(self.generation_floor, generation)
        state = self.output
        if state and state["generation"] < generation:
            state["retired"] = True
            self.output = None
            state["ready"].set()
            state["connected"].set()
            if state["socket"]:
                state["socket"].close()
            if state["monitor"]:
                state["monitor"].cancel()
            if not state["prepare"].done():
                state["prepare"].cancel()
        self._schedule_cleanup()
        self._changed()

    async def played(self, generation, chunk):
        # The SFU has no browser chunk receipts. Both routes share the speech
        # pipeline's context policy, independently of this flow-control hook.
        return

    def _changed(self):
        if self.on_cleanup_change:
            self.on_cleanup_change(self)

    def _has_retired_resources(self):
        return any(self._retired(owner) for owners in (self.adapters, self.tracks, self.sessions)
                   for owner in owners.values())

    def _cleanup_eligible(self):
        if self.cleanup_results:
            return True
        for adapter, owner in self.adapters.items():
            if self._retired(owner) and self.cleanup_attempts.get(("adapter", adapter), 0) < CLEANUP_ATTEMPTS:
                return True
        for session, owner in self.sessions.items():
            if self._retired(owner) and self.cleanup_attempts.get(("session", session), 0) < CLEANUP_ATTEMPTS:
                return True
        by_session = {}
        for (session, mid), owner in self.tracks.items():
            if self._retired(owner):
                by_session.setdefault(session, []).append(mid)
        return any(self.cleanup_attempts.get(("tracks", session, tuple(sorted(mids))), 0) < CLEANUP_ATTEMPTS
                   for session, mids in by_session.items())

    def _schedule_cleanup(self):
        if not self._has_retired_resources() or not self._cleanup_eligible():
            return
        if self.cleanup_task and not self.cleanup_task.done():
            return
        self.cleanup_task = self._task(self._run_cleanup())

        def drained(done):
            # A request can settle during the last retry pass, while this task
            # still owns the worker slot. Drain its recorded result afterward;
            # the per-operation retry counters remain unchanged.
            if not done.cancelled() and self.cleanup_results:
                self._schedule_cleanup()

        self.cleanup_task.add_done_callback(drained)

    async def _run_cleanup(self):
        for attempt in range(CLEANUP_ATTEMPTS):
            await self._cleanup_retired()
            self._changed()
            if not self._has_retired_resources() or not self._cleanup_eligible() or self.cleanup_requests:
                return
            if attempt + 1 < CLEANUP_ATTEMPTS:
                await self.sleep(.1 * (attempt + 1))

    async def _cleanup_request(self, key, path, method, payload):
        if key in self.cleanup_results:
            value = self.cleanup_results.pop(key)
            if isinstance(value, BaseException):
                raise value
            return value
        task = self.cleanup_requests.get(key)
        if task is None:
            if self.cleanup_attempts.get(key, 0) >= CLEANUP_ATTEMPTS:
                raise _CleanupExhausted("Realtime cleanup retry budget exhausted")
            self.cleanup_attempts[key] = self.cleanup_attempts.get(key, 0) + 1
            task = asyncio.create_task(self.request(f"{API_BASE}/{quote(self.app_id)}/{path}",
                                                   method, payload, self.secret))
            self.cleanup_requests[key] = task
            self._changed()

            def settled(done):
                self.cleanup_requests.pop(key, None)
                self.cleanup_results[key] = (SfuError("Realtime cleanup was canceled")
                                             if done.cancelled() else done.exception() or done.result())
                if not done.cancelled():
                    self._schedule_cleanup()
                    self._changed()

            task.add_done_callback(settled)
        done, _ = await asyncio.wait((task,), timeout=CLEANUP_REQUEST_TIMEOUT)
        if not done:
            raise SfuError("Realtime cleanup is still pending")
        value = self.cleanup_results.pop(key, None)
        if isinstance(value, BaseException):
            raise value
        return task.result() if value is None else value

    def _retired(self, role_generation):
        role, generation = role_generation
        return self.closed or role == "output" and generation < self.generation_floor

    async def _cleanup_retired(self):
        async with self.cleanup_lock:
            for adapter, owner in list(self.adapters.items()):
                if not self._retired(owner):
                    continue
                try:
                    # Already absent is an explicit result, not any arbitrary 503.
                    status, result = await self._cleanup_request(("adapter", adapter), "adapters/websocket/close",
                                                        "POST", {"tracks": [{"adapterId": adapter}]})
                    entries = result.get("tracks", [])
                    item = next((item for item in entries if item.get("adapterId") == adapter), None)
                    if item and (not item.get("errorCode") and 200 <= status < 300 or item.get("errorCode") == "adapter_not_found"):
                        self.adapters.pop(adapter, None)
                    else:
                        self.cleanup_failures += 1
                except _CleanupExhausted:
                    pass
                except Exception:
                    self.cleanup_failures += 1
            # A tracks/new request can succeed remotely while its response is
            # lost. Inspect only sessions this object allocated, so cleanup can
            # recover those unknown mids without trusting browser identifiers.
            inspected = set()
            for session, owner in list(self.sessions.items()):
                if not self._retired(owner):
                    continue
                try:
                    status, result = await self._cleanup_request(("session", session), f"sessions/{session}",
                                                        "GET", None)
                    if status == 410 and result.get("errorCode") == "session_error":
                        self.sessions.pop(session, None)
                        for key in [key for key in self.tracks if key[0] == session]:
                            self.tracks.pop(key, None)
                    elif 200 <= status < 300 and not result.get("errorCode"):
                        inspected.add(session)
                        for item in result.get("tracks", []):
                            if isinstance(item.get("mid"), str):
                                if item.get("status") == "inactive":
                                    self.tracks.pop((session, item["mid"]), None)
                                else:
                                    self.tracks[(session, item["mid"])] = owner
                    else:
                        self.cleanup_failures += 1
                except _CleanupExhausted:
                    pass
                except Exception:
                    self.cleanup_failures += 1
            by_session = {}
            for (session, mid), owner in list(self.tracks.items()):
                if self._retired(owner):
                    by_session.setdefault(session, []).append(mid)
            for session, mids in by_session.items():
                try:
                    status, result = await self._cleanup_request(("tracks", session, tuple(sorted(mids))), f"sessions/{session}/tracks/close",
                                                        "PUT", {"force": True, "tracks": [{"mid": mid} for mid in mids]})
                    if status == 410 and result.get("errorCode") == "session_error":
                        for mid in mids:
                            self.tracks.pop((session, mid), None)
                    elif 200 <= status < 300 and not result.get("errorCode"):
                        closed_mids = set()
                        for item in result.get("tracks", []):
                            if not item.get("errorCode") and item.get("mid") in mids:
                                self.tracks.pop((session, item["mid"]), None)
                                closed_mids.add(item["mid"])
                        if closed_mids != set(mids):
                            self.cleanup_failures += 1
                    else:
                        self.cleanup_failures += 1
                except _CleanupExhausted:
                    pass
                except Exception:
                    self.cleanup_failures += 1
            if not self.requests:
                for session in inspected:
                    if not any(key[0] == session for key in self.tracks):
                        self.sessions.pop(session, None)
            # Successful resources no longer need retry bookkeeping.
            for key in tuple(self.cleanup_attempts):
                kind, resource = key[:2]
                retained = (resource in self.adapters if kind == "adapter" else
                            resource in self.sessions if kind == "session" else
                            any(session == resource for session, mid in self.tracks))
                if not retained and key not in self.cleanup_requests:
                    self.cleanup_attempts.pop(key, None)
                    self.cleanup_results.pop(key, None)

    async def close(self):
        deadline = asyncio.get_running_loop().time() + CLOSE_TIMEOUT
        if not self.closed:
            self.closed = True
            if self.input_socket:
                self.input_socket.close()
                self.input_socket = None
            if self.output:
                self.output["retired"] = True
                self.output["ready"].set()
                self.output["connected"].set()
                if self.output["socket"]:
                    self.output["socket"].close()
            current = asyncio.current_task()
            for task in tuple(self.tasks):
                if task is not current and task is not self.cleanup_task:
                    task.cancel()
            pending = [task for task in self.tasks if task is not current and task is not self.cleanup_task]
            if pending:
                await asyncio.wait(pending, timeout=max(0, deadline - asyncio.get_running_loop().time()))
        # Local media is already isolated. Bound End's wait and retain every
        # request, resource identifier and cleanup result that settles later.
        while True:
            self._schedule_cleanup()
            pending = list(self.requests) + list(self.cleanup_requests.values())
            if self.cleanup_task and not self.cleanup_task.done():
                pending.append(self.cleanup_task)
            remaining = deadline - asyncio.get_running_loop().time()
            if not pending or remaining <= 0:
                break
            await asyncio.wait(pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
            if self.cleanup_task and self.cleanup_task.done() and not self.requests and not self.cleanup_requests:
                break
        self._changed()

    def cleanup_checkpoint(self):
        """Private durable state: unresolved identifiers, never endpoint secrets."""
        return {"adapters": list(self.adapters),
                "tracks": [{"session": session, "mid": mid} for session, mid in self.tracks],
                "sessions": list(self.sessions),
                "unconfirmed_allocations": self.unconfirmed_allocations,
                "pending_requests": len(self.requests),
                "pending_cleanup_requests": len(self.cleanup_requests),
                "cleanup_failures": self.cleanup_failures}

    def cleanup_pending(self):
        return bool(self.adapters or self.tracks or self.sessions or self.requests
                    or self.cleanup_requests or self.unconfirmed_allocations)

    def diagnostics(self):
        return {"sfu_input_bytes": self.received_bytes, "sfu_submitted_bytes": self.submitted_bytes,
                "sfu_dropped_packets": self.dropped_packets, "sfu_owned_adapters": len(self.adapters),
                "sfu_owned_tracks": len(self.tracks), "sfu_cleanup_failures": self.cleanup_failures,
                "sfu_owned_sessions": len(self.sessions),
                "sfu_unconfirmed_allocations": self.unconfirmed_allocations,
                "sfu_pending_requests": len(self.requests),
                "sfu_pending_cleanup_requests": len(self.cleanup_requests),
                "sfu_cleanup_tasks": int(bool(self.cleanup_task and not self.cleanup_task.done())),
                "sfu_cleanup_unresolved": bool(self._has_retired_resources() or self.unconfirmed_allocations
                                               or self.closed and (self.requests or self.cleanup_requests)),
                "sfu_delivery_confirmed": False}
