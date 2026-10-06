"""Standard output packetization, sentence context and receipt flow control."""
import asyncio
import base64
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from runtime_probe import Harness
from conversation import MAX_PENDING_RECEIPTS, MAX_UNACKED_BYTES


class PacketizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_many_small_packets_preserve_audio_and_standard_context(self):
        # Over 256 provider packets must not impose a sentence receipt limit.
        # Cover an exact 20ms output boundary and a trailing partial frame.
        for packet_count in (300, 301):
            with self.subTest(packet_count=packet_count):
                h = await Harness().start()
                payload = bytes(range(96)) * packet_count
                closed = asyncio.Event()

                async def synthesize(text):
                    try:
                        for offset in range(0, len(payload), 96):
                            await asyncio.sleep(0)
                            yield payload[offset:offset+96]
                    finally:
                        closed.set()

                h.provider.synthesize = synthesize
                original_send = h.session.send
                peak_pending = 0

                async def send_with_receipts(event):
                    nonlocal peak_pending
                    await original_send(event)
                    if event["type"] == "audio":
                        peak_pending = max(peak_pending, len(h.session.pending))
                        await h.session.played(event["generation"], event["chunk_id"])

                h.session.send = send_with_receipts
                try:
                    await h.turn("packetized speech", 0)
                    await h.response()
                    audio = h.audio()
                    chunks = [base64.b64decode(event["data"]) for event in audio]
                    received = b"".join(chunks)
                    self.assertEqual(received[:len(payload)], payload)
                    self.assertEqual(received[len(payload):], bytes(len(received)-len(payload)))
                    self.assertEqual(len(chunks), (len(payload) + 959) // 960)
                    self.assertTrue(all(len(chunk) == 960 for chunk in chunks))
                    self.assertTrue(all(event["text"] == "" for event in audio))
                    self.assertEqual(h.assistant(), ["fixture reply to packetized speech."])
                    self.assertLessEqual(peak_pending, MAX_PENDING_RECEIPTS)
                    self.assertLessEqual(h.session.unacked_bytes, MAX_UNACKED_BYTES)
                    self.assertTrue(closed.is_set())
                    self.assertFalse(any(event["type"] == "error" for event in h.events))
                    self.assertEqual(h.session.pending, {})
                    self.assertEqual(h.session.unacked_bytes, 0)
                finally:
                    await h.close()
