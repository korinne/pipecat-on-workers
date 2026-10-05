"""Direct browser audio delivery and bounded playback flow control.

The speech pipeline owns assistant context. Browser acknowledgements release
queue credit only; they do not decide which text the assistant remembers.
"""

import asyncio
import base64

MAX_UNACKED_BYTES = 384000  # Eight seconds of 24 kHz mono PCM16.
MAX_PENDING_RECEIPTS = 256
CREDIT_TIMEOUT = 12


class WebSocketAudioTransport:
    def __init__(self, send, *, is_current=None, initial_generation=0):
        self.send = send
        self.is_current = is_current or (lambda generation: True)
        self.generation_floor = initial_generation
        self.closed = False
        self.pending = {}
        self.unacked_bytes = 0
        self.next_chunk = 0
        self.credit = asyncio.Event()
        self.credit.set()
        self.output_lock = asyncio.Lock()

    def _alive(self, generation):
        return (not self.closed and type(generation) is int
                and generation >= self.generation_floor and self.is_current(generation))

    async def send_audio(self, pcm, generation):
        pcm = bytes(pcm)
        if not pcm or len(pcm) % 2 or len(pcm) > 48000:
            raise ValueError("Expected at most one second of PCM16 at 24 kHz")
        async with self.output_lock:
            while self._alive(generation):
                if (self.unacked_bytes + len(pcm) <= MAX_UNACKED_BYTES
                        and len(self.pending) < MAX_PENDING_RECEIPTS):
                    break
                self.credit.clear()
                await asyncio.wait_for(self.credit.wait(), CREDIT_TIMEOUT)
            if not self._alive(generation):
                return False
            self.next_chunk += 1
            chunk = self.next_chunk
            self.pending[chunk] = {"bytes": len(pcm), "generation": generation}
            self.unacked_bytes += len(pcm)
            try:
                await self.send({"type": "audio", "data": base64.b64encode(pcm).decode(),
                                 "sample_rate": 24000, "generation": generation,
                                 "chunk_id": chunk, "text": ""})
            except BaseException:
                # The sender may fail after an acknowledgement already arrived.
                item = self.pending.pop(chunk, None)
                if item:
                    self.unacked_bytes -= item["bytes"]
                self.credit.set()
                raise
            return self._alive(generation)

    async def played(self, generation, chunk):
        if type(chunk) is not int or not self._alive(generation):
            return
        item = self.pending.get(chunk)
        if not item or item["generation"] != generation:
            return
        self.pending.pop(chunk)
        self.unacked_bytes -= item["bytes"]
        self.credit.set()

    async def finish_generation(self, generation):
        return self._alive(generation)

    async def clear(self, generation):
        if type(generation) is not int:
            raise ValueError("Invalid assistant audio generation")
        self.generation_floor = max(self.generation_floor, generation)
        for chunk, item in list(self.pending.items()):
            if item["generation"] < self.generation_floor:
                self.pending.pop(chunk)
                self.unacked_bytes -= item["bytes"]
        self.credit.set()

    async def close(self):
        self.closed = True
        self.pending.clear()
        self.unacked_bytes = 0
        self.credit.set()

    def diagnostics(self):
        return {"unacked_audio_bytes": self.unacked_bytes,
                "pending_playback_chunks": len(self.pending)}
