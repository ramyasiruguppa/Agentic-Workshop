---
title: 'The rationale judge and the report'
type: 'feature'
created: '2026-09-26'
status: 'in-progress'
baseline_commit: 'a7a4df09c8ba1bb3667e76303396af1c537a0c84'
route: 'dispatch'
review_loop_iteration: 0
context: ['{project-root}/_bmad-output/specs/spec-epic-3/SPEC.md', '{project-root}/eval/run_eval.py']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The eval scores decisions only in code: nothing judges whether a rationale is sound, and the numbers are visible only in the MLflow UI (Epic 3 SPEC, CAP-6 and CAP-7, and the reporting half of CAP-8).

**Approach:** Extend `eval/run_eval.py` with a fifth scorer, `rationale_judge`. It uses `ChatGroq` (model from `JUDGE_MODEL`, default `openai/gpt-oss-120b`, key from `GROQ_API_KEY`) to return `pass` or `fail` with a one-line reason for each ticket, given the ticket text, the agent's decision and rationale, and the ticket's `judge_notes`. After the run, the script prints and writes to `eval/latest_report.json` the mean of all five scorers, the agent's total tokens and the number of auto-approved escalations.

## Boundaries & Constraints

**Always:**
- The judge never reads `GEMINI_API_KEY`, whatever `PROVIDER` is set to.
- A ticket whose output is `None` gets `fail` ("no decision to judge") without calling Groq.
- Judge model calls are not counted in the agent's tokens and do not add traces to the eval run.
- If the judge call itself errors (e.g. Groq 429 or 5xx after `ChatGroq`'s retries), the ticket is left unjudged: the scorer records an error rather than `pass`/`fail`.
- `rationale_judge`'s mean is its pass rate over judged tickets only (`pass` = 1, `fail` = 0).
- The agent's total tokens is the sum of `total_tokens` from `trace.info.token_usage` over the run's 20 ticket traces.
- The report JSON holds `run_id`, `scorers` (name → mean, all five), `agent_total_tokens`, `escalations_auto_approved` and `rationale_judge_unjudged` (the count of unjudged tickets). The printout shows the same numbers.
- A missing `GROQ_API_KEY` stops the run in `preflight()` before `evaluate`.
- The story 1 behavior stays: one run, one worker by default, and every existing scorer.

**Never:**
- No edits to `agent.py`, `run_agent.py`, `triage/`, `mcp/`, `TRIAGE_POLICY.md`, `seed/` or `eval/labelled_tickets.csv`.
- No second MLflow run, and no new file besides `eval/latest_report.json` (already git-ignored).
- No LangSmith or Databricks.
- No network or keys in tests.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Sound rationale | Judge model answers pass | `pass` with a one-line reason | N/A |
| Unsound rationale | Judge model answers fail | `fail` with a one-line reason | N/A |
| No decision | output `None` | `fail`, "no decision to judge"; no Groq call | N/A |
| Unparseable judge reply | reply is neither pass nor fail | `fail` with the reason "unparseable judge reply" | N/A |
| Gemini isolation | `PROVIDER` unset, only `GEMINI_API_KEY` removed | Judge still builds from `GROQ_API_KEY` | N/A |
| Judge call error | Groq raises after retries | Ticket unjudged; left out of the pass rate; counted in `rationale_judge_unjudged` | Error recorded on the assessment |
| No Groq key | `GROQ_API_KEY` unset | Run stops before `evaluate` | Message names `GROQ_API_KEY` |
| Report | Finished run | Five means, total tokens, approvals and the unjudged count printed and written to `eval/latest_report.json` | N/A |

</frozen-after-approval>

## Code Map

- `eval/run_eval.py` -- extend. Add the `rationale_judge` scorer to `SCORERS`, `GROQ_API_KEY` to `preflight()`, and after `evaluate` a `build_report(result, approver)` plus printing and writing `eval/latest_report.json` (path anchored to `ROOT`). `auto_approver.count` already counts approvals.
- `mlflow.entities.Feedback(value=..., rationale=...)` -- the scorer return type for pass/fail with a reason. The scorer gets `inputs`, `outputs`, `expectations` and `trace`; `expectations["judge_notes"]` is already in the dataset. Get the ticket text from the trace's `get_ticket` span outputs, or from `seed/tickets.csv` (read only).
- `trace.info.token_usage` -- a dict with `total_tokens`, filled in by LangChain autolog (the story 3.1 run summed to 67,649). Get the run's traces with `mlflow.search_traces(locations=[experiment_id], run_id=result.run_id, return_type="list")`.
- `langchain_groq.ChatGroq(model=..., api_key=..., max_retries=2)` -- installed. So that judge calls don't create traces, run them outside autolog, e.g. inside `with mlflow.tracing.disable()` or an equivalent context, and check that in a test.
- `result.metrics` -- has `<scorer>/mean` for numeric scorers. Compute the judge's pass rate yourself if it isn't there.
- `tests/test_run_eval.py` -- existing fixtures (`main_env`, a temporary tracking store) can be reused. Stub the judge model with a fake that returns fixed text.

## Tasks & Acceptance

**Execution:**
- [ ] `eval/run_eval.py` -- the `rationale_judge` scorer, the Groq key check in preflight, and the report build, print and JSON write -- CAP-6, CAP-7 and CAP-8 reporting.
- [ ] `tests/test_run_eval.py` -- every matrix row offline with a fake judge model; the report built from a fake result with fake traces; `GEMINI_API_KEY` never read by the judge -- proves CAP-6 and CAP-7.

**Acceptance Criteria:**
- Given no network, when `uv run pytest` runs, then all tests pass.
- Given `PROVIDER=groq`, a valid `GROQ_API_KEY` and a loaded `app.db`, when `uv run python eval/run_eval.py` runs, then it prints five means, the agent's total tokens, the approval count and the unjudged count, writes the same numbers to `eval/latest_report.json`, and the run holds exactly 20 traces.

## Implementation Notes

## Spec Change Log

## Review Triage Log

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass.
- `PROVIDER=groq uv run python eval/run_eval.py && cat eval/latest_report.json` -- expected: five means, tokens and approvals, identical in both.
