---
title: 'Seed loader'
type: 'feature'
created: '2026-09-26'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: ['{project-root}/_bmad-output/specs/spec-epic-1/SPEC.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `mcp/triage_server.py` reads `app.db`, but nothing creates it, so Epic 2's agent has no tickets or customers to look up (Epic 1 SPEC, CAP-2 and CAP-3).

**Approach:** Add `load_seed.py` at the repo root. `uv run python load_seed.py` reads `seed/tickets.csv` and `seed/customers.csv` with the `csv` module and writes them into `app.db` at the repo root. Table `tickets(ticket_id, customer_id, created_at, text)` and table `customers(customer_id, name, plan, open_tickets)` have the CSV header names; `open_tickets` is INTEGER and every other column is TEXT. Each run drops and recreates both tables in one transaction, so a second run leaves identical contents (24 tickets, 20 customers) with no duplicates. Paths resolve from the script's own location, not the working directory. The seed files are only read. No network, no API keys, no new dependency. `mcp/triage_server.py` and `seed/` stay unchanged. Add `tests/test_load_seed.py` covering row counts, column names, the INTEGER type of `open_tickets`, a run-twice identical result, and the MCP server's `get_ticket("T-1042")` → customer `C-77` and `get_customer_history("C-77")` → Northwind, Enterprise, 2 open tickets; tests write to a temporary database, never the real `app.db`.

</frozen-after-approval>

## Implementation Notes

- Added `load_seed.py` (repo root) with `load(db_path, seed_dir)` so tests target a temp database; the `__main__` path writes `app.db` beside the script.
- The CSV header is checked against the expected columns before any table is dropped, so a malformed seed file fails without touching the existing `app.db`.
- DROP, CREATE and INSERT for both tables run in one transaction.
- `tests/test_load_seed.py` loads `mcp/triage_server.py` by file path (the repo's `mcp/` folder shares its name with the installed `mcp` package) and points its `DB_PATH` at the temp database.
- Review fix: the connection now uses `autocommit=False`; with Python's default mode the DROP/CREATE statements committed on their own, so a failed rebuild left empty tables. `test_failed_load_leaves_existing_db_untouched` fails without the fix.
- Added tests beyond the five required areas: rows match the CSVs field by field, and `DB_PATH`/`SEED_DIR` resolve from the script's location.
- Verified: `uv run pytest` → 30 passed; `uv run python load_seed.py` run twice gives identical `sqlite3 .dump` hashes; `app.db` is git-ignored.

## Review Triage Log

- high (patched): DROP/CREATE ran outside the transaction under the default sqlite3 mode, so a failure mid-rebuild left empty tables. Reproduced; fixed with `autocommit=False`.
- medium (patched): no test proved a failed load keeps the old data. Added a test that fails the customers insert after tickets was rebuilt.
- low (patched): the quoted-text test only checked that some text contained a comma. Replaced with a field-by-field comparison against the CSVs.
- low (patched): nothing checked that paths resolve from the script's location. Added assertions on `DB_PATH` and `SEED_DIR`.
- low (patched): the test helper left connections open. Now wrapped in `contextlib.closing`.
- low (rejected): short or extra fields in data rows go undetected. `seed/` is read-only and clean; guarding adds branches for a state the repo can't reach.
- low (rejected): the run-twice test can't tell replace from no-op. An append would double the counts and fail; stale-row cases need seed files the repo doesn't have.
- low (rejected): counts are hard-coded as 24/20. The epic spec states those numbers, and the field-by-field test now derives the rows from the CSVs.
- low (rejected): no primary keys. The spec fixes column names and types only; the seed has no duplicate IDs.
- low (rejected): a UTF-8 BOM would break the header check. The seed has no BOM and is read-only.
- false: the frontmatter was stale. The status is set when the review finishes, as it is now.
- low (rejected): the notes didn't mention the extra test. Fixed with a line added to the notes.
- deferred: `mcp/triage_server.py` leaks connections (pre-existing, read-only file); see `_bmad-output/implementation-artifacts/deferred-work.md`.
