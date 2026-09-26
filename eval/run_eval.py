"""Evaluate the Epic 2 triage agent over eval/labelled_tickets.csv with MLflow.

Usage: uv run python eval/run_eval.py

Drives `agent.triage` through `mlflow.genai.evaluate` with four code scorers
(valid_schema, category_match, priority_match, tool_order) and logs one MLflow run
to the `triage-agent` experiment at sqlite:///mlflow.db. Escalations are approved
automatically, so the run never waits for a person.
"""

import asyncio
import csv
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mlflow  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from mlflow.genai.scorers import scorer  # noqa: E402

import agent  # noqa: E402
from triage.schema import TriageDecision  # noqa: E402

LABELS_PATH = ROOT / "eval" / "labelled_tickets.csv"
TRACKING_URI = f"sqlite:///{ROOT / 'mlflow.db'}"
APP_DB = ROOT / "app.db"
EXPERIMENT = "triage-agent"

logger = logging.getLogger(__name__)


# --- data ----------------------------------------------------------------------


def build_dataset(path: Path = LABELS_PATH) -> list[dict]:
    """One eval row per labelled ticket: inputs {ticket_id}, expectations from the labels."""
    with open(path, newline="", encoding="utf-8") as f:
        return [
            {
                "inputs": {"ticket_id": row["ticket_id"]},
                "expectations": {
                    "expected_category": row["expected_category"],
                    "expected_priority": row["expected_priority"],
                    "judge_notes": row["judge_notes"],
                },
            }
            for row in csv.DictReader(f)
        ]


# --- escalation approver ---------------------------------------------------------


class AutoApprover:
    """Approves every escalation request and counts the approvals."""

    def __init__(self) -> None:
        self.count = 0

    def __call__(self, request) -> bool:
        self.count += 1
        return True


auto_approver = AutoApprover()


# --- predict -----------------------------------------------------------------------


@mlflow.trace(name="triage", span_type="AGENT")
def predict(ticket_id: str) -> dict | None:
    """Triage one ticket inside a single trace. On an agent error, log it on the trace and return None."""
    try:
        return asyncio.run(agent.triage(ticket_id, approve=auto_approver))
    except Exception as exc:  # the eval carries on past a failing ticket
        logger.warning("Ticket %s failed: %s", ticket_id, exc)
        span = mlflow.get_current_active_span()
        if span is not None:
            span.record_exception(exc)
        return None


# --- scorers -------------------------------------------------------------------------


@scorer
def valid_schema(outputs) -> int:
    try:
        TriageDecision.model_validate(outputs)
    except ValidationError:
        return 0
    return 1


@scorer
def category_match(outputs, expectations) -> int:
    if not isinstance(outputs, dict):
        return 0
    return int(outputs.get("category") == expectations.get("expected_category"))


@scorer
def priority_match(outputs, expectations) -> int:
    if not isinstance(outputs, dict):
        return 0
    return int(outputs.get("priority") == expectations.get("expected_priority"))


@scorer
def tool_order(trace) -> int:
    """1 when a get_ticket span starts before the get_customer_history span."""
    if trace is None:
        return 0
    spans = trace.data.spans
    tickets = [s.start_time_ns for s in spans if s.name == "get_ticket"]
    histories = [s.start_time_ns for s in spans if s.name == "get_customer_history"]
    if not tickets or not histories:
        return 0
    return int(min(tickets) < min(histories))


SCORERS = [valid_schema, category_match, priority_match, tool_order]


# --- main ------------------------------------------------------------------------------


def preflight() -> None:
    """Fail fast, before any ticket runs, when the provider key or app.db is missing."""
    try:
        agent.build_model()
    except agent.AgentConfigError as exc:
        raise SystemExit(f"Cannot run the eval: {exc}")
    if not APP_DB.exists():
        raise SystemExit(f"Cannot run the eval: {APP_DB} is missing. Run `uv run python load_seed.py` first.")


def main():
    load_dotenv()
    preflight()
    # One ticket at a time keeps the run under free-tier rate limits.
    os.environ.setdefault("MLFLOW_GENAI_EVAL_MAX_WORKERS", "1")
    # predict is already traced; skip MLflow's extra validation call so no ticket runs twice.
    os.environ.setdefault("MLFLOW_GENAI_EVAL_SKIP_TRACE_VALIDATION", "true")

    mlflow.set_tracking_uri(TRACKING_URI)
    mlflow.set_experiment(EXPERIMENT)
    mlflow.langchain.autolog()

    result = mlflow.genai.evaluate(data=build_dataset(), scorers=SCORERS, predict_fn=predict)
    print(f"MLflow run: {result.run_id}")
    return result


if __name__ == "__main__":
    main()
