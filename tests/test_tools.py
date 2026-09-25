"""Verify the exact plural transcript routes through the real async tool."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from conversation import lookup_availability
from runtime_probe import Harness


class AppointmentToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_plural_appointments_passes_tool_result_to_model(self):
        results = []

        async def actual_tool():
            result = await lookup_availability()
            results.append(result)
            return result

        h = await Harness(tool=actual_tool).start()
        try:
            await h.turn("What fictional appointments are available?", 0)
            await h.response()
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0]["fictional"])
            self.assertFalse(results[0]["booked"])
            self.assertEqual(results[0]["appointments"],
                             ["Tuesday at 10 AM", "Thursday at 2 PM"])
            self.assertIn({"role": "system", "content":
                           f"lookup_availability returned: {results[0]}"},
                          h.provider.generations[0])
            self.assertTrue(any(event["type"] == "status" and
                                event.get("state") == "tool" for event in h.events))
            self.assertEqual(sum(metric["event"] == "tool_completed"
                                 for metric in h.session.metrics), 1)
        finally:
            await h.close()
