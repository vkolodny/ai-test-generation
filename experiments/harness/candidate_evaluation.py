"""Evaluate one candidate-generated Java test against a fixed behavior mutation.

FLPATH-5050 experiment: accepts test output from any candidate. This script
does not invoke a model or decide which agent should be adopted.
"""

import argparse
import hashlib
import json
import re
import resource
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional
from xml.etree import ElementTree

FIXTURE_REVISION = "2aac181ae63d938c118bac133d33fb8ae19eeec9"
FIXTURE_DIGEST = "sha256:5e19aa18e2019ecf9fe2559648002bac97467da685f5ef3fb4eefa0251080efe"
MUTATION_PATH = Path("src/main/java/com/projectlightpath/fixture/target/UserAuthPublicKey.java")
ORIGINAL_LINE = "return strict && user != null && !user.isBlank();"
MUTATED_LINE = "return true;"
DEFAULT_IMAGE = "docker.io/library/maven:3.9.11-eclipse-temurin-21"
SHARED_OBJECTIVE = Path(__file__).resolve().parent / "fixtures/blank-user-objective.md"
MAX_LOG_BYTES = 16 * 1024 * 1024


def sha256(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root)
        if relative.parts[0] == "target" or ".git" in relative.parts:
            continue
        digest.update(relative.as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return "sha256:" + digest.hexdigest()


def test_target(value: str) -> Path:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.parts[:3] != ("src", "test", "java"):
        raise ValueError("test path must stay under src/test/java")
    if not path.name.endswith("Test.java"):
        raise ValueError("test file must end with Test.java")
    return Path(*path.parts)



def load_invocation(path: Path, args: argparse.Namespace) -> Dict[str, object]:
    record = json.loads(path.read_text())
    identities = {"candidate": args.candidate, "tool_version": args.tool_version,
                  "model_id": args.model_id, "endpoint_id": args.endpoint_id}
    if not isinstance(record, dict) or any(record.get(k) != v for k, v in identities.items()):
        raise ValueError("invocation identities must match evaluator arguments")
    command = record.get("redacted_command")
    if not isinstance(command, list) or any(not isinstance(part, str) for part in command):
        raise ValueError("invocation needs a redacted command argument list")
    secret_field = re.compile(r"(?i)(api[_-]?key|token|password|secret|authorization|bearer)")
    placeholders = {"[REDACTED]", "<redacted>"}
    for index, part in enumerate(command):
        if not secret_field.search(part):
            continue
        inline_value = part.split("=", 1)[1] if "=" in part else None
        following = command[index + 1] if index + 1 < len(command) else None
        if inline_value is not None and inline_value not in placeholders:
            raise ValueError("invocation command may contain a secret; use [REDACTED]")
        if inline_value is None and following not in placeholders:
            raise ValueError("invocation command may contain a secret; use [REDACTED]")
    for field in ("prompt_sha256", "config_sha256"):
        value = record.get(field)
        if value is not None and (not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value)):
            raise ValueError("invocation " + field + " must be a SHA-256 digest")
    if args.model_id != "none" and (not command or not record.get("prompt_sha256")
                                     or not record.get("config_sha256")):
        raise ValueError("real-model invocation needs command, prompt and config digests")
    if args.model_id != "none" and record.get("prompt_sha256") != sha256(SHARED_OBJECTIVE.read_bytes()):
        raise ValueError("candidate prompt digest does not match shared objective")
    return {**identities, "redacted_command": command,
            "prompt_sha256": record.get("prompt_sha256"),
            "config_sha256": record.get("config_sha256"),
            "tool_source_revision": record.get("tool_source_revision"),
            "record_sha256": sha256(path.read_bytes())}


def copy_fixture(source: Path, destination: Path) -> None:
    def ignore_build(directory: str, names: List[str]) -> List[str]:
        ignored = [".git"] if ".git" in names else []
        if Path(directory) == source and "target" in names:
            ignored.append("target")
        return ignored

    shutil.copytree(str(source), str(destination), ignore=ignore_build)


def mutate_fixture(root: Path) -> str:
    path = root / MUTATION_PATH
    source = path.read_text()
    if source.count(ORIGINAL_LINE) != 1:
        raise ValueError("fixture mutation anchor must occur exactly once")
    path.write_text(source.replace(ORIGINAL_LINE, MUTATED_LINE))
    return sha256(path.read_bytes())


def surefire_result(root: Path, class_name: str) -> Dict[str, int]:
    reports = list((root / "target/surefire-reports").glob("TEST-*.xml"))
    matching = []
    for report in reports:
        suite = ElementTree.parse(str(report)).getroot()
        name = suite.attrib.get("name", "")
        if name == class_name or name.endswith("." + class_name):
            matching.append(suite)
    if len(matching) != 1:
        return {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "assertion_failures": 0}
    suite = matching[0]
    counts = {field: int(suite.attrib.get(field, 0))
              for field in ("tests", "failures", "errors", "skipped")}
    counts["assertion_failures"] = sum(
        1 for test in suite.findall("testcase") for failure in test.findall("failure")
        if "AssertionError" in failure.attrib.get("type", ""))
    return counts


def _limit_log_file() -> None:
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_LOG_BYTES, MAX_LOG_BYTES))


def _remove_container(cid_file: Path) -> None:
    if not cid_file.is_file():
        return
    cid = cid_file.read_text().strip()
    if cid:
        subprocess.run(["podman", "rm", "-f", cid], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=30)


def run_case(root: Path, label: str, class_name: str, cache: Path, image: str) -> Dict[str, object]:
    cid_file = root.parent / (label + ".cid")
    command = [
        "podman", "run", "--rm", "--cidfile", str(cid_file), "--pull=never",
        "--network=none", "--cap-drop=all", "--security-opt=no-new-privileges",
        "--pids-limit=256", "--memory=2g", "--cpus=2",
        "--ulimit", "fsize=134217728:134217728",
        "-e", "JAVA_TOOL_OPTIONS=-XX:ActiveProcessorCount=2",
        "-v", str(root) + ":/workspace:Z",
        "-v", str(cache) + ":/root/.m2/repository:ro,Z",
        "-w", "/workspace", image,
        "mvn", "-o", "-B", "-ntp", "-Ptest",
        "-Dtest=" + class_name, "-Dsurefire.failIfNoSpecifiedTests=true", "test",
    ]
    log = root.parent / (label + ".log")
    try:
        with log.open("wb") as output:
            result = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT,
                                    timeout=900, preexec_fn=_limit_log_file,
                                    start_new_session=True)
    except subprocess.TimeoutExpired:
        return {"status": "blocked_timeout", "exit_code": None, "tests": {},
                "log": str(log), "command": command}
    finally:
        _remove_container(cid_file)
    if log.stat().st_size >= MAX_LOG_BYTES:
        return {"status": "blocked_output_limit", "exit_code": result.returncode,
                "tests": {}, "log": str(log), "command": command}
    counts = surefire_result(root, class_name)
    if counts["tests"] == 0:
        status = "blocked_no_tests"
    elif counts["tests"] <= counts["skipped"]:
        status = "blocked_no_executed_tests"
    elif counts["errors"] > 0:
        status = "blocked_test_error"
    elif counts["failures"] > 0:
        status = ("failed_assertion" if result.returncode != 0
                  and counts["assertion_failures"] == counts["failures"]
                  else "blocked_test_failure")
    elif result.returncode != 0:
        status = "blocked_runner_failure"
    else:
        status = "passed"
    return {"status": status, "exit_code": result.returncode, "tests": counts,
            "log": str(log), "command": command}


def verify_inputs(args: argparse.Namespace, target: Path) -> str:
    if not args.candidate_test_file.is_file() or not args.maven_cache.is_dir():
        raise ValueError("candidate test file or Maven cache is missing")
    if args.output_root.exists():
        raise ValueError("output directory must be new")
    digest = tree_digest(args.fixture_root.resolve())
    if digest != FIXTURE_DIGEST:
        raise ValueError("fixture content differs from pinned Java 21/Quarkus revision")
    if (args.fixture_root / target).exists():
        raise ValueError("candidate test would replace an existing fixture test")
    for value in (args.candidate, args.tool_version, args.model_id, args.endpoint_id):
        if not value.strip():
            raise ValueError("candidate, tool version, model, and endpoint identity are required")
    return digest


def evaluate(args: argparse.Namespace) -> Dict[str, object]:
    fixture = args.fixture_root.resolve()
    candidate_file = args.candidate_test_file.resolve()
    cache = args.maven_cache.resolve()
    output = args.output_root.resolve()
    target = test_target(args.candidate_test_path)
    invocation = load_invocation(args.invocation_record, args)
    actual_fixture_digest = verify_inputs(args, target)
    image = subprocess.check_output(
        ["podman", "image", "inspect", "--format", "{{.Id}}", args.image],
        universal_newlines=True).strip()
    output.mkdir(parents=True)
    results = {}
    for label in ("baseline", "mutant"):
        root = output / label
        copy_fixture(fixture, root)
        installed = root / target
        installed.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(candidate_file), str(installed))
        if label == "mutant":
            mutation_digest = mutate_fixture(root)
        results[label] = run_case(root, label, target.stem, cache, args.image)
    status = ("discriminates" if results["baseline"]["status"] == "passed"
              and results["mutant"]["status"] == "failed_assertion" else "inconclusive")
    report = {
        "record_type": "flpath-5050.candidate-evaluation",
        "shared_objective_sha256": sha256(SHARED_OBJECTIVE.read_bytes()),
        "candidate": {"name": args.candidate, "tool_version": args.tool_version,
                      "model_id": args.model_id, "endpoint_id": args.endpoint_id},
        "invocation": invocation,
        "test": {"path": str(target), "sha256": sha256(candidate_file.read_bytes())},
        "fixture": {"repository": "project-lightpath/java-analysis-adapter",
                    "revision": FIXTURE_REVISION, "verified_tree_sha256": actual_fixture_digest},
        "mutation": {"path": str(MUTATION_PATH), "old": ORIGINAL_LINE,
                     "new": MUTATED_LINE, "mutated_source_sha256": mutation_digest},
        "environment": {"image": args.image, "image_id": image,
                        "network": "disabled", "maven_cache": "offline-read-only",
                        "memory_limit": "2g", "cpu_limit": 2, "file_size_limit_bytes": 134217728},
        "result": status, "cases": results,
        "limitations": [
            "This checks sensitivity to one seeded application behavior change.",
            "It does not prove vulnerability remediation or model quality across tasks.",
            "A generated test that duplicates an existing test can still discriminate.",
            "Candidate invocation is external; its redacted command and digests are supplied by the caller.",
            "The writable workspace is not disk-quota isolated; run untrusted tests only on a disposable, quota-limited host.",
            "Surefire XML is in the candidate-writable workspace; results are a review signal, not tamper-proof proof.",
        ],
    }
    (output / "evaluation.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-test-file", required=True, type=Path)
    parser.add_argument("--candidate-test-path", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--tool-version", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--endpoint-id", required=True)
    parser.add_argument("--invocation-record", required=True, type=Path)
    parser.add_argument("--fixture-root", required=True, type=Path)
    parser.add_argument("--maven-cache", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    args = parser.parse_args()
    try:
        report = evaluate(args)
    except (ValueError, OSError, json.JSONDecodeError, subprocess.CalledProcessError, ElementTree.ParseError) as error:
        parser.error(str(error))
    print(json.dumps({"result": report["result"],
                      "baseline": report["cases"]["baseline"]["status"],
                      "mutant": report["cases"]["mutant"]["status"]}))
    return 0 if report["result"] == "discriminates" else 2


if __name__ == "__main__":
    sys.exit(main())
