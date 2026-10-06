"""Test result handling separately from the application being measured."""
import pathlib
import tempfile
import types
import unittest
from unittest.mock import patch

import run


class ResultTests(unittest.TestCase):
    def test_missing_prerequisite_is_error_not_application_gap(self):
        with self.assertRaises(run.ProbeError):
            run.classify(False, False)

    def test_met_expectation_is_only_scoped_pass(self):
        self.assertEqual(run.classify(True, True), "scoped_pass")

    def test_missing_expected_behavior_is_gap(self):
        self.assertEqual(run.classify(True, False), "observed_gap")

    def test_matching_truncation_in_transcript_and_history_cannot_pass(self):
        expected = "fixture reply to Give me two suggestions."
        truncated = "fixture reply to Give me"
        messages = [{"role": "assistant", "content": truncated}]
        with self.assertRaisesRegex(run.ProbeError, "complete generated answer"):
            run.classify_history(expected, [truncated], messages, messages)

    def test_complete_transcript_with_truncated_history_is_gap(self):
        expected = "fixture reply to Give me two suggestions."
        messages = [{"role": "assistant", "content": "fixture reply to Give me"}]
        self.assertEqual(run.classify_history(expected, [expected], messages, messages), "observed_gap")

    def test_complete_transcript_and_history_pass_local_context_check(self):
        expected = "fixture reply to Give me two suggestions."
        messages = [{"role": "assistant", "content": expected}]
        self.assertEqual(run.classify_history(expected, [expected], messages, messages), "scoped_pass")

    def test_local_passes_cannot_establish_readiness(self):
        self.assertEqual(run.exit_code([{"status": "scoped_pass"}]), 1)

    def test_probe_errors_take_precedence(self):
        self.assertEqual(run.exit_code([{"status": "observed_gap"}, {"status": "test_error"}]), 2)

    def test_unknown_status_rejected(self):
        with self.assertRaises(run.ProbeError):
            run.exit_code([{"status": "pass"}])

    def test_foreign_fixture_rejected(self):
        fake = types.SimpleNamespace(__name__="foreign_fixture", __file__="/tmp/foreign_fixture.py")
        with self.assertRaises(run.ProbeError):
            run.check_origin(fake, pathlib.Path("/repo/src"))

    def test_child_optimization_removed_and_missing_evidence_is_error(self):
        observed = {}

        def completed(command, **kwargs):
            observed.update(kwargs)
            observed["command"] = command
            return types.SimpleNamespace(returncode=0, stderr="")

        with patch.dict(run.os.environ, {"PYTHONOPTIMIZE": "2"}), patch.object(run.subprocess, "run", completed):
            result = run.subprocess_case(pathlib.Path("/repo"), "ws-context")
        self.assertNotIn("PYTHONOPTIMIZE", observed["env"])
        self.assertNotIn("-O", observed["command"])
        self.assertEqual(result["status"], "test_error")

    def test_child_timeout_is_error_not_gap(self):
        with patch.object(run.subprocess, "run", side_effect=run.subprocess.TimeoutExpired("probe", 35)):
            result = run.subprocess_case(pathlib.Path("/repo"), "sfu-cleanup")
        self.assertEqual(result["status"], "test_error")


if __name__ == "__main__":
    unittest.main()
