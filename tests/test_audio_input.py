"""Check safe input delivery counters and startup guidance in the real pipeline."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from conversation import SYSTEM
from runtime_probe import Harness


class AudioInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_received_and_forwarded_audio_are_distinct(self):
        h = await Harness().start()
        results = iter((True, False, None, RuntimeError("send failed"), True))

        async def send_audio(pcm):
            result = next(results)
            if isinstance(result, Exception):
                raise result
            return result

        h.provider.send_audio = send_audio
        try:
            for size in (640, 320, 160):
                await h.session.audio(bytes(size))
            with self.assertRaisesRegex(RuntimeError, "send failed"):
                await h.session.audio(bytes(80))
            expected = {"input_audio_chunks": 4, "input_audio_bytes": 1200,
                        "forwarded_audio_bytes": 640}
            self.assertEqual({key: h.session.diagnostics()[key] for key in expected}, expected)

            # Provider-originated silence does not inflate client input counters.
            self.assertTrue(await h.provider.send_audio(bytes(2560)))
            for size in (1, 16002):
                with self.assertRaises(ValueError):
                    await h.session.audio(bytes(size))
            self.assertEqual({key: h.session.diagnostics()[key] for key in expected}, expected)
        finally:
            await h.close()
        await h.session.audio(bytes(640))
        self.assertEqual({key: h.session.diagnostics()[key] for key in expected}, expected)

    async def test_startup_guidance_reflects_restored_history(self):
        history = [{"role": "user", "content": "Earlier conversation"}]
        for restored in (False, True):
            with self.subTest(restored=restored):
                state = {"context_schema": 2, "messages": [{"role": "system", "content": SYSTEM}] + history} if restored else None
                h = await Harness(state=state).start()
                try:
                    reset = next(event for event in h.events if event["type"] == "reset")
                    self.assertEqual(reset["history"], history if restored else [])
                    self.assertEqual(reset["message"],
                                     "Reconnected. Your conversation history is restored." if restored else
                                     "Connected. Say hello to start.")
                    for key in ("input_audio_chunks", "input_audio_bytes", "forwarded_audio_bytes"):
                        self.assertEqual(h.session.diagnostics()[key], 0)
                finally:
                    await h.close()
