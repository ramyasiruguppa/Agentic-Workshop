---
title: 'The eval run and the four code scorers'
type: 'feature'
created: '2026-09-26'
status: 'done'
baseline_commit: '680b3295905104484bd861595b3069981b3db447'
route: 'dispatch'
review_loop_iteration: 0
context: ['{project-root}/_bmad-output/specs/spec-epic-3/SPEC.md', '{project-root}/agent.py', '{project-root}/triage/schema.py']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing measures how well the Epic 2 agent triages (Epic 3 SPEC, CAP-1 to CAP-5 and CAP-8).

**Approach:** Add `eval/run_eval.py`. `uv run python eval/run_eval.py` reads all 20 rows of `eval/labelled_tickets.csv` and drives `agent.triage` through `mlflow.genai.evaluate` with four code scorers: `valid_schema`, `category_match`, `priority_match` and `tool_order`. It logs exactly one MLflow run to the `triage-agent` experiment at `sqlite:///mlflow.db`. Every escalation is approved automatically by an approver passed to `triage`, so the run never waits for a person.

## Boundaries & Constraints

**Always:**
- Tracking URI `sqlite:///mlflow.db`, experiment `triage-agent`, `mlflow.langchain.autolog()`; `load_dotenv()` before building the agent.
- Each ticket's prediction is one trace. The predict function is wrapped with `mlflow.trace`, so the resume after an approved escalation stays inside it.
- Each scorer returns 1 or 0:
  - `valid_schema`: the output validates as `TriageDecision`.
  - `category_match`: the output's `category` equals `expected_category`.
  - `priority_match`: the output's `priority` equals `expected_priority`.
  - `tool_order`: the trace has a `get_ticket` span that starts before the `get_customer_history` span.
- If the agent raises on a ticket, the eval continues. That ticket's output is `None`, so `valid_schema`, `category_match` and `priority_match` score 0.
- The run evaluates one ticket at a time: set `MLFLOW_GENAI_EVAL_MAX_WORKERS=1` unless the user already set it, to stay under free-tier rate limits.
- The auto-approver counts approvals, exposed for story 2's report.
- The agent runs on whatever `PROVIDER` selects. A full run needs about 60–80 model calls, more than Gemini's free tier of 20 a day, so `PROVIDER=groq` or a paid key is needed.

**Never:**
- No edits to `agent.py`, `run_agent.py`, `triage/`, `mcp/`, `TRIAGE_POLICY.md`, `seed/` or `eval/labelled_tickets.csv`.
- No `rationale_judge`, no printed means or token totals, no `eval/latest_report.json`; those are story 2.
- No hand-rolled scoring loop, and no LangSmith or Databricks.
- No network or keys in tests.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Data | `eval/labelled_tickets.csv` | 20 rows; inputs `{"ticket_id"}`, expectations `expected_category`, `expected_priority` | N/A |
| Correct decision | output billing/P2, expected billing/P2 | valid_schema 1, category_match 1, priority_match 1 | N/A |
| Wrong priority | output P3, expected P2 | priority_match 0; others unaffected | N/A |
| Invalid output | `{"category":"sales",...}` or `None` | valid_schema 0; category/priority 0 when `None` | No exception from scorers |
| Tool order right | trace: get_ticket then get_customer_history | tool_order 1 | N/A |
| Tool order wrong or missing | reversed, or no `get_customer_history` span | tool_order 0 | N/A |
| Escalating ticket | T-1044 | auto-approved; no terminal prompt; approval counted | N/A |
| Agent error | `triage` raises | ticket output `None`; run continues | Error logged on that trace |

</frozen-after-approval>

## Code Map

- `agent.py` -- read only. `async triage(ticket_id, approve=ask_terminal) -> dict`; an approver returning `True` approves and is asked at most once per ticket. Retry-once lives inside `triage`.
- `run_agent.py` -- read only; its MLflow setup lines are the pattern to repeat.
- `mlflow.genai.evaluate(data, scorers, predict_fn)` -- MLflow 3.16. `data` rows are dicts `{"inputs": {"ticket_id": ...}, "expectations": {...}}`, and `predict_fn(ticket_id)` receives the inputs as keyword arguments. The `@scorer` decorator is in `mlflow.genai.scorers`; scorer functions take `outputs`, `expectations` and `trace`. The workers setting is `MLFLOW_GENAI_EVAL_MAX_WORKERS` (default 10).
- The predict function is sync: `asyncio.run(agent.triage(ticket_id, approve=...))` inside an `mlflow.trace`-wrapped function. Tool spans are named `get_ticket` and `get_customer_history` (span type TOOL), as seen in the Epic 2 traces.
- `eval/labelled_tickets.csv` -- read only. Columns are `ticket_id, expected_category, expected_priority, expected_tools, judge_notes`. `judge_notes` is for story 2; it can be carried in the expectations now.
- `tests/conftest.py` -- puts the repo root on `sys.path`. Import the script as a module, e.g. via `importlib`, since `eval/` has no `__init__.py`.

## Tasks & Acceptance

**Execution:**
- [x] `eval/run_eval.py` -- dataset builder, auto-approver with a counter, the traced predict function, the four scorers, and `main()` calling `mlflow.genai.evaluate` in one run -- CAP-1 to CAP-5 and CAP-8.
- [x] `tests/test_run_eval.py` -- every matrix row offline: the dataset from the real CSV, the scorers on hand-built outputs and fake traces, and the predict function with `agent.triage` stubbed (approves escalations, and an error returns `None`) -- proves the offline parts.

**Acceptance Criteria:**
- Given no network, when `uv run pytest` runs, then all tests pass.
- Given `PROVIDER=groq`, a valid `GROQ_API_KEY` and a loaded `app.db`, when `uv run python eval/run_eval.py` runs, then it finishes with no prompt, and exactly one new MLflow run in `triage-agent` holds 20 traces scored by the four scorers.

## Implementation Notes

- `main()` also sets `MLFLOW_GENAI_EVAL_SKIP_TRACE_VALIDATION=true` unless already set. Without it, `mlflow.genai.evaluate` calls the predict function once more as a check, which spends model calls and can add a phantom escalation to the count.
- `predict` records the exception on its span (the trace shows ERROR) and returns `None`.
- `tool_order` compares the earliest start of each span name and scores 0 if either span is missing.
- Tests write MLflow data to a temporary database, never the repo's `mlflow.db`.
- Review fixes: `preflight()` exits before `evaluate` on a bad provider, a missing key or a missing `app.db`; the tracking URI is anchored to the repo root; `valid_schema` catches only `ValidationError`; `main()` and the trace-shows-ERROR path now have tests. After the fixes: `uv run pytest -q` → 85 passed; `PROVIDER=bogus` stops with a clear message.
- Verified before review: `uv run pytest -q` → 81 passed. Live `PROVIDER=groq` run `e8285a0474064c27b1163edfa7d22880`: one run, 20 traces, 3 auto-approvals; means valid_schema 0.85, category_match 0.8, priority_match 0.85, tool_order 0.9. Three tickets failed on Groq: T-1050 and T-1058 hit the 8000 tokens-per-minute free-tier limit (429); on T-1048 the model called a nonexistent `commentary` tool (400).

## Spec Change Log

## Review Triage Log

| # | Source | Finding | Verdict | Evidence | Route |
|---|--------|---------|---------|----------|-------|
| 1 | verification-gap, blind | `main()` wiring (env defaults, experiment, one evaluate call) untested | medium | Pre-verified: dropping MAX_WORKERS, SKIP_TRACE_VALIDATION or a scorer leaves all 12 tests green. | patch |
| 2 | verification-gap, blind | Agent-error row never checks the trace is ERROR with an exception | medium | Pre-verified: deleting `record_exception` leaves the test green. | patch |
| 3 | blind | Missing key or `app.db` gives a "successful" all-zero run | medium | `predict` swallows every error; `build_model()` raises AgentConfigError per ticket, and a missing `app.db` fails every `get_ticket`. | patch (fail before `evaluate`) |
| 4 | blind, verification-gap | `sqlite:///mlflow.db` resolves against the current directory | low | Running from `eval/` writes `eval/mlflow.db`, which the UI command never shows; `LABELS_PATH` is already anchored to `ROOT`. | patch |
| 5 | blind | `valid_schema` catches every Exception | low | A code bug would be scored as invalid output; narrowing to `ValidationError` is a direct correction. | patch |
| 6 | blind | Leftover `if True:` block in a test | low | Direct deletion. | patch |
| 7 | blind | 429 rate-limit errors are not retried | medium | 2 of 3 live failures were Groq tokens-per-minute 429s, so scores partly measure free-tier limits; backoff goes beyond the spec's "failures score 0". | defer |
| 8 | blind, edge-case | Global approver never reset; count races with >1 worker; never logged | low | One `main()` per process, one worker by default; story 2 owns reporting the count. | reject |
| 9 | blind, edge-case | `tool_order` compares starts only; ignores `expected_tools`; equal timestamps | low | The frozen spec defines tool_order by span start; sequential tool calls cannot share a nanosecond start. | reject |
| 10 | blind | Scorers return bare 0/1 with no reason | low | Nice-to-have; the spec asks for 1 or 0. | reject |
| 11 | edge-case | `setdefault` keeps a user's workers>1 or validation=false | false | The spec says "unless the user already set it". | reject |
| 12 | edge-case | `expectations` may be None | false | Every dataset row carries expectations. | reject |
| 13 | edge-case | Invalid-schema output can still earn category/priority credit | low | The scorers are independent by design in the spec. | reject |
| 14 | edge-case | Real tool spans may be named differently | false | Live run scored tool_order 0.9; a test checks a real local trace. | reject |
| 15 | edge-case | Bad or empty label rows | low | `eval/labelled_tickets.csv` is read-only and complete. | reject |
| 16 | edge-case | Test assumes `get_last_active_trace_id` is not None | low | Opaque failure only in a test; negligible. | reject |

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass.
- `PROVIDER=groq uv run python eval/run_eval.py` -- expected: finishes unattended; one new run with 20 scored traces.
