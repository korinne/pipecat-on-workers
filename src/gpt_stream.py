"""Bounded answer-only decoder for the Workers AI GPT-OSS SSE interface."""
import codecs
import json

MODEL = "@cf/openai/gpt-oss-120b"
MAX_TOKENS = 2048
REASONING_EFFORT = "low"


class GPTStreamError(RuntimeError):
    """A sanitized, classified generation failure."""

    def __init__(self, outcome):
        self.outcome = outcome
        messages = {
            "token_limit": "The answer reached its generation limit before completion. Please try a shorter question.",
            "empty_answer": "The model completed without an answer. Please try again.",
            "unexpected_eof": "The answer stream ended before completion. Please try again.",
            "missing_completion": "The answer stream did not report a completion reason. Please try again.",
            "provider_error": "The model could not complete this answer. Please try again.",
            "unexpected_finish": "The model returned an unsupported completion outcome. Please try again.",
            "invalid_stream": "The model returned an invalid answer stream. Please try again.",
            "stream_limit": "The answer stream exceeded its safety bound. Please try a shorter question.",
        }
        super().__init__(messages.get(outcome, "The model request failed. Please try again."))


class GPTStream:
    """Only choices[0].delta.content is speech; native response is not a fallback.

    Reasoning text is counted and immediately discarded. Success requires an
    explicit stop and a nonempty answer. EOF alone or [DONE] alone is not success.
    """

    def __init__(self):
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.pending = ""
        self.data = []
        self.event_size = 0
        self.bytes = 0
        self.events = 0
        self.answer_characters = 0
        self.has_answer = False
        self.answer_pending = ""
        self.protocol_seen = False
        self.reasoning_characters = 0
        self.finish_reason = None
        self.done = False
        self.usage = None

    def feed(self, chunk, *, eof=False):
        self.bytes += len(chunk)
        if self.bytes > 1048576:
            raise GPTStreamError("stream_limit")
        try:
            self.pending += self.decoder.decode(chunk, final=eof)
        except UnicodeError:
            raise GPTStreamError("invalid_stream") from None
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            yield from self._line(line.rstrip("\r"))
        if len(self.pending) + self.event_size > 65536:
            raise GPTStreamError("stream_limit")
        if eof:
            if self.pending:
                yield from self._line(self.pending.rstrip("\r"))
                self.pending = ""
            # A final explicit finish event can establish completion even when
            # the provider closes without the optional SSE [DONE] sentinel.
            yield from self._line("")
            self.finish(eof=True)

    def _line(self, line):
        if not line:
            if not self.data:
                return []
            payload = "\n".join(self.data)
            self.data = []
            self.event_size = 0
            return self._event(payload)
        if line.startswith("data:"):
            value = line[5:]
            if value.startswith(" "):
                value = value[1:]
            self.data.append(value)
            self.event_size += len(value)
            if self.event_size > 65536:
                raise GPTStreamError("stream_limit")
        return []

    def _event(self, payload):
        if self.done:
            raise GPTStreamError("invalid_stream")
        if payload == "[DONE]":
            self.done = True
            self.finish()
            return []
        try:
            event = json.loads(payload)
        except (ValueError, TypeError):
            raise GPTStreamError("invalid_stream") from None
        if not isinstance(event, dict):
            raise GPTStreamError("invalid_stream")
        self.events += 1
        if self.events > 16384:
            raise GPTStreamError("stream_limit")
        if "error" in event or event.get("type") in ("error", "response.failed"):
            raise GPTStreamError("provider_error")
        if isinstance(event.get("usage"), dict):
            # Keep only numeric token totals, never arbitrary provider content.
            self.usage = {key: value for key, value in event["usage"].items()
                          if key in ("prompt_tokens", "completion_tokens", "total_tokens")
                          and type(value) is int and value >= 0}
        choices = event.get("choices")
        if choices is None or choices == []:
            return []
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise GPTStreamError("invalid_stream")
        choice = choices[0]
        delta = choice.get("delta") or {}
        if not isinstance(delta, dict):
            raise GPTStreamError("invalid_stream")
        for key in ("reasoning_content", "reasoning"):
            if isinstance(delta.get(key), str):
                self.reasoning_characters += len(delta[key])
                break
        if delta.get("role") not in (None, "assistant"):
            raise GPTStreamError("invalid_stream")
        text = delta.get("content")
        if text is not None and not isinstance(text, str):
            raise GPTStreamError("invalid_stream")
        if text and self.finish_reason is not None:
            raise GPTStreamError("invalid_stream")
        if text:
            text = self.answer_pending + text
            self.answer_pending = ""
            if "<|" in text:
                self.protocol_seen = True
            if self.protocol_seen:
                text = ""
            elif text.endswith("<"):
                self.answer_pending = "<"
                text = text[:-1]
        if text:
            self.answer_characters += len(text)
            self.has_answer |= bool(text.strip())
        reason = choice.get("finish_reason") or event.get("finish_reason")
        if reason:
            if not isinstance(reason, str):
                raise GPTStreamError("invalid_stream")
            if self.finish_reason is not None and reason != self.finish_reason:
                raise GPTStreamError("invalid_stream")
            self.finish_reason = reason
            if reason in ("length", "model_length", "max_tokens"):
                raise GPTStreamError("token_limit")
            if reason != "stop":
                raise GPTStreamError("unexpected_finish")
            if self.protocol_seen or self.answer_pending:
                raise GPTStreamError("invalid_stream")
        return [text] if text else []

    def finish(self, *, eof=False):
        if self.finish_reason != "stop":
            raise GPTStreamError("unexpected_eof" if eof else "missing_completion")
        if not self.has_answer:
            raise GPTStreamError("empty_answer")

    def diagnostics(self):
        return {"events": self.events, "answer_characters": self.answer_characters,
                "reasoning_characters": self.reasoning_characters,
                "finish_reason": self.finish_reason, "sse_done": self.done,
                "usage": self.usage, "protocol_text_rejected": self.protocol_seen}
