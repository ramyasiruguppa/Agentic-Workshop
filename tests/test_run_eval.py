"""Offline tests for eval/run_eval.py: no network, no API keys, no model calls."""

import importlib.util
from pathlib import Path
import json
import os
from types import SimpleNamespace

import mlflow
import mlflow.groq
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from mlflow.entities import Feedback, TraceState

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
    assert [s.name for s in run_eval.SCORERS] == [
        "valid_schema",
        "category_match",
        "priority_match",
        "tool_order",
        "rationale_judge",
    ]


# --- rationale_judge ----------------------------------------------------------------------

JUDGE_INPUTS = {"ticket_id": "T-1042"}


class BoomChatModel(BaseChatModel):
    """A judge model whose call fails, like Groq after ChatGroq's retries."""

    @property
    def _llm_type(self) -> str:
        return "boom"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise RuntimeError("429 Too Many Requests")


def use_judge(monkeypatch, model):
    monkeypatch.setattr(run_eval, "_judge_model", lambda: model)
    return model


def judge(outputs=GOOD):
    return run_eval.rationale_judge(inputs=JUDGE_INPUTS, outputs=outputs, expectations=EXPECT)


def test_judge_pass(monkeypatch):
    use_judge(monkeypatch, FakeListChatModel(responses=["pass: double charge treated as a P2 money issue."]))
    fb = judge()
    assert (fb.name, fb.value, fb.rationale) == ("rationale_judge", "pass", "double charge treated as a P2 money issue.")
    assert fb.error is None


def test_judge_fail(monkeypatch):
    use_judge(monkeypatch, FakeListChatModel(responses=["FAIL - ignores the money at stake."]))
    fb = judge()
    assert (fb.value, fb.rationale) == ("fail", "ignores the money at stake.")


def test_judge_none_output_skips_groq(monkeypatch):
    def no_call():
        pytest.fail("judge model built or called for a None output")

    monkeypatch.setattr(run_eval, "_judge_model", no_call)
    fb = judge(outputs=None)
    assert (fb.value, fb.rationale) == ("fail", "no decision to judge")


def test_judge_unparseable_reply(monkeypatch):
    use_judge(monkeypatch, FakeListChatModel(responses=["I think it is mostly fine."]))
    fb = judge()
    assert (fb.value, fb.rationale) == ("fail", "unparseable judge reply")


def test_judge_error_leaves_ticket_unjudged(monkeypatch):
    use_judge(monkeypatch, BoomChatModel())
    fb = judge()
    assert fb.value is None
    assert fb.error is not None
    assert "429" in fb.error.error_message


def test_judge_prompt_holds_ticket_decision_and_notes(monkeypatch):
    seen = []

    def fake_ask(model, messages):
        seen.append(messages)
        return "pass: fine"

    monkeypatch.setattr(run_eval, "ask_judge", fake_ask)
    use_judge(monkeypatch, object())
    judge()
    prompt = seen[0][-1].content
    assert "I was charged twice this month" in prompt
    assert "Money at stake (P2)." in prompt
    assert EXPECT["judge_notes"] in prompt


def test_parse_verdict_variants():
    assert run_eval.parse_verdict("**PASS**: sound") == ("pass", "sound")
    assert run_eval.parse_verdict("\nfail \u2014 wrong priority\nextra") == ("fail", "wrong priority")
    assert run_eval.parse_verdict("passable") == ("fail", "unparseable judge reply")
    assert run_eval.parse_verdict("") == ("fail", "unparseable judge reply")


def test_judge_call_adds_no_trace(monkeypatch):
    mlflow.langchain.autolog()
    try:
        fake = FakeListChatModel(responses=["pass: ok"])
        fake.invoke("control call")  # proves autolog is live and would trace a normal call
        control = mlflow.get_last_active_trace_id()
        assert control is not None
        use_judge(monkeypatch, fake)
        assert judge().value == "pass"
        assert mlflow.get_last_active_trace_id() == control
        assert len(mlflow.search_traces(return_type="list", flush=True)) == 1
    finally:
        mlflow.langchain.autolog(disable=True)


class GeminiGuardEnviron(dict):
    """os.environ stand-in that fails the test if anything reads GEMINI_API_KEY."""

    def _check(self, key):
        if key == "GEMINI_API_KEY":
            pytest.fail("the judge read GEMINI_API_KEY")

    def __getitem__(self, key):
        self._check(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        self._check(key)
        return super().get(key, default)

    def __contains__(self, key):
        self._check(key)
        return super().__contains__(key)


def test_judge_builds_from_groq_key_without_gemini(monkeypatch):
    monkeypatch.delenv("PROVIDER", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("JUDGE_MODEL", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setattr(os, "environ", GeminiGuardEnviron(os.environ))
    model = run_eval.build_judge_model()
    assert model.model_name == "openai/gpt-oss-120b"
    assert model.groq_api_key.get_secret_value() == "test-groq-key"


def test_judge_ignores_gemini_key_and_provider(monkeypatch):
    monkeypatch.setenv("PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setenv("JUDGE_MODEL", "some/judge")
    monkeypatch.setattr(os, "environ", GeminiGuardEnviron(os.environ))
    model = run_eval.build_judge_model()
    assert model.model_name == "some/judge"
    assert model.groq_api_key.get_secret_value() == "test-groq-key"


# --- report ----------------------------------------------------------------------------------


def report_trace(tokens, judge_feedback):
    return SimpleNamespace(
        info=SimpleNamespace(
            token_usage=None if tokens is None else {"input_tokens": 1, "output_tokens": 1, "total_tokens": tokens},
            assessments=[Feedback(name="valid_schema", value=1), judge_feedback],
        )
    )


def test_build_report_from_fake_run(monkeypatch):
    traces = [
        report_trace(100, Feedback(name="rationale_judge", value="pass", rationale="ok")),
        report_trace(200, Feedback(name="rationale_judge", value="pass", rationale="ok")),
        report_trace(300, Feedback(name="rationale_judge", value="fail", rationale="no decision to judge")),
        report_trace(None, Feedback(name="rationale_judge", error=RuntimeError("503"))),
    ]
    monkeypatch.setattr(run_eval, "_run_traces", lambda run_id: traces if run_id == "r1" else pytest.fail(run_id))
    metrics = {"valid_schema/mean": 1.0, "category_match/mean": 0.75, "priority_match/mean": 0.5, "tool_order/mean": 1.0}
    approver = run_eval.AutoApprover()
    approver.count = 3
    report = run_eval.build_report(SimpleNamespace(run_id="r1", metrics=metrics), approver)
    assert report == {
        "run_id": "r1",
        "scorers": {
            "valid_schema": 1.0,
            "category_match": 0.75,
            "priority_match": 0.5,
            "tool_order": 1.0,
            "rationale_judge": pytest.approx(2 / 3),
        },
        "agent_total_tokens": 600,
        "escalations_auto_approved": 3,
        "rationale_judge_unjudged": 1,
    }


# --- main ------------------------------------------------------------------------------


@pytest.fixture
def main_env(monkeypatch, tmp_path):
    """Stub everything main() touches outside this module; return the evaluate call log."""
    calls = []
    monkeypatch.setattr(
        mlflow.genai, "evaluate", lambda **kw: calls.append(kw) or SimpleNamespace(run_id="r1", metrics={})
    )
    monkeypatch.setattr(run_eval, "_run_traces", lambda run_id: [])
    monkeypatch.setattr(run_eval, "REPORT_PATH", tmp_path / "latest_report.json")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setattr(mlflow.langchain, "autolog", lambda *a, **kw: None)
    monkeypatch.setattr(mlflow.groq, "autolog", lambda *a, **kw: calls.append(("groq.autolog", kw)))
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
    assert main_env[0] == ("groq.autolog", {"disable": True})  # judge calls must not become traces
    evaluate_calls = [c for c in main_env if isinstance(c, dict)]
    assert len(evaluate_calls) == 1
    kw = evaluate_calls[0]
    assert kw["predict_fn"] is run_eval.predict
    assert kw["scorers"] is run_eval.SCORERS
    assert len(kw["data"]) == 20


def test_main_prints_and_writes_report(monkeypatch, main_env, capsys):
    monkeypatch.setattr(run_eval, "preflight", lambda: None)
    fake = {
        "run_id": "r1",
        "scorers": {n: 1.0 for n in [*run_eval.CODE_SCORERS, "rationale_judge"]},
        "agent_total_tokens": 1234,
        "escalations_auto_approved": 3,
        "rationale_judge_unjudged": 0,
    }
    monkeypatch.setattr(run_eval, "build_report", lambda result, approver: fake)
    run_eval.main()
    assert json.loads(run_eval.REPORT_PATH.read_text()) == fake
    out = capsys.readouterr().out
    for text in ("r1", "rationale_judge", "1234", "Escalations auto-approved: 3", "unjudged:  0"):
        assert text in out


def test_main_exits_on_missing_groq_key(monkeypatch, main_env):
    monkeypatch.setattr(run_eval.agent, "build_model", lambda: object())
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="GROQ_API_KEY"):
        run_eval.main()
    assert main_env == []


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
