"""Integration tests through real Pipecat queues; only provider I/O is synthetic."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from runtime_probe import PROBE_CASES


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    """Each probe case owns and cleans up its real Pipecat pipeline."""


def make_case(name):
    async def run(self):
        evidence = await PROBE_CASES[name]()
        self.assertIsInstance(evidence, dict)
    run.__name__ = f"test_{name}"
    return run


for name in PROBE_CASES:
    setattr(ConversationTests, f"test_{name}", make_case(name))


if __name__ == "__main__":
    unittest.main(verbosity=2)
