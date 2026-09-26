---
title: 'Triage-decision schema'
type: 'feature'
created: '2026-09-26'
status: 'done'
baseline_commit: 'dc9f023d53cc087e6ce9445909a6a3d20f63c261'
route: 'dispatch'
review_loop_iteration: 0
context: ['{project-root}/_bmad-output/specs/spec-epic-1/SPEC.md', '{project-root}/TRIAGE_POLICY.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Epic 2's agent and Epic 3's `valid_schema` scorer both need one enforced shape for a triage decision, and none exists yet (Epic 1 SPEC, CAP-1).

**Approach:** Add a `TriageDecision` Pydantic model in `triage/schema.py` that accepts only the allowed categories, priorities and routes, requires the route to be the one `TRIAGE_POLICY.md` pairs with the category, and rejects anything else with an error naming the offending field. Also export `TriageValidationError` for Epic 2 to raise.

## Boundaries & Constraints

**Always:** Import path is `from triage.schema import TriageDecision, TriageValidationError`. `TriageDecision.model_validate(dict)` is the validation entry point. Allowed values: category `billing|bug|access|performance|how-to`; priority `P1|P2|P3|P4`; route `billing-team|bug-team|access-team|performance-team|how-to-team`; pairing billing→billing-team, bug→bug-team, access→access-team, performance→performance-team, how-to→how-to-team. Every rejection names the offending field. Extra fields are rejected: an object with any key beyond `category`, `priority`, `route` and `rationale` fails validation, and the error names the extra key. The rationale must be a non-empty string once surrounding whitespace is ignored; no single-sentence check (sentence quality is Epic 3's rationale judge's job). Python 3.12+, uv; any package via `uv add`.

**Never:** No network calls, no API keys, no model calls. No loader, agent, MCP or eval code (story 2 and later epics). No edits to `seed/`, `TRIAGE_POLICY.md`, `mcp/triage_server.py`, `run_agent.py` or `eval/labelled_tickets.csv`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Valid | `{"category":"billing","priority":"P2","route":"billing-team","rationale":"Double charge puts money at stake."}` | Validates; fields readable | N/A |
| Bad priority | same with `"priority":"P5"` | Rejected | Error names `priority` |
| Bad category | `"category":"sales"` | Rejected | Error names `category` |
| Bad route value | `"route":"sales-team"` | Rejected | Error names `route` |
| Mismatched pair | `billing` with `bug-team` | Rejected | Error names `route` |
| Missing field | no `rationale` key | Rejected | Error names `rationale` |
| Wrong type | `"priority": 2` | Rejected | Error names `priority` |
| Empty rationale | `"rationale": "   "` | Rejected | Error names `rationale` |
| Extra field | valid object plus `"confidence": 0.9` | Rejected | Error names `confidence` |

</frozen-after-approval>

## Code Map

- `triage/__init__.py` -- new, empty package marker.
- `triage/schema.py` -- new; `TriageDecision` (Pydantic v2 `BaseModel`) and `TriageValidationError` (plain `Exception` subclass; Epic 2 raises it after a failed retry, the schema does not need to raise it).
- `tests/conftest.py` -- new; puts the repo root on `sys.path` so tests import `triage` (pytest `testpaths = ["tests"]` is already set in `pyproject.toml`).
- `tests/test_schema.py` -- new; one test per I/O matrix row.
- `pyproject.toml` -- read only; `pydantic>=2.8` and `pytest>=8` already present, no new dependency needed.
- `TRIAGE_POLICY.md` -- read only; source of the category→route table.

## Tasks & Acceptance

**Execution:**
- [x] `triage/__init__.py` -- create empty -- makes `triage` importable.
- [x] `triage/schema.py` -- define `TriageDecision` with the four fields using `Literal` types, a validator enforcing the category→route pairing, and `TriageValidationError` -- the cross-epic contract.
- [x] `tests/conftest.py` -- add repo root to `sys.path` -- tests import the package without installing it.
- [x] `tests/test_schema.py` -- cover every I/O matrix row, asserting the error names the field -- proves CAP-1.

**Acceptance Criteria:**
- Given a clean checkout with no `app.db` and no `.env`, when `uv run pytest` runs, then all schema tests pass without network access.
- Given a valid decision, when it is serialised with `model_dump()`, then the result is a dict with exactly `category`, `priority`, `route` and `rationale`.

## Implementation Notes

## Spec Change Log

## Review Triage Log

| # | Source | Finding | Verdict | Evidence | Route |
|---|--------|---------|---------|----------|-------|
| 1 | verification-gap | No test accepts P1, P3 or P4 | medium | Pre-verified: every valid-input test uses P2; shrinking `Priority` would pass all 17 tests. | patch |
| 2 | edge-case, blind | Fields can be reassigned after validation (`d.route = "bug-team"` accepted) | medium | Reproduced: mismatched route set after validation is kept; breaks "anything else is rejected" for Epic 2's post-decision priority bump. | patch |
| 3 | blind | `test_mismatched_pair` does not assert `route` is the only error field | low | `str(exc)` check is trivially true (message contains "route"); direct fix: assert fields == {"route"}. | patch |
| 4 | edge-case, blind | `ROUTE_FOR_CATEGORY` could drift from `Category` and raise KeyError | low | No drift today; both are a frozen cross-epic contract; fix adds a guard or extra test. | reject |
| 5 | edge-case | Module-level dict is mutable by importers | low | No caller mutates it; guarding adds MappingProxyType for a hypothetical. | reject |
| 6 | edge-case | Zero-width-only rationale passes | low | Real but unlikely from an LLM; fix adds unicode filtering; human chose non-empty-only. | reject |
| 7 | edge-case, blind | No max length on rationale | low | Human chose non-empty string only; a bound is a new constraint beyond intent. | reject |
| 8 | blind | Fields lack `Field(description=...)` for the LLM | low | Enforcement unaffected; descriptions don't change field names or allowed values, so Epic 2 can add them or put rules in its prompt. | reject |
| 9 | blind | `additionalProperties: false` may break Gemini structured output | false | Checked offline: langchain-google-genai drops the key with a warning; `with_structured_output` builds for json_schema and function_calling. | reject |
| 10 | blind | Missing-`category` path untested | low | Same branch as invalid category (`info.data` lacks the key), which is tested. | reject |
| 11 | blind | Lax mode accepts `bytes` rationale | low | Reproduced from a Python dict only; JSON-parsed LLM output cannot carry bytes; `strict=True` changes behavior beyond the spec. | reject |
| 12 | blind | `TriageValidationError` test is trivial | low | Spec requires only a plain `Exception` subclass; the test proves that. | reject |
| 13 | blind | No `__all__` in `triage/schema.py` | low | Cosmetic; import path works as specified. | reject |

## Design Notes

The pairing check must report its error under `route`, not at model level. A `model_validator(mode="after")` puts the error at location `()`, which fails the "names `route`" row. Use a `field_validator("route")` that reads `info.data.get("category")`: fields validate in declaration order, so declare `category` before `route`. If `category` itself failed, it is absent from `info.data`; skip the pairing check so only the `category` error is reported. Use `model_config = ConfigDict(extra="forbid")` for extra fields. Check the rationale with `.strip()` without mutating the stored value.

## Verification

**Commands:**
- `uv run pytest tests/test_schema.py -q` -- expected: all tests pass.
- `uv run python -c "from triage.schema import TriageDecision, TriageValidationError"` -- expected: exits 0.
