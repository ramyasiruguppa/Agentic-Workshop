- source_spec: `_bmad-output/specs/spec-epic-2/stories/1-the-triage-agent.md`
  summary: The agent does not check in code that `get_ticket` succeeded or that `get_customer_history` got the returned customer_id, so a model could triage an unknown ticket.
  evidence: Unverified (medium if true). Groq on T-9999 refused and failed loudly; Gemini could not be tested (503, then free-tier quota). Settle it by running `uv run python run_agent.py T-9999` on Gemini and checking that no decision is printed.
