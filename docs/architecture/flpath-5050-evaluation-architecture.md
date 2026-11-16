# FLPATH-5050: Evaluation Architecture

**Status:** Proposed evaluation plan for review. No implementation tool, model, or provider has been selected.

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

## Common technical experiment

1. Give each candidate the exact [blank-user objective](../../experiments/harness/fixtures/blank-user-objective.md) and the same pinned Java 21/Quarkus fixture. Keep the reference test and mutation out of the candidate's input.
2. Pin the candidate version, source revision, model, endpoint, configuration, prompt, and execution budget. Use the same approved model and endpoint across candidates where supported. When that is impossible, report the **tool + model + endpoint combination** as the result; do not attribute differences to the tool alone.
3. Save the new native Java test and a redacted invocation record. Do not change application code, existing tests, or build files.
4. Pass the test to the [candidate evaluator](../../experiments/harness/candidate_evaluation.py). It verifies the fixture's file digest, installs only the submitted test in two disposable copies, and runs only that test class with Maven. The second copy changes `UserAuthPublicKey.doAuth` so an empty user is accepted.
5. Review the generated test, Maven reports, and evaluator record. Repeat candidate runs with fresh workspaces to observe model variability; keep every attempt, including failures.

The result is `discriminates` only if the submitted test runs and passes on the unchanged fixture, then fails by assertion on the changed fixture. A test that passes on both copies does not detect this defect. Compilation failures, absent or skipped tests, timeouts, and environment failures remain blocked or inconclusive. This single behavior check does not prove a CVE fix or overall test-generation quality.

## Evaluate candidates by the layer they could own

- **Generation and execution harness:** Run the SSC demo's Aider and Claude Code paths on the common task. Assess the generated test and their file-scope controls, retries, failure handling, local operation, and evidence. Assess [`ai-test-harness`](https://github.com/croadfeldt/ai-test-harness) for reusable generation, differential, self-check, and provenance capabilities. Verify its ownership, license, and fit before reuse or forking.
- **Planning and test creation:** Assess [`fullsend-ai/test-creator`](https://github.com/fullsend-ai/test-creator) and internal test-plan tooling for the Plan or Code capabilities they demonstrate. Evaluate their outputs at that boundary; do not score them as full execution harnesses without execution evidence.
- **Model and orchestration integration:** Assess direct SDKs, Pydantic AI, and LangGraph for provider isolation, explicit state, checkpoints, replay, and failure recovery. A minimal adapter may be needed to expose the same task and evidence. Compare these as integration choices, not as competing generated tests unless each produces one.
- **External evaluation:** Consider CyberGym and bounded VVAH use as independent evaluation inputs. They can add vulnerability-specific or differential tasks; they are not implementation harness choices. Keep benchmark results separate from this Java behavior check.

Do not combine unlike layers into one overall score. For **each viable option**, record ownership, licensing, security, maintenance, local testability, provider isolation, and ATG fit. Record `unknown` when evidence is missing. The [repository experiments guide](../../experiments/README.md) also requires source revision, tested capabilities, reproduction command, results, limitations, and a promotion or removal decision.

## Evidence and trust boundaries

Keep an evidence packet per candidate attempt: exact source and tool revisions; model and approved endpoint labels; prompt and sanitized configuration hashes; redacted command; generated-test content hash; fixture revision and verified file digest; container identity; exact Maven commands; exit codes; Surefire counts; logs; limitations; and unresolved decisions. Preserve candidate failures instead of reporting only successful attempts.

Model invocation and test execution are separate boundaries. The evaluator runs generated Java code with network access disabled, a read-only Maven cache, and CPU, memory, process, and file-size limits. The writable workspace has no total disk quota: use a disposable, quota-limited host or volume for real candidate output. The submitted test can write Surefire XML in its workspace, so `discriminates` is a review signal rather than tamper-proof evidence.

## Current evidence and pending decisions

The [manual control test](../../experiments/harness/fixtures/TransferBlankUserTest.java) passed on the unchanged fixture and failed by assertion on the changed fixture. The replay and evaluator Python checks passed. The [reference evaluation record](../../experiments/harness/results/reference-blank-user-evaluation.json) captures this control run. The earlier deterministic fake-model replay uses synthetic Analyze and Plan inputs and records Execute as `not_run`; its native Maven execution is recorded in a separate result file. These results establish a reproducible starting point for candidate evaluation.

No Aider, Claude Code, other candidate, or approved model endpoint has yet been evaluated with this protocol. No candidate recommendation has been made.

**Review with Tomas:** confirm the shortlist for each layer, the approved model endpoint, the owner of orchestration and evidence, and whether this one task is sufficient for the spike or needs additional tasks. Coordinate pipeline ownership with [FLPATH-5018](https://redhat.atlassian.net/browse/FLPATH-5018) and input-contract decisions with [FLPATH-5017](https://redhat.atlassian.net/browse/FLPATH-5017). After candidate runs, record a layer-specific reuse/fork/reference/replace recommendation with the evidence and remaining gaps.
