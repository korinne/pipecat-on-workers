import asyncio
import base64
import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from audio_transport import WebSocketAudioTransport


class WebSocketAudioTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []
        self.generation = 2

        async def send(event):
            self.events.append(event)

        self.transport = WebSocketAudioTransport(send, is_current=lambda g: g == self.generation)

    async def asyncTearDown(self):
        await self.transport.close()

    async def test_pcm_format_and_receipts_only_release_credit(self):
        pcm = bytes(range(256)) * 3 + bytes(192)
        self.assertTrue(await self.transport.send_audio(pcm, 2))
        event = self.events[-1]
        self.assertEqual(base64.b64decode(event["data"]), pcm)
        self.assertEqual(event["sample_rate"], 24000)
        self.assertEqual(event["text"], "")
        self.assertEqual(self.transport.pending, {1: {"bytes": 960, "generation": 2}})
        await self.transport.played(1, 1)
        await self.transport.played(2, 999)
        self.assertEqual(self.transport.unacked_bytes, 960)
        await self.transport.played(2, 1)
        await self.transport.played(2, 1)
        self.assertEqual(self.transport.diagnostics(), {"unacked_audio_bytes": 0, "pending_playback_chunks": 0})

    async def test_slow_receiver_blocks_at_chunk_bound_then_ack_unblocks(self):
        with patch("audio_transport.MAX_PENDING_RECEIPTS", 2):
            await self.transport.send_audio(bytes(960), 2)
            await self.transport.send_audio(bytes(960), 2)
            waiting = asyncio.create_task(self.transport.send_audio(bytes(960), 2))
            await asyncio.sleep(0)
            self.assertFalse(waiting.done())
            self.assertEqual(len(self.transport.pending), 2)
            await self.transport.played(2, 1)
            self.assertTrue(await waiting)
            self.assertEqual(len(self.transport.pending), 2)

    async def test_byte_bound_and_interruption_release_waiter_without_stale_send(self):
        with patch("audio_transport.MAX_UNACKED_BYTES", 960):
            await self.transport.send_audio(bytes(960), 2)
            waiting = asyncio.create_task(self.transport.send_audio(bytes(960), 2))
            await asyncio.sleep(0)
            self.generation = 3
            await self.transport.clear(3)
            self.assertFalse(await waiting)
            self.assertEqual(len(self.events), 1)
            self.assertEqual(self.transport.pending, {})
            self.assertTrue(await self.transport.send_audio(bytes(960), 3))
            await self.transport.played(2, 1)
            self.assertEqual(self.transport.unacked_bytes, 960)

    async def test_timeout_is_failure_and_end_wakes_waiter(self):
        with patch("audio_transport.MAX_PENDING_RECEIPTS", 1), patch("audio_transport.CREDIT_TIMEOUT", .001):
            await self.transport.send_audio(bytes(960), 2)
            with self.assertRaises(asyncio.TimeoutError):
                await self.transport.send_audio(bytes(960), 2)
        with patch("audio_transport.MAX_PENDING_RECEIPTS", 1):
            waiting = asyncio.create_task(self.transport.send_audio(bytes(960), 2))
            await asyncio.sleep(0)
            await self.transport.close()
            self.assertFalse(await waiting)
        self.assertEqual(self.transport.unacked_bytes, 0)

    async def test_failed_send_releases_its_credit(self):
        async def fail(event):
            raise RuntimeError("connection lost")

        self.transport.send = fail
        with self.assertRaisesRegex(RuntimeError, "connection lost"):
            await self.transport.send_audio(bytes(960), 2)
        self.assertEqual(self.transport.pending, {})
        self.assertEqual(self.transport.unacked_bytes, 0)

    async def test_fresh_connection_rejects_old_generation_and_accepts_current(self):
        await self.transport.send_audio(bytes(960), 2)
        await self.transport.close()
        self.generation = 4
        fresh = WebSocketAudioTransport(self.transport.send, is_current=lambda g: g == 4, initial_generation=4)
        self.assertFalse(await fresh.send_audio(bytes(960), 2))
        self.assertTrue(await fresh.send_audio(bytes(960), 4))
        await fresh.played(2, 1)
        self.assertEqual(fresh.unacked_bytes, 960)
        await fresh.close()
