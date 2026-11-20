# FLPATH-5050: Spring transfer comparison

This is a **second fixture** for the four-candidate experiment. It uses the
Spring Boot application linked from FLPATH-5000 at commit
`01a18a1b40e151ffcc1d7bef86bf335f87999750`. It does not run the
`ssc-demo` pipeline or deploy the application.

This guide records the `spring-transfer` experiment on `sealusa35`.
Its paths and credential location are examples from that host and operator.
Use paths and an approved secret source for your environment. Record the ATG
checkout revision, dirty state, and runner hash; a dirty checkout needs a saved
source snapshot for reproduction. See [FINDINGS.md](FINDINGS.md) for the separate
`spring-xml-deposit` profile and current reporting workflow.

## 1. Confirm the application baseline

The pinned app is at
`/root/tmp/flpath-5050-spring-001/spring-boot-example-application`.
It uses Spring Boot 2.7.18 and Java 17. The Maven image is
`docker.io/library/maven:3.9.11-eclipse-temurin-17`, image ID
`bbb7e05a6487b189e3dc833b6360b4f9eaf0154299fb4e67e764cad5cca33800`.
The dependency cache is
`/root/tmp/flpath-5050-spring-001/maven-cache/repository`.

On `sealusa35`, online `mvn -B -ntp verify` passed **121 tests, 0 failures,
0 errors, 0 skipped**. Offline `mvn -o -B -ntp verify` passed with network
disabled and the cache mounted read-only. Logs:
`/root/tmp/flpath-5050-spring-001/baseline-verify.log` and
`baseline-offline-verify.log`. The repository instructions mention a
`settings.xml` absent at this commit; the verified commands did not need it.
Always repeat the offline build before model calls.

Run these commands **on sealusa35**:

```sh
REPRO_ROOT=/root/tmp/flpath-5050-spring-001
ATG_ROOT="$REPRO_ROOT/ai-test-generation"
APP_ROOT="$REPRO_ROOT/spring-boot-example-application"
MAVEN_CACHE="$REPRO_ROOT/maven-cache/repository"
RUNTIME_ROOT=/root/tmp/flpath-5050-item1-clean-001/runtimes
RUN_VOLUME="$REPRO_ROOT/quota-output"
RUN_OUTPUT="$RUN_VOLUME/run-001"
git -C "$APP_ROOT" rev-parse HEAD
podman image inspect --format '{{.Id}}' \
  docker.io/library/maven:3.9.11-eclipse-temurin-17
podman run --rm --pull=never --network=none \
  -v "$APP_ROOT:/workspace:Z" \
  -v "$MAVEN_CACHE:/root/.m2/repository:ro,Z" \
  -w /workspace \
  docker.io/library/maven:3.9.11-eclipse-temurin-17 \
  mvn -o -B -ntp verify
```

## 2. Check the controls

The controlled defect changes **only** the `transfer` balance guard in
`TransactionService.java` from `< 0` to `<= 0`. The separate mutant checkout
at `$REPRO_ROOT/spring-transfer-mutant-control` passed the existing **121
tests**. The reference `SpringTransferExactBalanceReferenceTest.java` passed
on the original app and failed by assertion on the mutant. The evaluator's
manual reference packet is
`$REPRO_ROOT/reference-evaluation-control/evaluation.json`; it reports
`discriminates`. Logs are `mutant-control-verify.log`,
`reference-baseline-test.log`, and `reference-mutant-test.log` under
`$REPRO_ROOT`. These controls are not candidate results. Do not give the
reference test or mutant source to candidates. To repeat them with the warmed
cache, use a new evaluator output path:

```sh
podman run --rm --pull=never --network=none \
  -v "$REPRO_ROOT/spring-transfer-mutant-control:/workspace:Z" \
  -v "$MAVEN_CACHE:/root/.m2/repository:ro,Z" \
  -w /workspace \
  docker.io/library/maven:3.9.11-eclipse-temurin-17 \
  mvn -o -B -ntp verify
python3 "$ATG_ROOT/experiments/harness/candidate_evaluation.py" \
  --profile spring-transfer \
  --candidate-test-file "$ATG_ROOT/experiments/harness/fixtures/SpringTransferExactBalanceReferenceTest.java" \
  --candidate-test-path src/test/java/com/redhat/lightwell/service/SpringTransferExactBalanceReferenceTest.java \
  --candidate manual-reference \
  --tool-version 1 \
  --model-id none \
  --endpoint-id none \
  --invocation-record "$ATG_ROOT/experiments/harness/fixtures/spring-reference-invocation.json" \
  --fixture-root "$APP_ROOT" \
  --maven-cache "$MAVEN_CACHE" \
  --output-root "$REPRO_ROOT/reference-evaluation-repeat-001"
```

## 3. Preflight the staged run

The runner supplies the same pinned context, objective, model, and output
rules to Aider, Pydantic AI, Claude Code, and LangGraph. It records
`prepare.json` with source, build, tool, and context identities. Each
candidate must save `analysis.json` and `plan.json` **before** creating a new
JUnit test. The shared evaluator then runs the test on the original app and
hidden mutant with network disabled.

Use the pinned Python and Claude runtimes under `$RUNTIME_ROOT`. If absent,
follow [RERUN.md](RERUN.md) to install them. The live output must be a new
path on a dedicated size-limited volume. After approval for a mount, run:

```sh
mkdir -p "$RUN_VOLUME"
mount -t tmpfs -o size=4G,mode=0700 tmpfs "$RUN_VOLUME"
df -h "$RUN_VOLUME"
python3 "$ATG_ROOT/experiments/harness/run_comparison.py" \
  --profile spring-transfer \
  --fixture-root "$APP_ROOT" \
  --maven-cache "$MAVEN_CACHE" \
  --output-root "$RUN_OUTPUT" \
  --pydantic-venv "$RUNTIME_ROOT/pydantic-venv" \
  --langgraph-venv "$RUNTIME_ROOT/langgraph-venv" \
  --claude-runtime-dir "$RUNTIME_ROOT/claude/node_modules" \
  --check-only
```

`--check-only` uses no EnMaaS key and makes no model call. Stop if the exact
fixture, objective, image, runtime, or output-root checks fail. The runner
will not overwrite an existing attempt; use `run-002` for a repeat.

## 4. Run all four candidates

After preflight passes, set `ENMAAS_KEY_FILE` on your local machine to
the path of your private key file. Replace the example path before running:

```sh
ENMAAS_KEY_FILE=/path/to/your/private/enmaas-key
```

Run the command below on that machine. The key is streamed over SSH standard
input; it is not a command argument or a file in the checkout. Do not paste it
into chat or save unreviewed terminal output. This run makes model calls and
can take several minutes.

```sh
ssh -T sealusa35 '
python3 /root/tmp/flpath-5050-spring-001/ai-test-generation/experiments/harness/run_comparison.py \
  --profile spring-transfer \
  --fixture-root /root/tmp/flpath-5050-spring-001/spring-boot-example-application \
  --maven-cache /root/tmp/flpath-5050-spring-001/maven-cache/repository \
  --output-root /root/tmp/flpath-5050-spring-001/quota-output/run-001 \
  --pydantic-venv /root/tmp/flpath-5050-item1-clean-001/runtimes/pydantic-venv \
  --langgraph-venv /root/tmp/flpath-5050-item1-clean-001/runtimes/langgraph-venv \
  --claude-runtime-dir /root/tmp/flpath-5050-item1-clean-001/runtimes/claude/node_modules \
  --api-key-stdin
' < "$ENMAAS_KEY_FILE"
```

If the command exits with an error, retain that attempt for review. Do not
call a setup failure a candidate failure. Use a fresh output path for any
repeat; never overwrite `run-001`.

## 5. Review evidence

Inspect `run-001/summary.json`, `prepare.json`, and each candidate folder:

1. `analysis.json` identifies the transfer method, existing coverage,
   exact-balance gap, and feasibility. A missing or unsupported analysis is a
   stage gap even if later code happens to pass.
2. `plan.json` names the test path, setup, expected balances, and limits. It
   must be saved before generation.
3. The generated Java test changes no production or existing test file.
4. `evaluation/evaluation.json` reports baseline pass and mutant assertion
   failure. A setup failure or unavailable environment is **inconclusive**.

Require `"evidence_scan": "passed"` before copying output. The exact-key
scan blocks on a credential match, symlink, nonregular file, or read error;
it cannot detect partial or transformed credentials. Review selected files,
copy only redacted commands/configuration, stage JSON, generated tests, and
evaluator reports/logs to private scratch, then unmount the temporary volume.
Do not publish a blocked or unreviewed packet.

One Spring behavior does not rank the tools or prove a CVE fix.
