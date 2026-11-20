"""Saved-evidence report checks; no model, container, or network calls."""

import hashlib
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[2] / "experiments" / "harness"))
import build_findings as findings  # noqa: E402


class FindingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def run_root(self, name, runner="sha256:runner", outcome="discriminates"):
        root = self.root / name
        self.write_json(root / "summary.json", {
            "candidate_results": {"candidate-a": outcome},
            "run_id": name,
            "candidate_order": ["candidate-a"],
            "fixture_profile": "spring-transfer",
            "fixture_tree_sha256": "sha256:fixture",
            "objective_sha256": "sha256:objective",
            "model_id": "model", "endpoint_id": "endpoint",
            "runner_sha256": runner, "evaluator_sha256": "sha256:evaluator",
            "evidence_scan": "passed",
        })
        folder = root / "candidate-a"
        self.write_json(folder / "generation.json", {
            "status": "completed", "elapsed_seconds": 7,
        })
        self.write_json(folder / "invocation.json", {
            "config_sha256": "sha256:config",
            "prompt_sha256": "sha256:prompt",
            "tool_source_revision": "sha256:tool", "tool_version": "1",
        })
        self.write_json(folder / "evaluation/evaluation.json", {
            "test": {"path": "src/test/java/sample/CandidateTest.java"},
            "result": outcome,
            "cases": {"baseline": {"status": "passed"},
                      "mutant": {"status": "failed_assertion"}},
        })
        test = folder / "workspace/src/test/java/sample/CandidateTest.java"
        test.parent.mkdir(parents=True)
        test.write_text("class CandidateTest {\n\n  void test() {}\n}\n")
        evaluation_path = folder / "evaluation/evaluation.json"
        evaluation = json.loads(evaluation_path.read_text())
        evaluation["test"]["sha256"] = "sha256:" + hashlib.sha256(test.read_bytes()).hexdigest()
        self.write_json(evaluation_path, evaluation)
        return root

    def test_reports_generated_size_and_unknown_coverage(self):
        run = findings.load_run(self.run_root("one"))
        attempt = run["candidates"][0]
        self.assertEqual(4, attempt["generated_test"]["physical_lines"])
        self.assertEqual(3, attempt["generated_test"]["nonblank_lines"])
        self.assertEqual("unknown", attempt["coverage"]["status"])
        self.assertEqual("failed_assertion", attempt["case_statuses"]["mutant"])
        self.assertEqual(7000, attempt["stages_ms"]["code"])
        self.assertTrue(any(ref["path"].endswith("evaluation.json")
                            for ref in attempt["evidence"]))

    def test_tampered_generated_file_invalidates_success(self):
        root = self.run_root("one")
        test = root / "candidate-a/workspace/src/test/java/sample/CandidateTest.java"
        test.write_text("class ChangedTest {}\n")
        attempt = findings.load_run(root)["candidates"][0]
        self.assertEqual("invalid_evidence", attempt["outcome"])
        self.assertIn("generated_test_hash_mismatch", attempt["integrity_issues"])

    def test_summary_success_requires_differential_evaluator(self):
        root = self.run_root("one")
        path = root / "candidate-a/evaluation/evaluation.json"
        evaluation = json.loads(path.read_text())
        evaluation["cases"]["mutant"]["status"] = "passed"
        self.write_json(path, evaluation)
        attempt = findings.load_run(root)["candidates"][0]
        self.assertEqual("invalid_evidence", attempt["outcome"])
        self.assertIn("differential_cases_do_not_support_success", attempt["integrity_issues"])

    def test_saved_run_spec_separates_external_image_revisions(self):
        one = self.run_root("one")
        two = self.run_root("two")
        self.write_json(one / "run-spec.json", {"image_id": "sha256:first"})
        self.write_json(two / "run-spec.json", {"image_id": "sha256:second"})
        report = findings.findings([findings.load_run(one), findings.load_run(two)])
        self.assertEqual(2, report["cohort_count"])
        self.assertEqual(2, report["decisions"]["candidate-a"]["comparable_identity_count"])

    def test_changed_runner_is_not_aggregated_as_repeat(self):
        a = findings.load_run(self.run_root("one"))
        b = findings.load_run(self.run_root("two", runner="sha256:changed"))
        report = findings.findings([a, b])
        self.assertEqual(2, report["cohort_count"])
        decision = report["decisions"]["candidate-a"]
        self.assertEqual(2, decision["comparable_identity_count"])
        self.assertIsNone(decision["complete_wall_ms_median"])
        self.assertEqual("insufficient_evidence", decision["status"])

    def test_same_identity_can_aggregate_complete_timings(self):
        a = findings.load_run(self.run_root("one"))
        b = findings.load_run(self.run_root("two"))
        for run in (a, b):
            stage = run["candidates"][0]["stages_ms"]
            stage.update({"analysis": 1000, "plan": 1000, "execute": 1000})
        report = findings.findings([a, b])
        self.assertEqual(10000, report["decisions"]["candidate-a"]["complete_wall_ms_median"])

    def test_changed_candidate_order_is_not_pooled(self):
        a = findings.load_run(self.run_root("one"))
        b = findings.load_run(self.run_root("two"))
        b["candidate_order"] = ["other", "candidate-a"]
        for run in (a, b):
            run["candidates"][0]["stages_ms"].update(
                {"analysis": 1000, "plan": 1000, "execute": 1000})
        decision = findings.findings([a, b])["decisions"]["candidate-a"]
        self.assertFalse(decision["same_candidate_order"])
        self.assertIsNone(decision["complete_wall_ms_median"])

    def test_duplicate_run_id_is_not_independent_repeat(self):
        a = findings.load_run(self.run_root("one"))
        b = findings.load_run(self.run_root("two"))
        b["run_id"] = a["run_id"]
        for run in (a, b):
            run["candidates"][0]["stages_ms"].update(
                {"analysis": 1000, "plan": 1000, "execute": 1000})
        decision = findings.findings([a, b])["decisions"]["candidate-a"]
        self.assertFalse(decision["independent_run_ids"])
        self.assertIsNone(decision["complete_wall_ms_median"])

    def test_replay_packet_needs_source_manifest(self):
        root = self.run_root("one")
        summary = json.loads((root / "summary.json").read_text())
        summary["run_kind"] = "evaluation_replay"
        summary["source_run_summary_sha256"] = "sha256:source"
        self.write_json(root / "summary.json", summary)
        with self.assertRaisesRegex(ValueError, "source linkage"):
            findings.load_run(root)

    def test_rejects_candidate_path_traversal(self):
        root = self.run_root("one")
        record = json.loads((root / "summary.json").read_text())
        record["candidate_results"] = {"../outside": "discriminates"}
        self.write_json(root / "summary.json", record)
        with self.assertRaisesRegex(ValueError, "invalid candidate results"):
            findings.load_run(root)

    def test_review_needs_authorization_reference(self):
        self.run_root("one")
        path = self.root / "review.json"
        self.write_json(path, {"schema": "flpath-5050.decision-review.v1",
                               "reviewer": "QE", "reviewer_role": "quality_engineer",
                               "candidates": {"candidate-a": {
                                   "status": "reject", "reason": "Not suitable",
                                   "decision_id": "decision-one",
                                   "by_stage": {stage: "reject" for stage in findings.STAGES}}}})
        with self.assertRaisesRegex(ValueError, "authorization_ref"):
            findings.reviewed_decisions(path, ["candidate-a"])

    def test_positive_review_requires_discriminating_attempt(self):
        root = self.run_root("one", outcome="blocked")
        path = self.root / "review.json"
        self.write_json(path, {"schema": "flpath-5050.decision-review.v1",
                               "reviewer": "QE", "reviewer_role": "quality_engineer",
                               "candidates": {
                                   "candidate-a": {"status": "use", "reason": "Claimed ready",
                                                   "decision_id": "decision-one",
                                                   "authorization_ref": "FLPATH-5050",
                                                   "by_stage": {stage: "use"
                                                                for stage in findings.STAGES}}}})
        review = findings.reviewed_decisions(path, ["candidate-a"])
        with self.assertRaisesRegex(ValueError, "discriminating"):
            findings.findings([findings.load_run(root)], review)

    def test_reviewed_use_cannot_override_unresolved_stage(self):
        self.run_root("one")
        path = self.root / "review.json"
        self.write_json(path, {"schema": "flpath-5050.decision-review.v1",
                               "reviewer": "QE", "reviewer_role": "quality_engineer",
                               "candidates": {
                                   "candidate-a": {"status": "use", "reason": "Checked",
                                                   "decision_id": "decision-one",
                                                   "authorization_ref": "FLPATH-5050",
                                                   "by_stage": {stage: "insufficient_evidence"
                                                                for stage in findings.STAGES}}}})
        with self.assertRaisesRegex(ValueError, "conflicts"):
            findings.reviewed_decisions(path, ["candidate-a"])

    def test_reviewed_fork_decision_requires_change_list(self):
        self.run_root("one")
        path = self.root / "review.json"
        stages = {stage: "use_with_fork" for stage in findings.STAGES}
        self.write_json(path, {"schema": "flpath-5050.decision-review.v1",
                               "reviewer": "QE", "reviewer_role": "quality_engineer",
                               "candidates": {
                                   "candidate-a": {"status": "use_with_fork",
                                                   "reason": "Bounded change needed",
                                                   "decision_id": "decision-one",
                                                   "authorization_ref": "FLPATH-5050",
                                                   "by_stage": stages}}})
        with self.assertRaisesRegex(ValueError, "required changes"):
            findings.reviewed_decisions(path, ["candidate-a"])
        value = json.loads(path.read_text())
        value["candidates"]["candidate-a"]["required_changes"] = ["Add bounded retries"]
        self.write_json(path, value)
        review = findings.reviewed_decisions(path, ["candidate-a"])
        report = findings.findings([findings.load_run(self.root / "one")], review)
        self.assertEqual("use_with_fork", report["decisions"]["candidate-a"]["status"])
        self.assertEqual("QE", report["decisions"]["candidate-a"]["reviewer"])



if __name__ == "__main__":
    unittest.main()
