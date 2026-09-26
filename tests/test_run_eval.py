"""Offline tests for eval/run_eval.py: no network, no API keys, no model calls."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import mlflow
import pytest
from mlflow.entities import TraceState

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location("run_eval", ROOT / "eval" / "run_eval.py")
run_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_eval)

GOOD = {"category": "billing", "priority": "P2", "route": "billing-team", "rationale": "Money at stake (P2)."}
EXPECT = {"expected_category": "billing", "expected_priority": "P2", "judge_notes": "n/a"}


@pytest.fixture(autouse=True)
def local_tracking(tmp_path):
    """Keep any traces in a throwaway local store, never the repo's mlflow.db."""
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    yield
    mlflow.set_tracking_uri(None)


def score(scorer_obj, **kwargs):
    return scorer_obj(**kwargs)


# --- dataset -----------------------------------------------------------------------


def test_dataset_from_real_csv():
    rows = run_eval.build_dataset()
    assert len(rows) == 20
    assert rows[0]["inputs"] == {"ticket_id": "T-1042"}
    assert rows[0]["expectations"]["expected_category"] == "billing"
    assert rows[0]["expectations"]["expected_priority"] == "P2"
    assert "judge_notes" in rows[0]["expectations"]
    for row in rows:
        assert set(row["inputs"]) == {"ticket_id"}
        assert {"expected_category", "expected_priority"} <= set(row["expectations"])


# --- output scorers ------------------------------------------------------------------


def test_correct_decision_scores_all_ones():
    assert score(run_eval.valid_schema, outputs=GOOD) == 1
    assert score(run_eval.category_match, outputs=GOOD, expectations=EXPECT) == 1
    assert score(run_eval.priority_match, outputs=GOOD, expectations=EXPECT) == 1


def test_wrong_priority_only_fails_priority():
    out = {**GOOD, "priority": "P3"}
    assert score(run_eval.priority_match, outputs=out, expectations=EXPECT) == 0
    assert score(run_eval.valid_schema, outputs=out) == 1
    assert score(run_eval.category_match, outputs=out, expectations=EXPECT) == 1


def test_invalid_category_fails_schema():
    out = {**GOOD, "category": "sales"}
    assert score(run_eval.valid_schema, outputs=out) == 0


def test_none_output_scores_zero_without_raising():
    assert score(run_eval.valid_schema, outputs=None) == 0
    assert score(run_eval.category_match, outputs=None, expectations=EXPECT) == 0
    assert score(run_eval.priority_match, outputs=None, expectations=EXPECT) == 0


# --- tool_order ----------------------------------------------------------------------


def fake_trace(*spans):
    return SimpleNamespace(
        data=SimpleNamespace(spans=[SimpleNamespace(name=n, start_time_ns=t) for n, t in spans])
    )


def test_tool_order_right():
    trace = fake_trace(("triage", 0), ("get_ticket", 10), ("get_customer_history", 20))
    assert score(run_eval.tool_order, trace=trace) == 1


def test_tool_order_reversed():
    trace = fake_trace(("get_customer_history", 10), ("get_ticket", 20))
    assert score(run_eval.tool_order, trace=trace) == 0


def test_tool_order_missing_history():
    assert score(run_eval.tool_order, trace=fake_trace(("get_ticket", 10))) == 0
    assert score(run_eval.tool_order, trace=None) == 0


def test_tool_order_on_real_trace():
    @mlflow.trace(name="get_ticket", span_type="TOOL")
    def get_ticket():
        return "t"

    @mlflow.trace(name="get_customer_history", span_type="TOOL")
    def get_customer_history():
        return "h"

    @mlflow.trace(name="triage")
    def run():
        get_ticket()
        get_customer_history()

    run()
    trace = mlflow.get_trace(mlflow.get_last_active_trace_id(), flush=True)
    assert score(run_eval.tool_order, trace=trace) == 1


# --- predict and approver ---------------------------------------------------------------


@pytest.fixture
def fresh_approver(monkeypatch):
    approver = run_eval.AutoApprover()
    monkeypatch.setattr(run_eval, "auto_approver", approver)
    return approver


def test_escalation_is_auto_approved_and_counted(monkeypatch, fresh_approver):
    async def fake_triage(ticket_id, approve):
        answer = approve({"action_requests": [{"args": {"ticket_id": ticket_id}}]})
        assert answer is True
        return {**GOOD, "category": "access", "priority": "P1", "route": "access-team"}

    monkeypatch.setattr(run_eval.agent, "triage", fake_triage)
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("prompted a person"))
    out = run_eval.predict("T-1044")
    assert out["priority"] == "P1"
    assert fresh_approver.count == 1


def test_agent_error_returns_none(monkeypatch, fresh_approver):
    async def boom(ticket_id, approve):
        raise RuntimeError("model down")

    monkeypatch.setattr(run_eval.agent, "triage", boom)
    assert run_eval.predict("T-1042") is None
    assert fresh_approver.count == 0
    trace = mlflow.get_trace(mlflow.get_last_active_trace_id(), flush=True)
    assert trace.info.state == TraceState.ERROR
    root = trace.data.spans[0]
    assert any(e.name == "exception" for e in root.events)


def test_scorer_list():
    assert [s.name for s in run_eval.SCORERS] == ["valid_schema", "category_match", "priority_match", "tool_order"]


# --- main ------------------------------------------------------------------------------


@pytest.fixture
def main_env(monkeypatch, tmp_path):
    """Stub everything main() touches outside this module; return the evaluate call log."""
    calls = []
    monkeypatch.setattr(mlflow.genai, "evaluate", lambda **kw: calls.append(kw) or SimpleNamespace(run_id="r1"))
    monkeypatch.setattr(mlflow.langchain, "autolog", lambda *a, **kw: None)
    monkeypatch.setattr(run_eval, "load_dotenv", lambda: None)
    monkeypatch.setattr(run_eval, "TRACKING_URI", f"sqlite:///{tmp_path / 'mlflow.db'}")
    monkeypatch.delenv("MLFLOW_GENAI_EVAL_MAX_WORKERS", raising=False)
    monkeypatch.delenv("MLFLOW_GENAI_EVAL_SKIP_TRACE_VALIDATION", raising=False)
    return calls


def test_main_configures_and_calls_evaluate(monkeypatch, main_env):
    monkeypatch.setattr(run_eval, "preflight", lambda: None)
    run_eval.main()
    assert run_eval.os.environ["MLFLOW_GENAI_EVAL_MAX_WORKERS"] == "1"
    assert run_eval.os.environ["MLFLOW_GENAI_EVAL_SKIP_TRACE_VALIDATION"] == "true"
    assert mlflow.get_experiment_by_name("triage-agent") is not None
    assert len(main_env) == 1
    kw = main_env[0]
    assert kw["predict_fn"] is run_eval.predict
    assert kw["scorers"] is run_eval.SCORERS
    assert len(kw["data"]) == 20


def test_main_keeps_preset_workers(monkeypatch, main_env):
    monkeypatch.setattr(run_eval, "preflight", lambda: None)
    monkeypatch.setenv("MLFLOW_GENAI_EVAL_MAX_WORKERS", "4")
    run_eval.main()
    assert run_eval.os.environ["MLFLOW_GENAI_EVAL_MAX_WORKERS"] == "4"


def test_main_exits_on_missing_key(monkeypatch, main_env):
    for var in ("PROVIDER", "MODEL", "GEMINI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(SystemExit, match="GEMINI_API_KEY"):
        run_eval.main()
    assert main_env == []


def test_main_exits_on_missing_app_db(monkeypatch, main_env, tmp_path):
    monkeypatch.setattr(run_eval.agent, "build_model", lambda: object())
    monkeypatch.setattr(run_eval, "APP_DB", tmp_path / "app.db")
    with pytest.raises(SystemExit, match="app.db"):
        run_eval.main()
    assert main_env == []
