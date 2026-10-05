"""Isolated Nova-3 / hosted Smart Turn request capture; not an application route.

Start with task2_turn.wrangler.jsonc, using the repository's locked Wrangler.
POST JSON {"pcm16_base64": "...", "fixture_label": "synthetic sentence"} to
/nova or /smart-turn on the local server. Supply authorized synthetic PCM16 LE,
16 kHz mono, at most eight seconds. Nova adds one second of generated silence,
then sends Finalize. The report records only selected event fields and timings.

No model calls ran during Task 2: existing authentication was unavailable.
Syntax checks do not establish Python Workers or model compatibility.
"""

import asyncio
import base64
import contextlib
import hashlib
import json
import math
import platform
import struct
import time
from urllib.parse import urlparse

from js import AbortController, JSON, Object
from pyodide.ffi import create_proxy, to_js
from workers import Response, WorkerEntrypoint

RATE = 16000
MAX_PCM_BYTES = RATE * 2 * 8
MAX_EVENTS = 128
MAX_EVENT_CHARS = 65536
# WebSocket parameters are strings, including values typed as booleans or
# numbers in the REST model catalog.
NOVA_INPUTS = {"encoding": "linear16", "sample_rate": "16000", "channels": "1",
               "language": "en-US", "interim_results": "true", "vad_events": "true",
               "endpointing": "200"}


def js(value):
    return to_js(value, dict_converter=Object.fromEntries)


def raw(value):
    return getattr(value, "js_object", value)


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def event_fields(event):
    """Omit provider identifiers, raw errors and unbounded alternative payloads."""
    result = {"type": str(event.get("type", "unknown"))[:64]}
    for key in ("start", "duration", "timestamp", "last_word_end"):
        if finite_number(event.get(key)):
            result[key] = event[key]
    for key in ("is_final", "speech_final", "from_finalize"):
        if type(event.get(key)) is bool:
            result[key] = event[key]
    channel = event.get("channel")
    alternatives = channel.get("alternatives", []) if isinstance(channel, dict) else []
    if isinstance(alternatives, list) and alternatives:
        alternative = alternatives[0]
        if isinstance(alternative, dict):
            transcript = alternative.get("transcript", "")
            if isinstance(transcript, str):
                result["transcript"] = transcript[:2048]
                result["transcript_truncated"] = len(transcript) > 2048
            words = alternative.get("words")
            result["words"] = [
                {key: word[key] for key in ("start", "end") if finite_number(word.get(key))}
                for word in (words if isinstance(words, list) else [])[:256]
                if isinstance(word, dict)
            ]
    return result


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        url = urlparse(str(request.url))
        if url.hostname not in ("localhost", "127.0.0.1", "::1"):
            return Response("Local probe only", status=403)
        if request.method != "POST" or url.path not in ("/nova", "/smart-turn"):
            return Response("POST a synthetic PCM fixture to /nova or /smart-turn", status=405)
        try:
            body = await asyncio.wait_for(request.text(), 2)
            if len(body) > 350000:
                raise ValueError("Request too large")
            payload = json.loads(body)
            pcm = base64.b64decode(payload["pcm16_base64"], validate=True)
            if not pcm or len(pcm) % 2 or len(pcm) > MAX_PCM_BYTES:
                raise ValueError("Invalid PCM fixture size")
            if pcm[:4] in (b"RIFF", b"OggS"):
                raise ValueError("Expected raw PCM, not a container")
        except Exception:
            return Response("Expected nonempty raw PCM16 LE, 16 kHz mono, at most eight seconds", status=400)
        report = {"task": 2, "mode": url.path[1:], "python": platform.python_version(),
                  "fixture_label": str(payload.get("fixture_label", "unspecified"))[:120],
                  "fixture_sha256": hashlib.sha256(pcm).hexdigest(),
                  "fixture_seconds": len(pcm) / (RATE * 2),
                  "audio_format": "PCM16 LE, 16000 Hz, mono", "events": [],
                  "remote_compute_canceled": "unverified"}
        started = time.monotonic()
        controller = AbortController.new()
        try:
            if url.path == "/smart-turn":
                report["model"] = "@cf/pipecat-ai/smart-turn-v2"
                floats = b"".join(struct.pack("<f", sample[0] / 32768)
                                  for sample in struct.iter_unpack("<h", pcm))
                response = await asyncio.wait_for(self.env.AI.run(
                    report["model"], js({"audio": base64.b64encode(floats).decode("ascii"),
                                          "dtype": "float32"}),
                    js({"signal": controller.signal})), 10)
                value = raw(response)
                if hasattr(value, "to_py"):
                    value = value.to_py()
                report["result_keys"] = sorted(value) if isinstance(value, dict) else []
                if isinstance(value, dict) and type(value.get("is_complete")) is bool:
                    report["is_complete"] = value["is_complete"]
                    if finite_number(value.get("probability")):
                        report["probability"] = value["probability"]
                    report["outcome"] = "decision_received"
                else:
                    report["outcome"] = "unexpected_response_shape"
            else:
                await self.nova(pcm, report, controller, started)
        except Exception as error:
            report["outcome"] = "exception"
            report["exception_type"] = type(error).__name__
        finally:
            controller.abort("Task 2 local probe ended")
            report["elapsed_seconds"] = time.monotonic() - started
        return Response(json.dumps(report), headers={"Content-Type": "application/json",
                                                     "Cache-Control": "no-store"})

    async def nova(self, pcm, report, controller, started):
        report.update({"model": "@cf/deepgram/nova-3", "inputs": NOVA_INPUTS,
                       "appended_silence_seconds": 1, "finalize_sent": False,
                       "from_finalize_received": False, "event_limit_reached": False})
        response = raw(await asyncio.wait_for(self.env.AI.run(
            report["model"], js(NOVA_INPUTS),
            js({"websocket": True, "signal": controller.signal})), 10))
        ws = getattr(response, "webSocket", None)
        if not ws:
            status = getattr(response, "status", None)
            report.update({"outcome": "websocket_not_returned",
                           "http_status": status if finite_number(status) else None})
            return
        audio_sent = 0
        listeners = []
        finished = asyncio.Event()

        def message(event):
            if len(report["events"]) >= MAX_EVENTS:
                report["event_limit_reached"] = True
                finished.set()
                return
            try:
                if not isinstance(event.data, str) or len(event.data) > MAX_EVENT_CHARS:
                    raise ValueError("Unexpected event")
                item = event_fields(json.loads(event.data))
                item.update({"received_after_seconds": time.monotonic() - started,
                             "audio_sent_seconds": audio_sent / (RATE * 2)})
                report["events"].append(item)
                if item.get("from_finalize"):
                    report["from_finalize_received"] = True
                    finished.set()
            except Exception:
                report["event_parse_failure"] = True
                finished.set()

        def closed(event):
            report["socket_closed"] = True
            finished.set()

        def failed(event):
            report["socket_error"] = True
            finished.set()

        try:
            for name, callback in (("message", message), ("close", closed), ("error", failed)):
                proxy = create_proxy(callback)
                listeners.append((name, proxy))
                ws.addEventListener(name, proxy)
            ws.accept()
            report["connected_after_seconds"] = time.monotonic() - started
            audio = pcm + bytes(RATE * 2)
            for offset in range(0, len(audio), 640):
                if finished.is_set():
                    break
                chunk = audio[offset:offset + 640]
                ws.send(js(chunk))
                audio_sent += len(chunk)
                await asyncio.sleep(len(chunk) / (RATE * 2))
            if not finished.is_set():
                ws.send(json.dumps({"type": "Finalize"}))
                report["finalize_sent"] = True
                report["finalize_after_seconds"] = time.monotonic() - started
                try:
                    await asyncio.wait_for(finished.wait(), 3)
                except asyncio.TimeoutError:
                    report["finalize_wait_elapsed"] = True
            report["outcome"] = "capture_finished"
        finally:
            with contextlib.suppress(Exception):
                ws.close(1000, "Task 2 capture complete")
            for name, proxy in listeners:
                with contextlib.suppress(Exception):
                    ws.removeEventListener(name, proxy)
                with contextlib.suppress(Exception):
                    proxy.destroy()
