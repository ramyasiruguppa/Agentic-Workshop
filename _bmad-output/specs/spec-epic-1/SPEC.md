---
id: SPEC-epic-1
companions: [../../../TRIAGE_POLICY.md, ../../../mcp/triage_server.py]
sources: [../../../INTENT.md]
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete, preservation-validated contract for what to build, test, and validate. Source documents listed in frontmatter are for traceability — consult them only if you need narrative rationale or prose color this contract intentionally omits.

# Epic 1: triage data and schema

## Why

The workshop's triage agent (Epic 2) and its eval (Epic 3) both need two things that don't exist yet: a fixed shape for a triage decision, and the seed tickets and customers in the `app.db` that `mcp/triage_server.py` already queries. Epic 1 builds that foundation. The schema is the contract both later epics check against, so its fields and allowed values must be settled before any agent code is written.

## Capabilities

- **CAP-1**
  - **intent:** A triage decision has a single, enforced shape: a JSON object with a category, a priority, a route and a one-sentence rationale.
  - **success:** A decision with category in {`billing`, `bug`, `access`, `performance`, `how-to`}, priority in {`P1`, `P2`, `P3`, `P4`}, route in {`billing-team`, `bug-team`, `access-team`, `performance-team`, `how-to-team`} and a rationale string validates. The route must be the one `TRIAGE_POLICY.md` pairs with the category (`billing`→`billing-team`, `bug`→`bug-team`, `access`→`access-team`, `performance`→`performance-team`, `how-to`→`how-to-team`). Any out-of-set value, mismatched category and route, missing field or wrong type is rejected with an error that names the offending field.

- **CAP-2**
  - **intent:** One command loads the seed tickets and customers into the local database the MCP server reads.
  - **success:** `uv run python load_seed.py` creates `app.db` with tables `tickets` and `customers` whose columns match the headers of `seed/tickets.csv` and `seed/customers.csv`, and whose row counts match the CSVs (24 tickets, 20 customers).

- **CAP-3**
  - **intent:** Reloading the seed data is safe and repeatable.
  - **success:** Running `load_seed.py` twice in a row succeeds both times and leaves identical table contents: no duplicated rows and no errors.

## Constraints

- Python 3.12 or newer, managed with uv. Packages are added with `uv add`, never pip.
- The files in `seed/` are read-only. The loader only reads them.
- No network calls and no API keys in any Epic 1 code or test.
- `mcp/triage_server.py` is unchanged and must keep working: `app.db` sits at the repo root with `tickets(ticket_id, customer_id, created_at, text)` and `customers(customer_id, name, plan, open_tickets)`.
- `app.db` is never committed.
- Epic 2 (the agent's structured output) and Epic 3 (the `valid_schema` scorer) consume the schema without changing it, so its field names and allowed values are a cross-epic contract.

## Non-goals

- The agent, the MCP tools, evals and any user interface.

## Success signal

On a fresh checkout, `uv run python load_seed.py` run twice leaves an `app.db` against which `get_ticket("T-1042")` returns customer `C-77`, and `get_customer_history("C-77")` returns Northwind, Enterprise, 2 open tickets. The schema accepts `{"category": "billing", "priority": "P2", "route": "billing-team", "rationale": "..."}` and rejects the same object with `"priority": "P5"`, with an error naming `priority`. It also rejects `billing` routed to `bug-team`.

## Assumptions

- The schema is a Pydantic model. `pydantic>=2.8` is already a dependency, and a Pydantic model works directly as LangChain structured output in Epic 2.
- `open_tickets` is stored as an INTEGER so the Enterprise rule's "3 or more" compares numerically. All other columns are TEXT. "Same columns as the CSV" is read as matching names, not types.
- Idempotent means replace, not append: each run rebuilds both tables from the CSVs.

## Open Questions

- Are fields beyond category, priority, route and rationale rejected?
- How is "one-sentence rationale" enforced: a non-empty string, or an actual single-sentence check?
- Which module holds the schema, so Epic 2 and Epic 3 import it from a known place?
