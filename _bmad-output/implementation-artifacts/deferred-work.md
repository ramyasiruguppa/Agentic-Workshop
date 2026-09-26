- source_spec: `_bmad-output/specs/spec-epic-1/stories/2-seed-loader.md`
  summary: `mcp/triage_server.py` `_query` never closes its SQLite connections (`with sqlite3.connect(...)` only commits or rolls back).
  evidence: `uv run pytest -W error::ResourceWarning tests/test_load_seed.py -k mcp` reports 3 "unclosed database" warnings, one per query; the file is read-only in Epic 1 and Epic 2, so fixing it needs a spec change.
