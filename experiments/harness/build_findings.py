"""Build a reviewable FLPATH-5050 findings report from saved run evidence.

The report never executes candidates or reads credentials. Distinct provenance
cohorts stay separate so changed prompts, fixtures, models, or runners cannot
be mistaken for repeated trials.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from statistics import median
from typing import Dict, List, Optional, Tuple

SCHEMA = "flpath-5050.findings.v1"
MAX_EVIDENCE_JSON_BYTES = 16 * 1024 * 1024
MAX_GENERATED_TEST_BYTES = 16 * 1024 * 1024
STAGES = ("analysis", "plan", "code", "execute")
OUTCOMES = ("use", "use_with_fork", "reject", "insufficient_evidence")


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(chunk)
    return "sha256:" + checksum.hexdigest()


def read_json(path: Path) -> Optional[Dict[str, object]]:
    if not path.is_file() or path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        return None
    if path.stat().st_size > MAX_EVIDENCE_JSON_BYTES:
        raise ValueError(f"evidence JSON exceeds size limit: {path}")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def evidence(root: Path, path: Path) -> Dict[str, str]:
    if path.is_symlink() or not path.is_file() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError(f"missing or linked evidence: {path}")
    relative = path.relative_to(root).as_posix()
    return {"path": relative, "sha256": digest(path)}


def cohort_identity(summary: Dict[str, object]) -> Dict[str, object]:
    fields = ("fixture_profile", "fixture_tree_sha256", "objective_sha256",
              "model_id", "endpoint_id", "runner_sha256", "evaluator_sha256",
              "run_spec_sha256")
    return {field: summary.get(field) for field in fields}


def timing(folder: Path, filename: str) -> Optional[int]:
    record = read_json(folder / filename)
    if not record:
        return None
    milliseconds = record.get("elapsed_ms")
    if isinstance(milliseconds, int) and milliseconds >= 0:
        return milliseconds
    seconds = record.get("elapsed_seconds")
    return round(seconds * 1000) if isinstance(seconds, (int, float)) and seconds >= 0 else None


def generated_test(root: Path, folder: Path, evaluation: Optional[Dict[str, object]]) -> Optional[Dict[str, object]]:
    if not evaluation or not isinstance(evaluation.get("test"), dict):
        return None
    relative = evaluation["test"].get("path")
    if not isinstance(relative, str):
        return None
    target = PurePosixPath(relative)
    if target.is_absolute() or ".." in target.parts or target.parts[:3] != ("src", "test", "java"):
        return None
    for workspace in ("generated", "workspace"):
        path = folder / workspace / relative
        if not path.is_file() or path.is_symlink():
            continue
        if any(parent.is_symlink() for parent in path.parents if parent != folder.parent):
            continue
        if path.stat().st_size > MAX_GENERATED_TEST_BYTES:
            return None
        content = path.read_bytes()
        lines = content.decode("utf-8", errors="replace").splitlines()
        actual_hash = "sha256:" + hashlib.sha256(content).hexdigest()
        return {"bytes": len(content), "physical_lines": len(lines),
                "nonblank_lines": sum(bool(line.strip()) for line in lines),
                "hash_matches": actual_hash == evaluation["test"].get("sha256"),
                "evidence": evidence(root, path)}
    return None


def phase_timings(evaluation: Optional[Dict[str, object]]) -> Dict[str, Optional[int]]:
    result: Dict[str, Optional[int]] = {}
    if not evaluation or not isinstance(evaluation.get("cases"), dict):
        return result
    for case in ("baseline", "mutant"):
        record = evaluation["cases"].get(case)
        if not isinstance(record, dict):
            continue
        for phase in ("package", "test"):
            value = record.get(phase)
            if isinstance(value, dict):
                result[f"{case}_{phase}"] = value.get("elapsed_ms")
    coverage = evaluation.get("coverage", {})
    if isinstance(coverage, dict):
        for name, value in coverage.get("runs", {}).items():
            if isinstance(value, dict):
                result[name] = value.get("elapsed_ms")
    return result


def candidate_attempt(root: Path, name: str, outcome: str) -> Dict[str, object]:
    folder = root / name
    evaluation_path = folder / "evaluation/evaluation.json"
    evaluation = read_json(evaluation_path)
    stage_validation = read_json(folder / "stage-validation.json")
    stage_files = {"analysis": "analysis-run.json", "plan": "plan-run.json",
                   "code": "generation.json", "execute": "evaluation-run.json"}
    stages = {stage: timing(folder, filename) for stage, filename in stage_files.items()}
    stage_statuses = {stage: (read_json(folder / filename) or {}).get("status")
                      for stage, filename in stage_files.items()}
    invocation_path = folder / "invocation.json"
    if not invocation_path.is_file():
        invocation_path = folder / "generated/invocation.json"
    invocation = read_json(invocation_path)
    invocation_identity = {
        key: invocation.get(key) if invocation else None
        for key in ("config_sha256", "prompt_sha256", "tool_source_revision", "tool_version")
    }
    artifacts = [root / "summary.json", invocation_path]
    artifacts.extend(folder / filename for filename in stage_files.values())
    artifacts.extend([folder / "stage-validation.json", folder / "runner-error.json", evaluation_path])
    artifacts.extend(root / "checkpoints" / name / (stage + ".json") for stage in STAGES)
    refs = [evidence(root, path) for path in artifacts if path.is_file() and not path.is_symlink()]
    cases = evaluation.get("cases", {}) if evaluation else {}
    statuses = {case: cases.get(case, {}).get("status") if isinstance(cases.get(case), dict) else None
                for case in ("baseline", "mutant")}
    code = generated_test(root, folder, evaluation)
    issues = []
    if outcome == "discriminates":
        if not evaluation or evaluation.get("result") != "discriminates":
            issues.append("summary_success_without_matching_evaluation")
        if statuses != {"baseline": "passed", "mutant": "failed_assertion"}:
            issues.append("differential_cases_do_not_support_success")
        if not code:
            issues.append("generated_test_missing")
    if code and not code["hash_matches"]:
        issues.append("generated_test_hash_mismatch")
    verified_outcome = "invalid_evidence" if issues else outcome
    return {"candidate": name, "outcome": verified_outcome,
            "reported_outcome": outcome, "integrity_issues": issues, "stages_ms": stages,
            "stage_statuses": stage_statuses,
            "stage_validation": stage_validation,
            "invocation_identity": invocation_identity,
            "maven_phases_ms": phase_timings(evaluation),
            "generated_test": code,
            "coverage": evaluation.get("coverage", {"status": "unknown"}) if evaluation else {"status": "unknown"},
            "case_statuses": statuses, "evidence": refs}


def load_run(root: Path) -> Dict[str, object]:
    root = root.resolve()
    summary = read_json(root / "summary.json")
    if not summary or not isinstance(summary.get("candidate_results"), dict):
        raise ValueError(f"run lacks candidate results: {root}")
    kind = summary.get("run_kind", "candidate_generation")
    if kind not in ("candidate_generation", "evaluation_replay"):
        raise ValueError(f"unknown run kind: {root}")
    if kind == "evaluation_replay":
        manifest = read_json(root / "replay-manifest.json")
        if (not manifest or manifest.get("record_type") != "flpath-5050.evaluation-replay.v1"
                or manifest.get("source_summary_sha256") != summary.get("source_run_summary_sha256")):
            raise ValueError(f"replay packet lacks source linkage: {root}")
    candidates = summary["candidate_results"]
    if not all(isinstance(name, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name)
               and isinstance(value, str) for name, value in candidates.items()):
        raise ValueError(f"invalid candidate results: {root}")
    identity = cohort_identity(summary)
    spec_path = root / "run-spec.json"
    if spec_path.is_symlink():
        raise ValueError(f"run spec is linked: {root}")
    if spec_path.is_file():
        if spec_path.stat().st_size > MAX_EVIDENCE_JSON_BYTES:
            raise ValueError(f"run spec exceeds size limit: {root}")
        spec_hash = digest(spec_path)
        if identity["run_spec_sha256"] not in (None, spec_hash):
            raise ValueError(f"run spec hash mismatch: {root}")
        identity["run_spec_sha256"] = spec_hash
    return {"root": str(root), "kind": kind,
            "run_id": summary.get("run_id"),
            "candidate_order": summary.get("candidate_order"),
            "source_run_summary_sha256": summary.get("source_run_summary_sha256"),
            "identity": identity,
            "identity_hash": "sha256:" + hashlib.sha256(
                json.dumps(identity, sort_keys=True).encode()).hexdigest(),
            "evidence_scan": summary.get("evidence_scan"),
            "candidates": [candidate_attempt(root, name, outcome)
                           for name, outcome in sorted(candidates.items())]}


def reviewed_decisions(path: Optional[Path], names: List[str]) -> Dict[str, object]:
    if path is None:
        return {}
    review = read_json(path)
    if not review or review.get("schema") != "flpath-5050.decision-review.v1":
        raise ValueError("decision review needs versioned schema")
    for field in ("reviewer", "reviewer_role"):
        if not isinstance(review.get(field), str) or not review[field].strip():
            raise ValueError(f"decision review needs {field}")
    entries = review.get("candidates")
    if not isinstance(entries, dict) or set(entries) - set(names):
        raise ValueError("decision review contains unknown candidates")
    decision_ids = set()
    for name, entry in entries.items():
        if not isinstance(entry, dict) or entry.get("status") not in OUTCOMES:
            raise ValueError(f"invalid decision for {name}")
        for field in ("decision_id", "authorization_ref"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise ValueError(f"decision for {name} needs {field}")
        if entry["decision_id"] in decision_ids:
            raise ValueError("decision IDs must be unique")
        decision_ids.add(entry["decision_id"])
        if not isinstance(entry.get("reason"), str) or not entry["reason"].strip():
            raise ValueError(f"decision for {name} needs a reason")
        stages = entry.get("by_stage")
        if not isinstance(stages, dict) or set(stages) != set(STAGES):
            raise ValueError(f"decision for {name} needs all stage decisions")
        if any(value not in OUTCOMES for value in stages.values()):
            raise ValueError(f"invalid stage decision for {name}")
        status = entry["status"]
        if status == "use" and any(value != "use" for value in stages.values()):
            raise ValueError(f"use decision for {name} conflicts with stage decisions")
        if status == "use_with_fork":
            if not entry.get("required_changes"):
                raise ValueError(f"fork decision for {name} needs required changes")
            if "use_with_fork" not in stages.values() or any(
                    value in ("reject", "insufficient_evidence") for value in stages.values()):
                raise ValueError(f"fork decision for {name} conflicts with stage decisions")
        if status == "reject" and "reject" not in stages.values():
            raise ValueError(f"reject decision for {name} needs a rejected stage")
        if status == "insufficient_evidence" and "insufficient_evidence" not in stages.values():
            raise ValueError(f"insufficient decision for {name} needs an unresolved stage")
    return {"reviewer": review["reviewer"], "reviewer_role": review["reviewer_role"],
            "source_sha256": digest(path),
            "candidates": entries}


def findings(runs: List[Dict[str, object]],
             review: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    cohorts: Dict[str, List[Dict[str, object]]] = {}
    for run in runs:
        cohorts.setdefault(run["identity_hash"], []).append(run)
    candidate_names = sorted({attempt["candidate"] for run in runs
                              for attempt in run["candidates"]})
    decisions = {}
    for name in candidate_names:
        attempts = [attempt for run in runs if run["kind"] == "candidate_generation"
                    for attempt in run["candidates"] if attempt["candidate"] == name]
        replays = [attempt for run in runs if run["kind"] == "evaluation_replay"
                   for attempt in run["candidates"] if attempt["candidate"] == name]
        identities = {
            json.dumps({"run": run["identity_hash"], "invocation": attempt["invocation_identity"]},
                       sort_keys=True)
            for run in runs if run["kind"] == "candidate_generation"
            for attempt in run["candidates"] if attempt["candidate"] == name
        }
        durations = [sum(attempt["stages_ms"].values()) for attempt in attempts
                     if all(value is not None for value in attempt["stages_ms"].values())]
        run_ids = [run["run_id"] for run in runs if run["kind"] == "candidate_generation"
                   and any(a["candidate"] == name for a in run["candidates"])]
        independent = len(run_ids) == len(attempts) and all(run_ids) and len(set(run_ids)) == len(run_ids)
        orders = [tuple(run["candidate_order"]) if isinstance(run["candidate_order"], list) else None
                  for run in runs if run["kind"] == "candidate_generation"
                  and any(a["candidate"] == name for a in run["candidates"])]
        same_order = bool(orders) and all(orders) and len(set(orders)) == 1
        comparable = len(identities) == 1 and len(durations) >= 2 and independent and same_order
        tasks = {(run["identity"]["fixture_tree_sha256"],
                  run["identity"]["objective_sha256"])
                 for run in runs if run["kind"] == "candidate_generation"
                 for attempt in run["candidates"] if attempt["candidate"] == name}
        reasons = []
        if len(tasks) < 2:
            reasons.append("one behavior")
        if len(attempts) < 3 or len(identities) != 1 or not independent or not same_order:
            reasons.append("no fixed-configuration repeated trials")
        reasons.append("semantic and production constraints remain unreviewed")
        decisions[name] = {"status": "insufficient_evidence",
                           "decision_origin": "automated_pending",
                           "by_stage": {stage: "insufficient_evidence" for stage in STAGES},
                           "reason": "; ".join(reasons) + ".",
                           "task_count": len(tasks),
                           "attempts": len(attempts),
                           "evaluation_replays": len(replays),
                           "comparable_identity_count": len(identities),
                           "independent_run_ids": bool(independent),
                           "same_candidate_order": bool(same_order),
                           "discriminating_attempts": sum(a["outcome"] == "discriminates" for a in attempts),
                           "complete_wall_ms_median": median(durations) if comparable else None}
    if review:
        for name, entry in review["candidates"].items():
            related = [(run, attempt) for run in runs for attempt in run["candidates"]
                       if attempt["candidate"] == name]
            if entry["status"] in ("use", "use_with_fork"):
                if not any(run["kind"] == "candidate_generation"
                           and attempt["outcome"] == "discriminates"
                           for run, attempt in related):
                    raise ValueError(f"positive decision for {name} needs a discriminating generation attempt")
                if any(run["evidence_scan"] != "passed" or attempt["integrity_issues"]
                       for run, attempt in related):
                    raise ValueError(f"positive decision for {name} needs verified evidence")
            decisions[name].update({
                "status": entry["status"], "decision_origin": "reviewer",
                "by_stage": entry["by_stage"],
                "reason": entry["reason"],
                "required_changes": entry.get("required_changes", []),
                "reviewer": review["reviewer"],
                "reviewer_role": review["reviewer_role"],
                "decision_id": entry["decision_id"],
                "authorization_ref": entry["authorization_ref"],
                "review_source_sha256": review["source_sha256"],
            })
    return {"schema": SCHEMA, "cohort_count": len(cohorts), "runs": runs,
            "decisions": decisions,
            "limitations": ["Different identity hashes are not repeated trials.",
                            "Generated lines and coverage describe output, not test quality.",
                            "Use/fork/reject requires a reviewed rubric and production constraints."]}


def coverage_text(value: object) -> str:
    if not isinstance(value, dict) or value.get("status") != "measured":
        return value.get("status", "unknown") if isinstance(value, dict) else "unknown"
    measurements = value.get("measurements", {})
    before = measurements.get("existing_suite", {})
    after = measurements.get("existing_plus_candidate", {})
    parts = []
    for name in ("line", "branch"):
        old = before.get(name, {})
        new = after.get(name, {})
        if not isinstance(old, dict) or not isinstance(new, dict):
            return "coverage_unknown"
        old_total = old.get("covered", 0) + old.get("missed", 0)
        new_total = new.get("covered", 0) + new.get("missed", 0)
        parts.append(f"{name} {old.get('covered', 0)}/{old_total} -> "
                     f"{new.get('covered', 0)}/{new_total}")
    return ", ".join(parts)


def markdown(report: Dict[str, object]) -> str:
    lines = ["# FLPATH-5050 candidate findings", "",
             f"Runs: {len(report['runs'])}; comparable provenance cohorts: {report['cohort_count']}.",
             "", "## Candidate findings", ""]
    if any(run["kind"] == "evaluation_replay" for run in report["runs"]):
        lines.extend(["Evaluation replay packets reuse saved generated tests and their original stage timings.",
                      "Their Maven evidence was collected later with a named evaluator revision.", ""])
    for name, decision in report["decisions"].items():
        attempts = [(run["kind"], a) for run in report["runs"]
                    for a in run["candidates"] if a["candidate"] == name]
        lines.extend([f"### {name}", "",
                      f"{'Reviewer decision' if decision['decision_origin'] == 'reviewer' else 'Decision'}: "
                      f"**{decision['status']}**. {decision['reason']}",
                      f"Discriminating: {decision['discriminating_attempts']}/{decision['attempts']} generation attempt(s); "
                      f"evaluation replays: {decision['evaluation_replays']}.", ""])
        for kind, attempt in attempts:
            times = ", ".join(
                f"{stage}={attempt['stage_statuses'].get(stage) or 'unknown'} ({ms / 1000:.1f}s)"
                for stage, ms in attempt["stages_ms"].items() if ms is not None)
            code = attempt["generated_test"]
            size = (f"{code['physical_lines']} lines, {code['bytes']} bytes"
                    if code else "no saved generated test")
            coverage = attempt["coverage"]
            coverage_status = coverage_text(coverage)
            phases = ", ".join(f"{phase}={ms / 1000:.1f}s"
                               for phase, ms in attempt["maven_phases_ms"].items()
                               if isinstance(ms, (int, float)))
            direct = next((item for item in attempt["evidence"]
                           if item["path"].endswith("evaluation/evaluation.json")),
                          next((item for item in attempt["evidence"]
                                if item["path"].endswith("generation.json")),
                               attempt["evidence"][0]))
            validation = attempt.get("stage_validation") or {}
            semantic = validation.get("semantic_review", "unrecorded")
            lines.extend([f"- {kind}: {attempt['outcome']}; {times}; {size}; coverage: {coverage_status}.",
                          f"  Maven phases: {phases or 'unmeasured'}; semantic review: {semantic}."])
            if attempt["integrity_issues"]:
                lines.append(f"  Evidence integrity: {', '.join(attempt['integrity_issues'])}.")
            lines.append(f"  Evidence: {direct['path']} ({direct['sha256']}).")
        lines.append("")
    lines.extend(["## Interpretation", "",
                  "Line coverage alone cannot prove that a test detects the target defect.",
                  "Compare branch coverage and baseline-pass/mutant-assertion-fail evidence.",
                  "Treat setup failures and timeouts separately from test failures."])
    if all(value["status"] == "insufficient_evidence" for value in report["decisions"].values()):
        lines.append("Decisions remain insufficient until repeated runs and human stage review are complete.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", action="append", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--decision-review", type=Path)
    args = parser.parse_args()
    if len({root.resolve() for root in args.run_root}) != len(args.run_root):
        parser.error("run roots must be unique")
    if args.output_dir.exists():
        parser.error("output directory must be new")
    runs = [load_run(root) for root in args.run_root]
    names = sorted({a["candidate"] for run in runs for a in run["candidates"]})
    review = reviewed_decisions(args.decision_review, names)
    report = findings(runs, review)
    args.output_dir.mkdir(parents=True)
    (args.output_dir / "findings.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    (args.output_dir / "findings.md").write_text(markdown(report))
    print(args.output_dir / "findings.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
