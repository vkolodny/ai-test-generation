# FLPATH-5050: Evaluation Architecture

**Status:** Evaluation plan for review. No implementation tool or provider has been selected.

## Decision this spike supports

[FLPATH-5050](https://redhat.atlassian.net/browse/FLPATH-5050) compares ways to implement the AI Test Generation (ATG) Analyze → Plan → Code → Execute flow. Candidates may serve different layers. The outcome is a recommendation to reuse, fork, use as a reference, or replace a candidate **at a named layer**, supported by evidence. Running a candidate does not make it an ATG dependency.

The evaluator checks the test a candidate produces. It does not invoke a model, select an implementation, or change the ATG service contracts.

```text
Pinned Java fixture + shared task
  → candidate tool and approved model endpoint
  → generated Java test + redacted invocation record
  → common evaluator
       → unchanged fixture: test should pass
       → fixture with one known defect: test should fail by assertion
  → evaluation record + human comparison by layer
```

## Repository file map

- **Architecture and usage:** This document; [experiment README](../../experiments/harness/README.md).
- **Evaluators:** [candidate evaluator](../../experiments/harness/candidate_evaluation.py); [deterministic fake-model replay](../../experiments/harness/fake_model_replay.py).
- **Shared candidate task and control:** [blank-user objective](../../experiments/harness/fixtures/blank-user-objective.md); [manual reference Java test](../../experiments/harness/fixtures/TransferBlankUserTest.java); [reference invocation record](../../experiments/harness/fixtures/reference-invocation.json).
- **Earlier replay inputs:** [transfer objective](../../experiments/harness/fixtures/transfer-objective.json); [recorded Java test response](../../experiments/harness/fixtures/TransferGeneratedReplayTest.java).
- **Automated checks:** [candidate evaluator tests](../../tests/replay/test_candidate_evaluation.py); [fake-model replay tests](../../tests/replay/test_fake_model_replay.py); [test package marker](../../tests/replay/__init__.py).
- **Recorded results:** [current package-first ENMAAS experiment](../../experiments/harness/results/enmaas-package-first-2026-11-16/README.md) with [control](../../experiments/harness/results/enmaas-package-first-2026-11-16/control/evaluation.json), [Aider](../../experiments/harness/results/enmaas-package-first-2026-11-16/aider/evaluation.json), and [Pydantic AI](../../experiments/harness/results/enmaas-package-first-2026-11-16/pydantic-ai/evaluation.json); [earlier reference evaluation](../../experiments/harness/results/reference-blank-user-evaluation.json); [fake-model replay record](../../experiments/harness/results/fake-model-replay.json); [Java/Quarkus execution record](../../experiments/harness/results/quarkus-transfer-execution.json).

## Common technical experiment

1. Give each candidate the exact [blank-user objective](../../experiments/harness/fixtures/blank-user-objective.md) and the same pinned Java 21/Quarkus fixture. Keep the reference test and mutation out of the candidate's input.
2. Pin the candidate version, source revision, model, endpoint, configuration, prompt, and execution budget. Use the same approved model and endpoint across candidates where supported. When that is impossible, report the **tool + model + endpoint combination** as the result; do not attribute differences to the tool alone.
3. Save the new native Java test and a redacted invocation record. Do not change application code, existing tests, or build files.
4. Pass the test to the [candidate evaluator](../../experiments/harness/candidate_evaluation.py). It verifies the fixture digest and installs only the submitted test in two disposable copies. For **every** candidate and copy, it first runs offline `mvn -DskipTests package`, then runs only the submitted test class with offline `mvn test`. Both `@QuarkusTest` and `@QuarkusIntegrationTest` are allowed. The second copy changes `UserAuthPublicKey.doAuth` so an empty user is accepted.
5. Review the generated test, Maven reports, and evaluator record. Repeat candidate runs with fresh workspaces to observe model variability; keep every attempt, including failures.

The shared task uses a `requestReference` different from the existing fixture test's `req-001`; it does not require a random value on every run. The result is `discriminates` only if packaging succeeds for both copies, the submitted test passes on the unchanged fixture, and it fails by assertion on the changed fixture. The report records package and test commands, logs, and outcomes separately. Package failures, absent or skipped tests, timeouts, and environment failures remain blocked or inconclusive. This single behavior check does not prove a CVE fix or overall test-generation quality.

## Evaluate candidates by the layer they could own

Tomas's shortlist starts with Aider, one or two additional documented third-party tools, and [`fullsend-ai/test-creator`](https://github.com/fullsend-ai/test-creator) if it is practical to run. Include Pydantic AI. Prefer one provider across Analyze, Plan, Code, and Execute unless a specialized provider has a demonstrated advantage.

- **Generation and execution harness:** Run Aider on the common task. Screen [`ai-test-harness`](https://github.com/croadfeldt/ai-test-harness) and another documented third-party option for ownership, license, maintenance, and testability before choosing the next run. The SSC demo's Claude Code path is available for comparison if useful. Assess output, file scope, retries, recovery, local operation, and evidence.
- **Planning and test creation:** Try fullsend if setup is light. Evaluate its Plan or Code output at that boundary; do not score it as a full execution harness without execution evidence.
- **Model and orchestration integration:** Evaluate Pydantic AI and, if useful, direct SDKs or LangGraph for provider isolation, explicit state, checkpoints, replay, and failure recovery. A minimal adapter may be needed to expose the same task and evidence.
- **External evaluation:** Consider CyberGym and bounded VVAH as independent inputs, separate from implementation-harness selection.

Do not combine unlike layers into one overall score. State when a candidate can or cannot cover all four lifecycle stages. For **each viable option**, record ownership, licensing, security, maintenance, local testability, provider isolation, and ATG fit. Record `unknown` when evidence is missing. The [repository experiments guide](../../experiments/README.md) also requires source revision, tested capabilities, reproduction command, results, limitations, and a promotion or removal decision.

## Evidence and trust boundaries

Keep an evidence packet per candidate attempt: exact source and tool revisions; model and approved endpoint labels; prompt and sanitized configuration hashes; redacted command; generated-test content hash; fixture revision and verified file digest; container identity; exact Maven commands; exit codes; Surefire counts; logs; limitations; and unresolved decisions. Preserve candidate failures instead of reporting only successful attempts.

Model invocation and test execution are separate boundaries. The evaluator runs generated Java code with network access disabled, a read-only Maven cache, and CPU, memory, process, and file-size limits. The writable workspace has no total disk quota: use a disposable, quota-limited host or volume for real candidate output. The submitted test can write Surefire XML in its workspace, so `discriminates` is a review signal rather than tamper-proof evidence.

## Current evidence and decisions

The [current package-first experiment](../../experiments/harness/results/enmaas-package-first-2026-11-16/README.md) uses the current shared-task hash and the same pinned Java fixture. The manual control, Aider 0.86.2, and Pydantic AI 2.68.0 each returned `discriminates`: baseline pass and mutant assertion failure. Aider and Pydantic AI used the same ENMAAS `gpt-oss-120b` model. These are one-run, one-behavior results across different tool layers; they do not select a provider. Only the [older manual control](../../experiments/harness/results/reference-blank-user-evaluation.json) and pre-contract Aider, Pydantic AI, and direct-model exploratory runs used the earlier prompt or `mvn test`-only command and remain historical. The deterministic fake-model replay records Execute as `not_run`, with native Maven execution recorded separately. No candidate recommendation has been made.

ENMAAS and models.corp are sufficient test endpoints where available. The spike evaluates provider and harness integration, not model quality. Production is expected to support customer-provided models. The team owns experiment orchestration and evidence; keep experiment work under `experiments/`. Production application and pipeline ownership remain open. Treat dependencies and evidence coordination with other tickets as optional unless a tested integration requires them. After comparable runs, record a reuse/fork/reference/replace recommendation and the remaining gaps.
