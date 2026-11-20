# FLPATH-5050: repeat the four-candidate experiment

The paths and credential location in this guide are examples from the original operator's environment. Use approved paths and a secure secret source in your environment.

Run this procedure as `root` on `sealusa35`. It prepares a clean checkout and
fresh runtimes, then runs Aider, Pydantic AI, Claude Code, and LangGraph against
the same pinned Java task. The final command streams the EnMaaS key from the
Mac; the key is never copied into the checkout or a command argument.
All candidates get the same four Java context files. Aider also gets the
fixture's `pom.xml` so it can see the Maven project configuration.

## 1. Prepare source and images on `sealusa35`

Open a shell with `ssh sealusa35`, then run these commands in that shell:

```sh
REPRO_ROOT=/root/tmp/flpath-5050-repro-001
ATG_ROOT="$REPRO_ROOT/ai-test-generation"
FIXTURE_REPO="$REPRO_ROOT/java-analysis-adapter"
FIXTURE_ROOT="$FIXTURE_REPO/spike/java21-quarkus"
RUNTIME_ROOT="$REPRO_ROOT/runtimes"
MAVEN_CACHE="$REPRO_ROOT/maven-cache/repository"
RUN_VOLUME="$REPRO_ROOT/quota-output"
RUN_OUTPUT="$RUN_VOLUME/run-001"
mkdir -p "$REPRO_ROOT" "$RUNTIME_ROOT" "$MAVEN_CACHE"
git clone --branch flpath-5050-fake-model-replay \
  https://github.com/vkolodny/ai-test-generation.git "$ATG_ROOT"
git clone https://github.com/project-lightpath/java-analysis-adapter.git \
  "$FIXTURE_REPO"
git -C "$FIXTURE_REPO" checkout --detach \
  2aac181ae63d938c118bac133d33fb8ae19eeec9
git -C "$ATG_ROOT" rev-parse HEAD
git -C "$FIXTURE_REPO" rev-parse HEAD
```

The first revision printed above identifies the runner source used in this
attempt. Record it with the evidence. After the runner is integrated into the
canonical ATG repository, clone that repository at its reviewed revision
instead of the fork branch.

Put live output on a disposable volume with an enforced **total size limit**.
The host's root XFS filesystem has no quota, and the evaluator limits
individual files but not total workspace use. After explicit approval for the
mount, create this dedicated 4 GiB temporary filesystem. The prior four-tool
scratch run used about 155 MiB. Keep the mount until you have reviewed and
copied the selected, redacted evidence to persistent storage.

```sh
free -h
mkdir -p "$RUN_VOLUME"
mount -t tmpfs -o size=4G,mode=0700 tmpfs "$RUN_VOLUME"
df -h "$RUN_VOLUME"
```

The runner checks the exact local image IDs used in the recorded experiment.
On `sealusa35`, these images already exist. If an image is missing, pull it;
if the preflight later reports a different ID, stop and record a new experiment
configuration rather than calling it the same run:

```sh
podman image exists docker.io/library/python:3.11-slim || \
  podman pull docker.io/library/python:3.11-slim
podman image exists docker.io/library/maven:3.9.11-eclipse-temurin-21 || \
  podman pull docker.io/library/maven:3.9.11-eclipse-temurin-21
podman image exists quay.io/sscdemo/ai-agent-maven-aider:v1.0.1 || \
  podman pull quay.io/sscdemo/ai-agent-maven-aider:v1.0.1
```

## 2. Install the pinned local runtimes

Create both Python 3.11 environments inside the pinned Python container. The
requirements files pin Pydantic AI 2.68.0, LangGraph 1.0.8, and OpenAI SDK 2.8.1.

```sh
podman run --rm --pull=never --network=host \
  -v "$ATG_ROOT:/repo:ro,Z" \
  -v "$RUNTIME_ROOT:/runtimes:Z" \
  docker.io/library/python:3.11-slim \
  sh -c 'python -m venv /runtimes/pydantic-venv && \
    /runtimes/pydantic-venv/bin/pip install -r \
      /repo/experiments/harness/candidates/pydantic_ai/requirements.txt && \
    python -m venv /runtimes/langgraph-venv && \
    /runtimes/langgraph-venv/bin/pip install -r \
      /repo/experiments/harness/candidates/langgraph/requirements.txt'
npm install --prefix "$RUNTIME_ROOT/claude" --no-audit --no-fund \
  node@22.23.5 @anthropic-ai/claude-code@2.1.296
```

The npm step prepares pinned `node_modules`. The runner mounts that directory
read-only and executes Claude Code as a non-root process inside the pinned
Python container; it does not run the host's Node installation. Claude's
configuration directory is an in-container temporary filesystem. It is not
bound to the host or part of the evidence packet.

Warm Maven's **package** lifecycle online. Running `mvn test` alone did not
cache `maven-jar-plugin` 3.4.1, which the evaluator needs during its package
step. The evaluator later mounts this cache read-only with network disabled.

```sh
podman run --rm --pull=never --network=host \
  -v "$FIXTURE_ROOT:/workspace:Z" \
  -v "$MAVEN_CACHE:/root/.m2/repository:Z" \
  -w /workspace \
  docker.io/library/maven:3.9.11-eclipse-temurin-21 \
  mvn -B -ntp -Ptest -DskipTests package
```

Before invoking any model, verify that the exact package command succeeds
offline with the cache read-only. Stop and repair the cache if it fails.

```sh
podman run --rm --pull=never --network=none \
  -v "$FIXTURE_ROOT:/workspace:Z" \
  -v "$MAVEN_CACHE:/root/.m2/repository:ro,Z" \
  -w /workspace \
  docker.io/library/maven:3.9.11-eclipse-temurin-21 \
  mvn -o -B -ntp -Ptest -DskipTests package
```

## 3. Check the run without using the key

Keep the same shell variables from steps 1 and 2. `--check-only` verifies the
fixture and objective hashes, local image IDs, tool versions, runtime paths,
and that `RUN_OUTPUT` does not exist. It does not call EnMaaS. Preflight runs
before the output directory exists, so capture its failure outside that
directory and stop if it fails.

```sh
set -o pipefail
python3 "$ATG_ROOT/experiments/harness/run_comparison.py" \
  --fixture-root "$FIXTURE_ROOT" \
  --maven-cache "$MAVEN_CACHE" \
  --output-root "$RUN_OUTPUT" \
  --pydantic-venv "$RUNTIME_ROOT/pydantic-venv" \
  --langgraph-venv "$RUNTIME_ROOT/langgraph-venv" \
  --claude-runtime-dir "$RUNTIME_ROOT/claude/node_modules" \
  --check-only 2>&1 | tee "$REPRO_ROOT/preflight.log"
```

## 4. Run from the Mac with the key streamed over SSH

Leave `RUN_OUTPUT` absent. On the local machine, set `ENMAAS_KEY_FILE`
to the path of your private key file. Replace the example path before running:

```sh
ENMAAS_KEY_FILE=/path/to/your/private/enmaas-key
```

Run the following command **on the local machine**, where that file exists. Do not paste the key into chat, a shell command, or
the repository. The remote runner reads one line from standard input.

```sh
ssh -T sealusa35 '
set -o pipefail
python3 /root/tmp/flpath-5050-repro-001/ai-test-generation/experiments/harness/run_comparison.py \
  --fixture-root /root/tmp/flpath-5050-repro-001/java-analysis-adapter/spike/java21-quarkus \
  --maven-cache /root/tmp/flpath-5050-repro-001/maven-cache/repository \
  --output-root /root/tmp/flpath-5050-repro-001/quota-output/run-001 \
  --pydantic-venv /root/tmp/flpath-5050-repro-001/runtimes/pydantic-venv \
  --langgraph-venv /root/tmp/flpath-5050-repro-001/runtimes/langgraph-venv \
  --claude-runtime-dir /root/tmp/flpath-5050-repro-001/runtimes/claude/node_modules \
  --api-key-stdin 2>&1 | tee /root/tmp/flpath-5050-repro-001/live-run.log
' < "$ENMAAS_KEY_FILE"
```

The live log is outside `RUN_OUTPUT`, so it also captures a preflight error
that occurs before the output directory is created. Stop and resolve any
preflight error before rerunning; the output directory remains absent.

Inspect `quota-output/run-001/summary.json` and each candidate's `generation.json`, test,
redacted invocation/configuration, command records, evaluator JSON, and logs.
The runner exits successfully only when all four tests pass on the original
fixture and fail by assertion on its seeded defect. Failed or timed-out
attempts remain in their candidate directories. Use a new output directory
such as `run-002` for a repeat; never overwrite `run-001`.

Before copying evidence, require `"evidence_scan": "passed"` in `summary.json`.
The runner scans every output file for the exact key and blocks the run if it
finds the key, a symlink, or a nonregular file. If the scan is blocked, **do not
copy or publish that output**; investigate it in the temporary volume. After
a scan pass, review the files and copy only the needed summary, redacted
commands and `sanitized-config.json`, generated tests, evaluator JSON, and logs
to persistent scratch or the repository. Exclude any raw Claude configuration,
credential cache, or unreviewed candidate output if present. Unmount the
temporary filesystem only after the evidence review and copy are complete.

Reproducibility here means the same source, task, model, tool versions,
procedure, and evidence checks. Model output need not be byte-identical.
This one behavior does not select a production tool or prove a CVE fix.
