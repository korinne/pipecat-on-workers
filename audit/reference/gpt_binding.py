"""Isolated Python/Workers AI probe. Never deploy as an application route.

Run locally with gpt.wrangler.jsonc and an existing authenticated AI binding.
Only fixed synthetic prompts are sent. Reasoning text is counted, not saved.
This harness has not run on Workers: authentication was unavailable in Task 1.
"""
import asyncio
import codecs
import contextlib
import json
import platform
import time
from urllib.parse import urlparse

from js import AbortController, Object, Uint8Array
from pyodide.ffi import to_js
from workers import Response, WorkerEntrypoint

MODEL = "@cf/openai/gpt-oss-120b"


def js(value):
    return to_js(value, dict_converter=Object.fromEntries)


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        mode = urlparse(str(request.url)).path.strip("/") or "normal"
        if mode not in ("normal", "one-token", "invalid-budget", "preabort", "cancel-after-first-event"):
            return Response("Unknown probe", status=404)
        if request.method != "POST":
            return Response("POST a fixed probe case", status=405)
        inputs = {"messages": [{"role": "system", "content": "Answer in one brief sentence."},
                                {"role": "user", "content": "What is two plus two?"}],
                  "stream": True, "reasoning_effort": "low", "max_tokens": 2048}
        if mode == "one-token":
            inputs["max_tokens"] = 1
        elif mode == "invalid-budget":
            inputs["max_tokens"] = "invalid-probe-value"
        controller = AbortController.new()
        if mode == "preabort":
            controller.abort("Task 1 pre-aborted request")
        report = {"mode": mode, "model": MODEL, "inputs": inputs,
                  "python": platform.python_version(), "event_shapes": [],
                  "answer_text": "", "reasoning_characters": 0,
                  "finish_reasons": [], "usage": [], "sse_done": False, "eof": False,
                  "reader_cancel_completed": False, "reader_released": False,
                  "remote_compute_canceled": "unverified"}
        reader = None
        started = time.monotonic()
        first_answer = None
        try:
            result = await asyncio.wait_for(
                self.env.AI.run(MODEL, js(inputs), js({"signal": controller.signal})), 45)
            raw = getattr(result, "js_object", result)
            stream = raw.body if hasattr(raw, "body") else raw
            report["return_has_reader"] = hasattr(stream, "getReader")
            if not report["return_has_reader"]:
                report["outcome"] = "nonstream_response"
            else:
                reader = stream.getReader()
                decoder, pending, count = codecs.getincrementaldecoder("utf-8")(), "", 0
                while count < 8192 and time.monotonic() - started < 45:
                    item = await asyncio.wait_for(reader.read(), max(0.01, 45 - (time.monotonic() - started)))
                    if item.done:
                        report["eof"] = True
                        break
                    pending += decoder.decode(bytes(Uint8Array.new(item.value).to_py()))
                    if len(pending) > 262144:
                        raise ValueError("SSE buffer limit")
                    lines = pending.split("\n")
                    pending = lines.pop()
                    for line in lines:
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if not payload:
                            continue
                        if payload == "[DONE]":
                            report["sse_done"] = True
                            continue
                        event = json.loads(payload)
                        count += 1
                        choice = (event.get("choices") or [{}])[0]
                        delta = choice.get("delta") or {}
                        shape = {"keys": sorted(event), "delta_keys": sorted(delta)}
                        if isinstance(event.get("type"), str):
                            shape["type"] = event["type"]
                        if shape not in report["event_shapes"]:
                            report["event_shapes"].append(shape)
                        # Whitelist candidate answer text; never echo a raw event or raw CoT.
                        text = delta.get("content")
                        if isinstance(text, str) and text:
                            if first_answer is None:
                                first_answer = time.monotonic() - started
                            report["answer_text"] += text
                        for key in ("reasoning_content", "reasoning"):
                            if isinstance(delta.get(key), str):
                                report["reasoning_characters"] += len(delta[key])
                        reason = choice.get("finish_reason") or event.get("finish_reason")
                        if reason:
                            report["finish_reasons"].append(reason)
                        if isinstance(event.get("usage"), dict):
                            report["usage"].append(event["usage"])
                        if event.get("error"):
                            report["provider_error_event"] = True
                    if report["sse_done"] or (mode == "cancel-after-first-event" and count):
                        break
                report["event_count"] = count
                report["outcome"] = "captured"
        except Exception as error:
            report["outcome"] = "exception"
            report["exception_type"] = type(error).__name__
        finally:
            # Abort support is source-verified but remains untested in the target runtime.
            controller.abort("Task 1 probe ended")
            if reader is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(reader.cancel("Task 1 probe ended"), 2)
                    report["reader_cancel_completed"] = True
                with contextlib.suppress(Exception):
                    reader.releaseLock()
                    report["reader_released"] = True
            report["elapsed_seconds"] = time.monotonic() - started
            report["first_answer_seconds"] = first_answer
        return Response(json.dumps(report), headers={"Content-Type": "application/json", "Cache-Control": "no-store"})
