# Epic 1: triage data and schema

## Done when

- Every triage decision is a JSON object with a category (billing, bug, access, performance or how-to), a priority (P1 to P4), a route (billing-team, bug-team, access-team, performance-team or how-to-team) and a one-sentence rationale. The route must be the one `TRIAGE_POLICY.md` pairs with the category. Anything else is rejected with a clear error.
- One command, `uv run python load_seed.py`, loads `seed/tickets.csv` and `seed/customers.csv` into a local SQLite file, `app.db`, as the tables `tickets` and `customers` with the same columns as the CSV files. Running it twice gives the same database.

## Must not change

- Python 3.12 or newer, with uv.
- The files in `seed/` are read-only.
- No network calls and no API keys in this epic.
- `mcp/triage_server.py` already reads `app.db`; its table and column names must keep working.

## Out of scope

- The agent, the MCP tools, evals and any user interface.
