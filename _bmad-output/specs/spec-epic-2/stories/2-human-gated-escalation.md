---
title: 'Human-gated escalation'
type: 'feature'
created: '2026-09-26'
status: 'done'
baseline_commit: '707ed684be08445ed36cc4e7db575b1cfa77f523'
route: 'dispatch'
review_loop_iteration: 0
context: ['{project-root}/_bmad-output/specs/spec-epic-2/SPEC.md', '{project-root}/TRIAGE_POLICY.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The policy says a final P1 for an Enterprise customer must be escalated to a person, but the agent has no escalation tool and nothing stops it from escalating on its own (Epic 2 SPEC, CAP-5).

**Approach:** Add a local `escalate_to_human` tool to the agent and gate it with LangChain's `HumanInTheLoopMiddleware`, so every call pauses the run for approval. The model decides when to call it, following the policy. `triage()` resumes the paused run with the approver's answer: "yes" lets the tool run, and anything else rejects it. The approver is a parameter whose default is a yes/no prompt at the terminal, so Epic 3 can pass an auto-approver without changing the agent.

## Boundaries & Constraints

**Always:** Only an explicit "yes" (case-insensitive, `y` accepted) approves; "no"/`n` rejects; any other answer re-asks; end of input rejects. The prompt names the ticket and the model's stated reason. After each approval answer, the agent prints one status line to stdout, `Escalated to a person: yes` on approval or `Escalation declined: not escalated` on rejection, so it appears before `run_agent.py`'s decision JSON. The returned decision stays exactly the four `TriageDecision` fields, so Epic 3's `valid_schema` still holds. Retry-once and every story 1 behavior stay unchanged. A paused run resumes as the same agent run (same thread), so the trace keeps one `get_ticket` → `get_customer_history` sequence.

**Never:** No edits to `run_agent.py`, `triage/schema.py`, `mcp/triage_server.py`, `TRIAGE_POLICY.md`, `seed/` or `eval/`. `escalate_to_human` does not go into the MCP server. No escalation without an approver "yes". No real model calls or terminal input in tests.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Approved | P1 + Enterprise ticket (e.g. T-1044), answer `yes` | Tool runs; `Escalated to a person: yes` printed; decision returned | N/A |
| Rejected | Same ticket, answer `no` | Tool does not run; `Escalation declined: not escalated` printed; decision still returned | N/A |
| Unclear answer | `maybe`, then `y` | Re-asks once, then approves | N/A |
| No input | stdin closed (EOF) | Treated as `no` | N/A |
| No escalation | T-1042 (P2) | No prompt; decision returned | N/A |
| Custom approver | `triage(id, approve=lambda req: True)` | Approved with no terminal prompt | N/A |

</frozen-after-approval>

## Code Map

- `agent.py` -- extend. `build_agent(model, tools)` gains the local `escalate_to_human(ticket_id: str, reason: str)` tool, `HumanInTheLoopMiddleware(interrupt_on={"escalate_to_human": {"allowed_decisions": ["approve", "reject"]}})`, and an `InMemorySaver` checkpointer (interrupts need one). `triage(ticket_id, approve=ask_terminal)` invokes with a fresh `thread_id`. While the result carries `__interrupt__`, it calls `approve(request)` and resumes with `Command(resume={"decisions": [{"type": "approve"}]})` or `{"type": "reject", "message": ...}`. The retry-once wrapper stays around the whole resumable run.
- `langchain.agents.middleware.HumanInTheLoopMiddleware`, `langgraph.types.Command`, `langgraph.checkpoint.memory.InMemorySaver` -- installed. The interrupt value is a `HITLRequest` whose `action_requests[0]` holds the tool name and args.
- `run_agent.py` -- read only. It calls `asyncio.run(triage(ticket_id))`, so the default approver prompts at the terminal. `input()` runs through `asyncio.to_thread`.
- `tests/test_agent.py` -- the existing `stub` fixture monkeypatches `build_agent(model, tools)`. Keep that signature, or update the fixture in the same change.
- `eval/labelled_tickets.csv` -- read only. T-1044, T-1048 and T-1057 are the Enterprise P1 tickets.

## Tasks & Acceptance

**Execution:**
- [x] `agent.py` -- add `escalate_to_human`, the middleware, the checkpointer, the `approve` parameter with a terminal default, and the resume loop -- CAP-5.
- [x] `tests/test_agent.py` -- cover every matrix row offline with a fake tool-calling model and stubbed approvers or stdin; assert the tool body runs only on approval -- proves CAP-5.

**Acceptance Criteria:**
- Given no network, when `uv run pytest` runs, then all tests pass.
- Given a valid key and a loaded `app.db`, when `uv run python run_agent.py T-1044` runs and the person answers `no`, then the run completes, `Escalation declined: not escalated` is printed, nothing is escalated, and the decision JSON still has exactly four fields.

## Implementation Notes

- `build_agent(model, tools)` keeps its signature and adds `escalate_to_human`, the HITL middleware and an `InMemorySaver`. Each attempt runs on a new `thread_id`; resumes reuse it.
- `AGENT_INSTRUCTIONS` gained steps telling the model to call `escalate_to_human` only for a final P1 with an Enterprise customer, then return its decision either way.
- A custom approver approves only by returning `True` or "yes"/"y". Several escalation requests in one pause get one shared answer. A retry after invalid output asks the approver again.
- Review fixes: the approver is asked at most once per `triage()` call (later requests auto-reject with the earlier outcome), a mismatched ticket ID auto-rejects without asking, and async approvers of any shape are awaited. Verified after the fixes: `uv run pytest -q` → 69 passed; Groq T-1044 + `no` → one prompt, one declined line, four-field decision.
- Verified before review: `uv run pytest -q` → 59 passed. Groq live: T-1044 + `no` → declined line, access / P1 / access-team, one trace with get_ticket → get_customer_history; T-1044 + `yes` → approved line, trace includes escalate_to_human; T-1042 → no prompt, billing / P2. Gemini not run (daily quota exhausted).
- MLflow's LangChain tracer logs `on_interrupt`/`on_resume` AttributeErrors on pause and resume (installed MLflow has no handlers); traces are still complete.

## Spec Change Log

## Review Triage Log

| # | Source | Finding | Verdict | Evidence | Route |
|---|--------|---------|---------|----------|-------|
| 1 | blind, edge-case | Retry after an approved escalation escalates the ticket again and re-asks | medium | `triage()` re-runs `_run_once` on a fresh thread with no memory of the approval; any invalid output after approval triggers it. | patch |
| 2 | blind, edge-case | Resume loop is unbounded: a model that re-calls the tool re-prompts every time; an always-no approver runs to GraphRecursionError | medium | `while result.get("__interrupt__")` has no cap; after a reject, only the message text stops the model. | patch (same fix as 1: one approver question per triage call) |
| 3 | blind, edge-case | Escalated ticket ID comes from the model, not the run | medium | `_describe` and `_hand_to_person` use `args["ticket_id"]` unchecked; ticket text is untrusted (AGENTS.md). | patch |
| 4 | blind, edge-case, verification-gap | `iscoroutinefunction` misses async `__call__`/partials; async approver untested | medium | Pre-verified: removing the coroutine branch leaves all tests green; such approvers silently reject. | patch |
| 5 | blind, edge-case | `triage` docstring says only `True` approves; strings "yes"/"y" do too | low | Direct correction. | patch |
| 6 | verification-gap | String "yes"/"y" approval untested | low | Pre-verified: deleting the string branch leaves tests green. | patch |
| 7 | verification-gap, blind, edge-case | Two escalation calls in one pause untested | medium | Pre-verified: hard-coding one decision leaves tests green; a parallel-calling model would crash. | patch |
| 8 | verification-gap, blind | Retry not checked to run on a fresh thread | medium | Pre-verified: a fixed thread id leaves tests green and leaks the failed attempt's messages. | patch |
| 9 | edge-case | Ctrl+C during the prompt may hang (input() in a worker thread) | maybe-false (medium if true) | `asyncio.run` waits on the default executor at shutdown; not verified interactively. | defer |
| 10 | blind | MLflow tracer logs on_interrupt/on_resume AttributeErrors | low | Installed MLflow lacks the handlers; traces are complete. The fix touches dependencies or `run_agent.py`. | defer |
| 11 | blind | Model's reason printed unfiltered before the prompt | low | Labelled as the model's reason; control-character injection is unlikely; filtering adds code. | reject |
| 12 | blind | No record of escalations for Epic 3 | low | Trace records the tool call; Epic 3 counts through its own approver. | reject |
| 13 | blind | "Escalated" line printed before the tool runs | low | The tool body only returns a string; it cannot realistically fail. | reject |
| 14 | blind | `test_escalate_tool_not_in_mcp_server` is not offline | false | Starts a local stdio subprocess; no network; listing needs no `app.db`. | reject |
| 15 | blind | Mid-file imports, unused `then` parameter in tests | low | Cosmetic. | reject |
| 16 | edge-case | Approver exception escapes unwrapped | low | Loud failure; nothing escalates. | reject |
| 17 | edge-case | Empty `action_requests` sends one decision | false | The middleware only interrupts with at least one request. | reject |
| 18 | edge-case | Status lines on stdout break JSON parsers | false | Human chose stdout lines before the JSON. | reject |
| 19 | edge-case | `y` approves | false | Frozen spec accepts `y`. | reject |

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass.
- `PROVIDER=groq uv run python run_agent.py T-1044` -- expected: a yes/no prompt; `no` gives a decision with nothing escalated.
- `PROVIDER=groq uv run python run_agent.py T-1042` -- expected: no prompt; billing / P2 / billing-team.
