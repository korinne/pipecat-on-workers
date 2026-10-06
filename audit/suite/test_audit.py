"""Tests for the evidence classifier: accidental success must not count."""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from audit import classify_ablation, classify_candidate, command, interpreter_details, strict_success, THREAD_MARKER, result


def run(returncode=0, stdout="", stderr="", **extras):
    return dict(returncode=returncode, stdout=stdout, stderr=stderr, timed_out=False, **extras)


class ClassificationTests(unittest.TestCase):
    def test_expected_denial_is_gap(self):
        self.assertEqual(classify_ablation(run(1, stderr="Traceback...\n" + THREAD_MARKER + "\n"), THREAD_MARKER), "observed_gap")

    def test_unexpected_success_is_error(self):
        self.assertEqual(classify_ablation(run(0, stderr=THREAD_MARKER), THREAD_MARKER), "test_error")

    def test_missing_dependency_cannot_prove_thread_denial(self):
        self.assertEqual(classify_ablation(run(1, stderr="ModuleNotFoundError: No module named 'loguru'"), THREAD_MARKER), "test_error")

    def test_source_excerpt_cannot_prove_denial(self):
        self.assertEqual(classify_ablation(run(1, stderr='  expected = "' + THREAD_MARKER + '"\nAssertionError'), THREAD_MARKER), "test_error")

    def test_timeout_cannot_prove_denial(self):
        evidence = run(1, stderr=THREAD_MARKER)
        evidence["timed_out"] = True
        self.assertEqual(classify_ablation(evidence, THREAD_MARKER), "test_error")

    def test_exit_zero_alone_is_not_core_success(self):
        self.assertFalse(strict_success(run(0)))
        self.assertFalse(strict_success(run(0, stdout='{"passed":true}')))

    def test_incomplete_or_dirty_success_report_rejected(self):
        valid = dict(passed=True, generations=2, threads_created=0,
                     forbidden_imports_loaded=[], live_pipecat_tasks_after_cancel=[])
        self.assertTrue(strict_success(run(stdout=json.dumps(valid))))
        for key, value in (("passed", "true"), ("generations", 0),
                           ("threads_created", 1), ("forbidden_imports_loaded", ["numpy"]),
                           ("live_pipecat_tasks_after_cancel", ["worker"])):
            changed = dict(valid, **{key: value})
            self.assertFalse(strict_success(run(stdout=json.dumps(changed))), key)
        self.assertFalse(strict_success(run(1, stdout=json.dumps(valid))))

    def test_unknown_result_category_rejected(self):
        with self.assertRaises(ValueError):
            result("id", "test", "production_ready", "unsupported claim")

    def test_candidate_unrelated_crash_is_not_capability_evidence(self):
        self.assertEqual(classify_candidate(run(1, stderr="ModuleNotFoundError: No module named 'loguru'")), "test_error")
        self.assertEqual(classify_candidate(run(0, stdout='{"passed": true}')), "test_error")

    def test_candidate_known_contract_failure_is_gap(self):
        self.assertEqual(classify_candidate(run(1, stderr=THREAD_MARKER)), "observed_gap")

    def test_candidate_api_profile_mismatch_is_not_capability_gap(self):
        self.assertEqual(classify_candidate(run(1, stderr="TypeError: PipelineWorker.__init__() got an unexpected keyword argument 'enable_import_prewarm'")), "test_error")

    def test_optimization_environment_cannot_strip_assertions(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"PYTHONOPTIMIZE": "2"}):
            evidence = command([sys.executable, "-c", "import sys,json;print(json.dumps({'optimize':sys.flags.optimize}))"], folder)
        self.assertEqual(interpreter_details(evidence)["optimize"], 0)

    def test_optimized_or_unknown_interpreter_rejected(self):
        for details in ({"optimize": 1}, {"optimize": 2}, {}):
            with self.assertRaises(ValueError):
                interpreter_details(run(stdout=json.dumps(details)))


if __name__ == "__main__":
    unittest.main()
