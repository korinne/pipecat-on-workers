"""GPT-OSS event and cancellation fixtures; model and JS I/O are explicit doubles."""
import asyncio
import importlib.util
import json
import pathlib
import sys
import types
import unittest
from types import SimpleNamespace as N
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gpt_stream import GPTStream, GPTStreamError


def event(delta=None, reason=None, **extra):
    return {"choices": [{"delta": delta or {}, "finish_reason": reason}], **extra}


def encoded(*events, done=True):
    value = "".join("data: " + json.dumps(item, ensure_ascii=False) + "\r\n\r\n" for item in events)
    return (value + ("data: [DONE]\r\n\r\n" if done else "")).encode()


class GPTStreamTests(unittest.TestCase):
    def decode(self, body, *, one_byte=False):
        parser, answer = GPTStream(), []
        chunks = (body[i:i+1] for i in range(len(body))) if one_byte else [body]
        for chunk in chunks:
            answer.extend(parser.feed(chunk))
        answer.extend(parser.feed(b"", eof=True))
        return parser, "".join(answer)

    def test_reasoning_and_native_response_never_become_answers(self):
        parser, answer = self.decode(encoded(
            event({"role": "assistant", "content": ""}),
            event({"reasoning_content": "private", "reasoning": "private"}, response="private"),
            event({"content": "Hello 🐈"}), event(reason="stop"),
            {"response": "not another answer", "usage": {"completion_tokens": 12}}), one_byte=True)
        self.assertEqual(answer, "Hello 🐈")
        self.assertEqual(parser.reasoning_characters, 7)
        self.assertEqual(parser.usage, {"completion_tokens": 12})

    def test_numeric_answer_text_is_preserved(self):
        _, answer = self.decode(encoded(event({"content": "1.50"}, response=1.5), event(reason="stop")))
        self.assertEqual(answer, "1.50")

    def test_explicit_stop_can_complete_without_done(self):
        _, answer = self.decode(encoded(event({"content": "Yes."}), event(reason="stop"), done=False))
        self.assertEqual(answer, "Yes.")

    def test_incomplete_outcomes_are_distinct(self):
        for body, outcome in (
            (encoded(event(reason="stop")), "empty_answer"),
            (encoded(event({"content": "  "}), event(reason="stop")), "empty_answer"),
            (encoded(event({"content": "partial"}), event(reason="length")), "token_limit"),
            (encoded(event({"content": "partial"}), event(reason="model_length")), "token_limit"),
            (encoded(event({"content": "partial"})), "missing_completion"),
            (encoded(event({"content": "partial"}), done=False), "unexpected_eof"),
            (encoded(event(reason="tool_calls")), "unexpected_finish"),
            (encoded({"error": {"message": "PRIVATE"}}), "provider_error"),
        ):
            with self.subTest(outcome=outcome):
                with self.assertRaises(GPTStreamError) as raised:
                    self.decode(body)
                self.assertEqual(raised.exception.outcome, outcome)
                self.assertNotIn("PRIVATE", str(raised.exception))

    def test_live_one_token_protocol_leak_is_suppressed(self):
        for fragments in (("<|channel|>",), ("<", "|channel", "|>")):
            parser, answer = GPTStream(), []
            for text in fragments:
                answer.extend(parser.feed(encoded(event({"content": text}), done=False)))
            self.assertEqual(answer, [])
            with self.assertRaises(GPTStreamError) as raised:
                list(parser.feed(encoded(event(reason="length"))))
            self.assertEqual(raised.exception.outcome, "token_limit")
            self.assertTrue(parser.protocol_seen)

    def test_protocol_text_cannot_claim_normal_completion(self):
        with self.assertRaises(GPTStreamError) as raised:
            self.decode(encoded(event({"content": "<|channel|>analysis"}), event(reason="stop")))
        self.assertEqual(raised.exception.outcome, "invalid_stream")

    def test_split_less_than_remains_answer_text(self):
        _, answer = self.decode(encoded(event({"content": "2 <"}), event({"content": " 3."}), event(reason="stop")))
        self.assertEqual(answer, "2 < 3.")

    def test_multiline_sse_and_comments(self):
        body = b': keepalive\r\nevent: message\r\ndata: {"choices":\r\ndata: [{"delta":{"content":"Okay."},"finish_reason":"stop"}]}\r\n\r\ndata: [DONE]\r\n\r\n'
        _, answer = self.decode(body, one_byte=True)
        self.assertEqual(answer, "Okay.")

    def test_malformed_or_oversize_stream_fails(self):
        for body in (b'data: not-json\n\n', b'data: []\n\n', b'data: \xff\n\n', b'data: '+b'x'*65537):
            with self.subTest(size=len(body)):
                with self.assertRaises(GPTStreamError):
                    self.decode(body)

    def test_answer_after_finish_fails(self):
        with self.assertRaises(GPTStreamError):
            self.decode(encoded(event({"content": "First"}), event(reason="stop"), event({"content": "late"})))


class Reader:
    __hash__ = None
    def __init__(self, chunks=(), *, hold=False):
        self.chunks, self.hold = list(chunks), hold
        self.reading = asyncio.Event()
        self.canceled = self.released = False
    async def read(self):
        if self.chunks:
            return N(done=False, value=self.chunks.pop(0))
        self.reading.set()
        if self.hold:
            await asyncio.Event().wait()
        return N(done=True)
    async def cancel(self, *_):
        self.canceled = True
    def releaseLock(self):
        self.released = True


class GPTProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.controllers = []
        def controller():
            value = N(signal=N(), aborted=False)
            def abort(*_): value.aborted = True
            value.abort = abort
            self.controllers.append(value)
            return value
        js = types.ModuleType("js")
        js.Object = N(fromEntries=lambda v: v)
        js.Uint8Array = N(new=lambda v: N(to_py=lambda: memoryview(v)))
        js.AbortController = N(new=controller)
        ffi = types.ModuleType("pyodide.ffi")
        ffi.to_js = lambda v, **kw: v
        ffi.create_proxy = lambda v: v
        self.modules = patch.dict(sys.modules, {"js": js, "pyodide": types.ModuleType("pyodide"), "pyodide.ffi": ffi})
        self.modules.start()
        spec = importlib.util.spec_from_file_location("gpt_test_providers", ROOT / "src/providers.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.calls = []
        self.reader = Reader()
        async def run(*args):
            self.calls.append(args)
            return N(getReader=lambda: self.reader, cancel=self.reader.cancel)
        self.provider = self.module.WorkersProviders(N(AI=N(run=run)), lambda _: None)

    async def asyncTearDown(self):
        await self.provider.close()
        self.modules.stop()

    async def test_request_contract_and_complete_cleanup(self):
        self.reader = Reader([encoded(event({"content": "Four."}), event(reason="stop"))])
        self.assertEqual([token async for token in self.provider.generate([])], ["Four."])
        model, inputs, options = self.calls[0]
        self.assertEqual(model, "@cf/openai/gpt-oss-120b")
        self.assertEqual(inputs, {"messages": [], "stream": True, "max_tokens": 2048, "reasoning_effort": "low"})
        self.assertIs(options["signal"], self.controllers[0].signal)
        self.assertTrue(self.controllers[0].aborted and self.reader.canceled and self.reader.released)
        self.assertEqual(self.provider.last_generation["outcome"], "completed")

    async def test_cancel_before_headers_disposes_late_result(self):
        gate, started = asyncio.Event(), asyncio.Event()
        async def run(*args):
            started.set()
            await gate.wait()
            return N(getReader=lambda: self.reader, cancel=self.reader.cancel)
        self.provider.env.AI.run = run
        stream = self.provider.generate([])
        opening = asyncio.create_task(anext(stream))
        await started.wait()
        opening.cancel()
        with self.assertRaises(asyncio.CancelledError): await opening
        self.assertTrue(self.controllers[0].aborted)
        self.assertEqual(self.provider.last_generation["outcome"], "canceled")
        self.assertEqual(len(self.provider.requests), 1)
        gate.set()
        for _ in range(20):
            await asyncio.sleep(0)
        self.assertTrue(self.reader.canceled)
        self.assertEqual(len(self.provider.requests), 0)

    async def test_canceled_request_rejection_is_consumed(self):
        gate, started = asyncio.Event(), asyncio.Event()
        errors = []
        loop = asyncio.get_running_loop()
        old_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _, context: errors.append(context))
        async def run(*args):
            started.set()
            await gate.wait()
            raise RuntimeError("Expected abort rejection")
        self.provider.env.AI.run = run
        try:
            opening = asyncio.create_task(anext(self.provider.generate([])))
            await started.wait()
            opening.cancel()
            with self.assertRaises(asyncio.CancelledError): await opening
            gate.set()
            for _ in range(20): await asyncio.sleep(0)
            self.assertEqual(errors, [])
            self.assertEqual(len(self.provider.requests), 0)
        finally:
            loop.set_exception_handler(old_handler)

    async def test_cancel_during_reasoning_before_answer(self):
        self.reader = Reader([encoded(event({"reasoning_content": "private"}), done=False)], hold=True)
        stream = self.provider.generate([])
        reading = asyncio.create_task(anext(stream))
        await self.reader.reading.wait()
        reading.cancel()
        with self.assertRaises(asyncio.CancelledError): await reading
        self.assertTrue(self.controllers[0].aborted and self.reader.canceled and self.reader.released)
        self.assertEqual(self.provider.last_generation["answer_characters"], 0)

    async def test_cancel_after_answer(self):
        self.reader = Reader([encoded(event({"content": "First."}), done=False)], hold=True)
        stream = self.provider.generate([])
        self.assertEqual(await anext(stream), "First.")
        await stream.aclose()
        self.assertEqual(self.provider.last_generation["outcome"], "canceled")
        self.assertTrue(self.reader.canceled and self.reader.released)

    async def test_unexpected_eof_is_failure(self):
        self.reader = Reader([encoded(event({"content": "Partial"}), done=False)])
        with self.assertRaises(self.module.ProviderError) as raised:
            _ = [token async for token in self.provider.generate([])]
        self.assertEqual(raised.exception.outcome, "unexpected_eof")
        self.assertEqual(self.provider.last_generation["outcome"], "unexpected_eof")
        self.assertTrue(self.reader.canceled and self.reader.released)

    async def test_provider_failure_is_sanitized(self):
        async def run(*_): raise RuntimeError("PRIVATE CREDENTIAL")
        self.provider.env.AI.run = run
        with self.assertRaises(self.module.ProviderError) as raised:
            _ = [token async for token in self.provider.generate([])]
        self.assertNotIn("PRIVATE", str(raised.exception))
        self.assertTrue(self.controllers[0].aborted)
