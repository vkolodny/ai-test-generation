"""Boundary checks for the FLPATH-5050 candidate evaluator."""

import argparse
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

sys.path.insert(0, str(pathlib.Path(__file__).parents[2] / "experiments" / "harness"))

import candidate_evaluation as evaluation  # noqa: E402


class CandidateEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary_directory.name)
        self.case = self.root / "baseline"
        self.case.mkdir()
        self.cache = self.root / "cache"
        self.cache.mkdir()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def write_report(self, name, tests, failures=0, errors=0, skipped=0):
        directory = self.case / "target" / "surefire-reports"
        directory.mkdir(parents=True, exist_ok=True)
        suite = ElementTree.Element("testsuite", name=name, tests=str(tests),
                                    failures=str(failures), errors=str(errors),
                                    skipped=str(skipped))
        if failures:
            case = ElementTree.SubElement(suite, "testcase", name="candidate")
            ElementTree.SubElement(case, "failure", type="java.lang.AssertionError")
        ElementTree.ElementTree(suite).write(directory / ("TEST-" + name + ".xml"))

    def fake_run(self, exit_code):
        class Result:
            returncode = exit_code
            stdout = "controlled Maven output"
        return Result()

    def test_rejects_test_path_outside_java_tests(self):
        for path in ("../pom.xml", "src/main/java/UnsafeTest.java",
                     "src/test/java/../../pom.xml", "/tmp/UnsafeTest.java"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    evaluation.test_target(path)

    def test_mutation_requires_exact_anchor(self):
        source = self.case / evaluation.MUTATION_PATH
        source.parent.mkdir(parents=True)
        source.write_text("class DifferentSource {}")
        with self.assertRaisesRegex(ValueError, "exactly once"):
            evaluation.mutate_fixture(self.case)

    def test_compile_failure_does_not_count_as_behavioral_detection(self):
        with patch.object(evaluation.subprocess, "run", side_effect=[self.fake_run(0), self.fake_run(1)]):
            result = evaluation.run_case(self.case, "baseline", "CandidateTest",
                                         self.cache, "pinned-image")
        self.assertEqual(result["status"], "blocked_no_tests")
        self.assertEqual(result["tests"]["tests"], 0)

    def test_package_failure_blocks_without_running_candidate(self) -> None:
        with patch.object(evaluation.subprocess, "run", return_value=self.fake_run(1)) as runner:
            result = evaluation.run_case(self.case, "baseline", "CandidateTest",
                                         self.cache, "pinned-image")
        self.assertEqual(result["status"], "blocked_package_failure")
        self.assertIsNone(result["test"])
        self.assertEqual(runner.call_count, 1)

    def test_package_first_command_is_the_same_for_every_candidate(self) -> None:
        self.write_report("com.projectlightpath.fixture.CandidateTest", 1)
        with patch.object(evaluation.subprocess, "run", side_effect=[self.fake_run(0), self.fake_run(0)]):
            result = evaluation.run_case(self.case, "baseline", "CandidateTest",
                                         self.cache, "pinned-image")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["package"]["command"][-2:], ["-DskipTests", "package"])
        self.assertEqual(result["test"]["command"][-3:],
                         ["-Dtest=CandidateTest", "-Dsurefire.failIfNoSpecifiedTests=true", "test"])
        for phase in ("package", "test"):
            self.assertIn("--network=none", result[phase]["command"])

    def test_skipped_candidate_is_not_a_pass(self):
        self.write_report("com.projectlightpath.fixture.CandidateTest", 1, skipped=1)
        with patch.object(evaluation.subprocess, "run", side_effect=[self.fake_run(0), self.fake_run(0)]):
            result = evaluation.run_case(self.case, "baseline", "CandidateTest",
                                         self.cache, "pinned-image")
        self.assertEqual(result["status"], "blocked_no_executed_tests")

    def test_matching_test_assertion_failure_counts(self):
        self.write_report("com.projectlightpath.fixture.CandidateTest", 1, failures=1)
        with patch.object(evaluation.subprocess, "run", side_effect=[self.fake_run(0), self.fake_run(1)]):
            result = evaluation.run_case(self.case, "mutant", "CandidateTest",
                                         self.cache, "pinned-image")
        self.assertEqual(result["status"], "failed_assertion")
        self.assertEqual(result["tests"]["failures"], 1)

    def test_existing_test_failure_cannot_substitute_for_candidate(self):
        self.write_report("com.projectlightpath.fixture.TransferResourceTest", 1, failures=1)
        with patch.object(evaluation.subprocess, "run", side_effect=[self.fake_run(0), self.fake_run(1)]):
            result = evaluation.run_case(self.case, "mutant", "CandidateTest",
                                         self.cache, "pinned-image")
        self.assertEqual(result["status"], "blocked_no_tests")

    def test_real_model_invocation_requires_prompt_and_config_hashes(self):
        invocation = self.root / "invocation.json"
        invocation.write_text('{"candidate":"aider","tool_version":"1","model_id":"model-x","endpoint_id":"approved","redacted_command":["aider"]}')
        args = argparse.Namespace(candidate="aider", tool_version="1",
                                  model_id="model-x", endpoint_id="approved")
        with self.assertRaisesRegex(ValueError, "prompt and config digests"):
            evaluation.load_invocation(invocation, args)

    def test_candidate_must_use_shared_objective(self):
        invocation = self.root / "invocation.json"
        wrong = "sha256:" + "0" * 64
        invocation.write_text('{"candidate":"aider","tool_version":"1","model_id":"model-x","endpoint_id":"approved","redacted_command":["aider"],"prompt_sha256":"' + wrong + '","config_sha256":"' + wrong + '"}')
        args = argparse.Namespace(candidate="aider", tool_version="1",
                                  model_id="model-x", endpoint_id="approved")
        with self.assertRaisesRegex(ValueError, "does not match shared objective"):
            evaluation.load_invocation(invocation, args)

    def test_invocation_rejects_unredacted_key(self):
        invocation = self.root / "invocation.json"
        invocation.write_text('{"candidate":"fake","tool_version":"1","model_id":"none","endpoint_id":"none","redacted_command":["aider","--api-key=live-value"]}')
        args = argparse.Namespace(candidate="fake", tool_version="1",
                                  model_id="none", endpoint_id="none")
        with self.assertRaisesRegex(ValueError, "may contain a secret"):
            evaluation.load_invocation(invocation, args)

    def test_invocation_accepts_redacted_key(self):
        invocation = self.root / "invocation.json"
        invocation.write_text('{"candidate":"fake","tool_version":"1","model_id":"none","endpoint_id":"none","redacted_command":["aider","--api-key=[REDACTED]"]}')
        args = argparse.Namespace(candidate="fake", tool_version="1",
                                  model_id="none", endpoint_id="none")
        self.assertEqual(evaluation.load_invocation(invocation, args)["redacted_command"][-1],
                         "--api-key=[REDACTED]")

    def test_invocation_identity_must_match_candidate(self):
        invocation = self.root / "invocation.json"
        invocation.write_text('{"candidate":"claude-code","tool_version":"1","model_id":"none","endpoint_id":"none","redacted_command":[]}')
        args = argparse.Namespace(candidate="aider", tool_version="1",
                                  model_id="none", endpoint_id="none")
        with self.assertRaisesRegex(ValueError, "identities must match"):
            evaluation.load_invocation(invocation, args)

    def test_inline_key_cannot_hide_behind_following_placeholder(self):
        invocation = self.root / "invocation.json"
        invocation.write_text('{"candidate":"fake","tool_version":"1","model_id":"none","endpoint_id":"none","redacted_command":["aider","--api-key=live-value","[REDACTED]"]}')
        args = argparse.Namespace(candidate="fake", tool_version="1",
                                  model_id="none", endpoint_id="none")
        with self.assertRaisesRegex(ValueError, "may contain a secret"):
            evaluation.load_invocation(invocation, args)

    def test_xml_failure_with_success_exit_is_inconclusive(self):
        self.write_report("com.projectlightpath.fixture.CandidateTest", 1, failures=1)
        with patch.object(evaluation.subprocess, "run", side_effect=[self.fake_run(0), self.fake_run(0)]):
            result = evaluation.run_case(self.case, "mutant", "CandidateTest",
                                         self.cache, "pinned-image")
        self.assertEqual(result["status"], "blocked_test_failure")

    def test_timeout_removes_started_container(self):
        cid_file = self.root / "baseline-package.cid"
        cid_file.write_text("test-container-id")
        timeout = evaluation.subprocess.TimeoutExpired(cmd="podman", timeout=900)
        with patch.object(evaluation.subprocess, "run", side_effect=[timeout, self.fake_run(0)]) as runner:
            result = evaluation.run_case(self.case, "baseline", "CandidateTest",
                                         self.cache, "pinned-image")
        self.assertEqual(result["status"], "blocked_package_timeout")
        self.assertEqual(runner.call_args_list[-1].args[0],
                         ["podman", "rm", "-f", "test-container-id"])

    def test_test_phase_timeout_keeps_package_evidence(self) -> None:
        cid_file = self.root / "baseline-test.cid"
        cid_file.write_text("test-container-id")
        timeout = evaluation.subprocess.TimeoutExpired(cmd="podman", timeout=900)
        with patch.object(evaluation.subprocess, "run", side_effect=[
                self.fake_run(0), timeout, self.fake_run(0)]) as runner:
            result = evaluation.run_case(self.case, "baseline", "CandidateTest",
                                         self.cache, "pinned-image")
        self.assertEqual(result["status"], "blocked_timeout")
        self.assertEqual(result["package"]["status"], "passed")
        self.assertEqual(result["test"]["status"], "timeout")
        self.assertEqual(runner.call_args_list[-1].args[0],
                         ["podman", "rm", "-f", "test-container-id"])

    def test_modified_fixture_is_rejected_before_execution(self):
        fixture = self.root / "fixture"
        fixture.mkdir()
        (fixture / "pom.xml").write_text("different fixture")
        candidate = self.root / "CandidateTest.java"
        candidate.write_text("class CandidateTest {}")
        invocation = self.root / "invocation.json"
        invocation.write_text('{"candidate":"fake","tool_version":"1","model_id":"none","endpoint_id":"none","redacted_command":[]}')
        args = argparse.Namespace(
            candidate_test_file=candidate,
            candidate_test_path="src/test/java/com/example/CandidateTest.java",
            candidate="fake", tool_version="1", model_id="none",
            endpoint_id="none", fixture_root=fixture, maven_cache=self.cache,
            output_root=self.root / "result", image="pinned-image", invocation_record=invocation,
        )
        with self.assertRaisesRegex(ValueError, "differs from pinned"):
            evaluation.evaluate(args)
        self.assertFalse(args.output_root.exists())


if __name__ == "__main__":
    unittest.main()
