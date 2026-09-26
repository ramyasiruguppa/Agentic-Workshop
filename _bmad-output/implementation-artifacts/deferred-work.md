- source_spec: `_bmad-output/specs/spec-epic-2/stories/1-the-triage-agent.md`
  summary: The agent does not check in code that `get_ticket` succeeded or that `get_customer_history` got the returned customer_id, so a model could triage an unknown ticket.
  evidence: Unverified (medium if true). Groq on T-9999 refused and failed loudly; Gemini could not be tested (503, then free-tier quota). Settle it by running `uv run python run_agent.py T-9999` on Gemini and checking that no decision is printed.
- source_spec: `_bmad-output/specs/spec-epic-2/stories/2-human-gated-escalation.md`
  summary: Ctrl+C at the escalation prompt may hang, because `input()` runs in a worker thread that `asyncio.run` waits on at shutdown.
  evidence: Unverified (medium if true). Settle it by running `PROVIDER=groq uv run python run_agent.py T-1044`, pressing Ctrl+C at the prompt, and checking whether the process exits at once.
- source_spec: `_bmad-output/specs/spec-epic-2/stories/2-human-gated-escalation.md`
  summary: MLflow's LangChain tracer logs `on_interrupt`/`on_resume` AttributeErrors on every escalation pause and resume.
  evidence: Seen on every live escalation run; the installed MLflow has no handlers for LangGraph interrupt events. Traces are still complete. Fix by upgrading MLflow or filtering the log in `run_agent.py` (read-only in Epic 2).
- source_spec: `_bmad-output/specs/spec-epic-3/stories/1-eval-run-and-code-scorers.md`
  summary: The eval does not retry rate-limit (429) errors, so free-tier limits show up as zero scores.
  evidence: Live Groq run e8285a04…: T-1050 and T-1058 failed on Groq's 8000 tokens-per-minute limit even with one worker. A backoff-and-retry in `predict`, or a count of infrastructure failures in story 2's report, would separate agent quality from quota.
