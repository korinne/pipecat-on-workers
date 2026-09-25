"""Provider packet boundaries must not inflate bounded playback receipts."""
import asyncio
import base64
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from runtime_probe import Harness
from conversation import MAX_PENDING_RECEIPTS, MAX_UNACKED_BYTES


class PacketizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_many_small_packets_preserve_audio_and_sentence_receipts(self):
        # Cover both an exact frame boundary and a final partial frame. Aura's
        # 40 ms packets previously consumed one receipt each, exceeding 256.
        for packet_count in (300, 301):
            with self.subTest(packet_count=packet_count):
                h = await Harness().start()
                payload = bytes(range(256)) * (packet_count * 1920 // 256)
                payload += bytes(range((packet_count * 1920) % 256))
                closed = asyncio.Event()

                async def synthesize(text):
                    try:
                        for offset in range(0, len(payload), 1920):
                            await asyncio.sleep(0)
                            yield payload[offset:offset+1920]
                    finally:
                        closed.set()

                h.provider.synthesize = synthesize
                original_send = h.session.send

                async def send_with_receipts(event):
                    await original_send(event)
                    if event["type"] == "audio" and not event["text"]:
                        await h.session.played(event["generation"], event["chunk_id"])

                h.session.send = send_with_receipts
                try:
                    await h.turn("packetized speech", 0)
                    await h.response()
                    audio = h.audio()
                    chunks = [base64.b64decode(event["data"]) for event in audio]
                    self.assertEqual(b"".join(chunks), payload)
                    self.assertEqual(len(chunks), (len(payload) + 4799) // 4800)
                    self.assertTrue(all(len(chunk) == 4800 for chunk in chunks[:-1]))
                    self.assertEqual(len(chunks[-1]), len(payload) % 4800 or 4800)
                    sentence = "fixture reply to packetized speech."
                    self.assertEqual([event["text"] for event in audio if event["text"]], [sentence])
                    self.assertEqual(audio[-1]["text"], sentence)
                    final_receipt = h.session.pending[audio[-1]["chunk_id"]]
                    self.assertEqual(final_receipt["before"], [event["chunk_id"] for event in audio[:-1]])
                    self.assertEqual(h.assistant(), [])
                    self.assertLessEqual(len(h.session.pending), MAX_PENDING_RECEIPTS)
                    self.assertLessEqual(h.session.unacked_bytes, MAX_UNACKED_BYTES)
                    self.assertTrue(closed.is_set())
                    self.assertFalse(any(event["type"] == "error" for event in h.events))
                    await h.session.played(audio[-1]["generation"], audio[-1]["chunk_id"])
                    self.assertEqual(h.assistant(), [sentence])
                    self.assertEqual(h.session.pending, {})
                    self.assertEqual(h.session.unacked_bytes, 0)
                finally:
                    await h.close()
