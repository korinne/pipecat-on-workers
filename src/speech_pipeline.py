"""Workers AI services through pinned Pipecat speech/output/context components."""
import asyncio
import contextlib
import copy
import re
import time

from pipecat.adapters.base_llm_adapter import BaseLLMAdapter
from pipecat.frames.frames import (Frame, InterruptionFrame, LLMContextFrame,
    LLMFullResponseStartFrame, LLMFullResponseEndFrame, LLMTextFrame,
    TTSAudioRawFrame, TTSTextFrame, TTSStartedFrame, TTSStoppedFrame, SystemFrame)
from pipecat.services.llm_service import LLMService
from pipecat.services.tts_service import TTSService, TextAggregationMode
from pipecat.services.settings import LLMSettings, TTSSettings
from pipecat.processors.frame_processor import FrameDirection
from pipecat.processors.aggregators.llm_response_universal import LLMAssistantAggregator
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import TransportParams

MAX_QUEUED_AUDIO_BYTES = 384000
MAX_ANSWER_CHARS = 16384
OUTPUT_FINISH_TIMEOUT = 12


class WorkersContextAdapter(BaseLLMAdapter):
    @property
    def id_for_llm_specific_messages(self):
        return "workers-ai"

    def get_llm_invocation_params(self, context, **kwargs):
        return {"messages": copy.deepcopy(context.get_messages())}

    def to_provider_tools_format(self, tools_schema):
        raise ValueError("Model tool calling is not selected")

    def get_messages_for_logging(self, context):
        return []  # Conversation text stays out of provider logs.


class WorkersLLMService(LLMService):
    adapter_class = WorkersContextAdapter

    def __init__(self, session):
        super().__init__(settings=LLMSettings(model="@cf/openai/gpt-oss-120b", system_instruction=None, temperature=None, max_tokens=2048, top_p=None, top_k=None, frequency_penalty=None, presence_penalty=None, seed=None, filter_incomplete_user_turns=False, user_turn_completion_config=None))
        self.session = session

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, InterruptionFrame):
            await self.session.invalidate("pipecat_interruption")
        elif isinstance(frame, LLMContextFrame) and direction == FrameDirection.DOWNSTREAM:
            generation = None
            try:
                await asyncio.wait_for(self.session.context_ready.wait(), 5)
                generation = self.session.generation + 1
                await self.respond(generation)
            except asyncio.CancelledError:
                self.session.measure("generation_cancelled", {})
                raise
            except Exception as exc:
                # Cleanup can raise while Pipecat is cancelling this processor.
                # Preserve cancellation instead of reporting a new model error.
                if asyncio.current_task().cancelling():
                    raise asyncio.CancelledError() from exc
                if generation is None or self.session.is_current(generation):
                    await self.session.fail_response("generation", exc)
            return
        await self.push_frame(frame, direction)

    async def respond(self, generation):
        session = self.session
        session.generation = generation
        session.responding = True
        session.response_started = time.monotonic()
        await session.persist()
        messages = self.get_llm_adapter().get_llm_invocation_params(session.context)["messages"]
        user_text = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        session.measure("generation_started", {"generation": generation})
        if re.search(r"\b(appointments?|availability|available times|free slots)\b", user_text, re.I):
            await session.send({"type": "status", "state": "tool", "generation": generation})
            result = await session.tool()
            session.measure("tool_completed", {"name": "lookup_availability", "fictional": True})
            messages.append({"role": "system", "content": f"lookup_availability returned: {result}"})
        await session.send({"type": "status", "state": "thinking", "generation": generation})

        async def emit(frame):
            frame.metadata["generation"] = generation
            await self.push_frame(frame)

        await emit(LLMFullResponseStartFrame())
        stream = session.provider.generate(messages)
        chars = 0
        try:
            async for token in stream:
                if not session.is_current(generation):
                    return
                chars += len(token)
                if chars > MAX_ANSWER_CHARS:
                    raise ValueError("Answer exceeded the text limit")
                await emit(LLMTextFrame(token))
        finally:
            await stream.aclose()
        if not session.is_current(generation):
            return
        if chars == 0:
            raise ValueError("Model returned no answer")
        await emit(LLMFullResponseEndFrame())


class WorkersTTSService(TTSService):
    def __init__(self, session):
        super().__init__(sample_rate=24000, push_start_frame=True, push_stop_frames=True,
            push_text_frames=True, text_aggregation_mode=TextAggregationMode.SENTENCE,
            settings=TTSSettings(model="@cf/deepgram/aura-2-en", voice="luna", language=None))
        self.session = session
        self.generation = None
        self.context_generations = {}

    async def process_frame(self, frame, direction):
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, LLMFullResponseStartFrame):
            self.generation = frame.metadata.get("generation")
        if (direction == FrameDirection.DOWNSTREAM and not isinstance(frame, SystemFrame)
                and frame.metadata.get("generation") is not None
                and not self.session.is_current(frame.metadata["generation"])):
            return
        await super().process_frame(frame, direction)

    async def on_turn_context_created(self, context_id):
        self.context_generations.clear()
        self.context_generations[context_id] = self.generation

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        if direction == FrameDirection.DOWNSTREAM and not isinstance(frame, SystemFrame):
            context_id = getattr(frame, "context_id", None)
            if context_id is not None and context_id not in self.context_generations:
                return
            generation = frame.metadata.get("generation", self.context_generations.get(context_id, self.generation))
            if generation is not None:
                frame.metadata["generation"] = generation
                if not self.session.is_current(generation):
                    return
        await super().push_frame(frame, direction)

    async def run_tts(self, text, context_id):
        generation = self.context_generations.get(context_id)
        if not self.session.is_current(generation):
            raise asyncio.CancelledError()
        await self.session.send({"type": "status", "state": "speaking", "generation": generation})
        stream = self.session.provider.synthesize(text)
        received = 0
        try:
            async for pcm in stream:
                if not pcm:
                    continue
                if len(pcm) % 2:
                    raise ValueError("Speech provider returned incomplete PCM16")
                for start in range(0, len(pcm), 48000):
                    chunk = pcm[start:start + 48000]
                    await self.session.reserve_output(len(chunk), generation)
                    received += len(chunk)
                    yield TTSAudioRawFrame(chunk, sample_rate=24000, num_channels=1, context_id=context_id)
            if received == 0:
                raise ValueError("Speech provider returned no audio")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Abort before the TTS base can append failed sentence text. The
            # standard assistant aggregator still retains earlier progressed text.
            if self.session.is_current(generation):
                await self.session.fail_response("synthesis", exc)
            raise asyncio.CancelledError() from exc
        finally:
            await stream.aclose()


class WorkersOutputTransport(BaseOutputTransport):
    def __init__(self, session):
        super().__init__(TransportParams(audio_out_enabled=True, audio_out_sample_rate=24000,
            audio_out_channels=1, audio_out_10ms_chunks=2, audio_out_end_silence_secs=0,
            audio_out_write_timeout_secs=40))
        self.session = session
        self.output_generation = None
        self.next_write_at = 0

    async def start(self, frame):
        await super().start(frame)
        await self.set_transport_ready(frame)

    async def process_frame(self, frame, direction):
        if (direction == FrameDirection.DOWNSTREAM and not isinstance(frame, SystemFrame)
                and frame.metadata.get("generation") is not None
                and not self.session.is_current(frame.metadata["generation"])):
            return
        await super().process_frame(frame, direction)

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        if direction == FrameDirection.DOWNSTREAM:
            generation = frame.metadata.get("generation")
            if generation is not None and not isinstance(frame, SystemFrame) and not self.session.is_current(generation):
                return
            if isinstance(frame, LLMFullResponseStartFrame):
                self.output_generation = frame.metadata.get("generation")
                self.next_write_at = time.monotonic()
            elif isinstance(frame, TTSTextFrame):
                if not self.session.is_current(frame.metadata.get("generation")):
                    return
                await self.session.send({"type": "transcript", "role": "assistant", "text": frame.text.strip(), "final": True})
        await super().push_frame(frame, direction)

    async def push_error_frame(self, error, force_treat_as_permanent=False):
        # BaseOutputTransport reports write timeouts here. Each response uses
        # fresh media resources, so abort this response before queued text can
        # pass, then permit a new generation after the interruption barrier.
        await self.session.fail_response("output", error.exception or RuntimeError(error.error))

    async def write_audio_frame(self, frame):
        session = self.session
        generation = self.output_generation
        if not session.is_current(generation):
            return False
        try:
            # The SFU adapter already paces its 48kHz packets; direct output
            # uses the same 20ms write cadence at the provider rate.
            if session.transport_kind == "websocket":
                await asyncio.sleep(max(0, self.next_write_at - time.monotonic()))
                self.next_write_at = max(self.next_write_at, time.monotonic()) + len(frame.audio) / 48000
            if not session.is_current(generation):
                return False
            accepted = await session.transport.send_audio(frame.audio, generation)
            if not session.is_current(generation):
                return False
            if not accepted:
                raise RuntimeError("Audio transport rejected current speech")
            session.release_output(len(frame.audio), generation)
            session.note_first_audio(generation)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if session.is_current(generation):
                await session.fail_response("output", exc)
            return False


class PersistedAssistantAggregator(LLMAssistantAggregator):
    """Standard context commits, with synchronous persistence at their boundary."""
    def __init__(self, session, user):
        super().__init__(session.context, _paired_user_aggregator=user, _realtime_service_mode=False)
        self.session = session

    async def push_aggregation(self):
        text = await super().push_aggregation()
        await self.session.persist()
        return text

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, InterruptionFrame):
            self.session.responding = False
            self.session.context_ready.set()
        elif isinstance(frame, LLMFullResponseEndFrame) and direction == FrameDirection.DOWNSTREAM:
            generation = frame.metadata.get("generation")
            if self.session.is_current(generation):
                try:
                    accepted = await asyncio.wait_for(
                        self.session.transport.finish_generation(generation), OUTPUT_FINISH_TIMEOUT)
                    if not self.session.is_current(generation):
                        return
                    if accepted is not True:
                        raise RuntimeError("Audio transport did not complete the response")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if self.session.is_current(generation):
                        await self.session.fail_response("output_completion", exc)
                else:
                    self.session.responding = False
                    await self.session.send({"type": "status", "state": "listening", "generation": generation})
