"""Offline tests for agent.py: no network, no API keys, no model calls."""

import asyncio

import pytest
from langchain.agents.structured_output import StructuredOutputValidationError
from langchain_core.messages import AIMessage
from pydantic import ValidationError

import agent
from triage.schema import TriageDecision, TriageValidationError

FAKE_KEY = "test-key-not-real-123"

DECISION = TriageDecision(
    category="billing", priority="P2", route="billing-team", rationale="Double charge puts money at stake (P2)."
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("PROVIDER", "MODEL", "GEMINI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(var, raising=False)


# --- provider switch ---------------------------------------------------------


def test_default_provider_is_gemini(monkeypatch):
    from langchain_google_genai import ChatGoogleGenerativeAI

    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    model = agent.build_model()
    assert isinstance(model, ChatGoogleGenerativeAI)
    assert model.model.removeprefix("models/") == "gemini-3.8-flash"


def test_gemini_explicit_with_model_override(monkeypatch):
    from langchain_google_genai import ChatGoogleGenerativeAI

    monkeypatch.setenv("PROVIDER", "gemini")
    monkeypatch.setenv("MODEL", "gemini-other")
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    model = agent.build_model()
    assert isinstance(model, ChatGoogleGenerativeAI)
    assert model.model.removeprefix("models/") == "gemini-other"


def test_groq_provider_default_model(monkeypatch):
    from langchain_groq import ChatGroq

    monkeypatch.setenv("PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", FAKE_KEY)
    model = agent.build_model()
    assert isinstance(model, ChatGroq)
    assert model.model_name == "openai/gpt-oss-120b"


def test_groq_model_override(monkeypatch):
    monkeypatch.setenv("PROVIDER", "groq")
    monkeypatch.setenv("MODEL", "llama-x")
    monkeypatch.setenv("GROQ_API_KEY", FAKE_KEY)
    assert agent.build_model().model_name == "llama-x"


def test_bad_provider_names_provider_and_allowed_values(monkeypatch):
    monkeypatch.setenv("PROVIDER", "openai")
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    with pytest.raises(agent.AgentConfigError) as exc_info:
        agent.build_model()
    msg = str(exc_info.value)
    assert "PROVIDER" in msg and "gemini" in msg and "groq" in msg
    assert FAKE_KEY not in msg


def test_missing_gemini_key_names_variable():
    with pytest.raises(agent.AgentConfigError, match="GEMINI_API_KEY"):
        agent.build_model()


def test_missing_groq_key_names_variable(monkeypatch):
    monkeypatch.setenv("PROVIDER", "groq")
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    with pytest.raises(agent.AgentConfigError, match="GROQ_API_KEY") as exc_info:
        agent.build_model()
    assert FAKE_KEY not in str(exc_info.value)


def test_bad_provider_stops_before_tools_or_model(monkeypatch):
    monkeypatch.setenv("PROVIDER", "openai")

    async def boom():
        raise AssertionError("tools must not load")

    monkeypatch.setattr(agent, "load_tools", boom)
    with pytest.raises(agent.AgentConfigError):
        asyncio.run(agent.triage("T-1042"))


# --- system prompt -----------------------------------------------------------


def test_system_prompt_contains_policy_and_instructions():
    prompt = agent.build_system_prompt()
    policy = (agent.ROOT / "TRIAGE_POLICY.md").read_text(encoding="utf-8").strip()
    assert policy in prompt
    assert prompt.index("get_ticket") < prompt.index("get_customer_history", prompt.index("## How to work"))
    assert "customer_id" in prompt
    assert "untrusted" in prompt


# --- retry-once rule ---------------------------------------------------------


def _validation_error() -> StructuredOutputValidationError:
    try:
        TriageDecision(category="billing", priority="P9", route="billing-team", rationale="x")
    except ValidationError as exc:
        return StructuredOutputValidationError("TriageDecision", exc, AIMessage(content=""))
    raise AssertionError("expected a validation error")


class StubAgent:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def ainvoke(self, payload, config=None):
        self.calls.append(payload)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def stub(monkeypatch):
    def install(outcomes):
        stub_agent = StubAgent(outcomes)
        monkeypatch.setattr(agent, "build_model", lambda: object())

        async def fake_tools():
            return []

        monkeypatch.setattr(agent, "load_tools", fake_tools)
        monkeypatch.setattr(agent, "build_agent", lambda model, tools: stub_agent)
        return stub_agent

    return install


def test_happy_path_returns_dict_and_sends_only_ticket_id(stub):
    stub_agent = stub([{"structured_response": DECISION}])
    result = asyncio.run(agent.triage("T-1042"))
    assert result == DECISION.model_dump()
    assert len(stub_agent.calls) == 1
    assert stub_agent.calls[0] == {"messages": [{"role": "user", "content": "T-1042"}]}


def test_one_bad_output_then_retry_passes(stub):
    stub_agent = stub([_validation_error(), {"structured_response": DECISION}])
    assert asyncio.run(agent.triage("T-1042")) == DECISION.model_dump()
    assert len(stub_agent.calls) == 2


def test_missing_structured_response_counts_as_bad_output(stub):
    stub_agent = stub([{"messages": []}, {"structured_response": DECISION}])
    assert asyncio.run(agent.triage("T-1042")) == DECISION.model_dump()
    assert len(stub_agent.calls) == 2


def test_two_bad_outputs_raise_triage_validation_error(stub):
    stub_agent = stub([_validation_error(), _validation_error()])
    with pytest.raises(TriageValidationError) as exc_info:
        asyncio.run(agent.triage("T-1042"))
    assert "T-1042" in str(exc_info.value)
    assert "priority" in str(exc_info.value)
    assert len(stub_agent.calls) == 2


# --- MCP tools ---------------------------------------------------------------


def test_mcp_tools_load():
    tools = asyncio.run(agent.load_tools())
    assert {t.name for t in tools} == {"get_ticket", "get_customer_history"}


def test_whitespace_provider_selects_gemini(monkeypatch):
    from langchain_google_genai import ChatGoogleGenerativeAI

    monkeypatch.setenv("PROVIDER", "  ")
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    assert isinstance(agent.build_model(), ChatGoogleGenerativeAI)


# --- real build_agent with a fake chat model ---------------------------------

from langchain.agents.structured_output import StructuredOutputError  # noqa: E402
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel  # noqa: E402
from langchain_core.messages import SystemMessage  # noqa: E402


class ToolFakeChatModel(GenericFakeChatModel):
    seen: list = []

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def _fake_model(args: dict) -> ToolFakeChatModel:
    call = {"name": "TriageDecision", "args": args, "id": "call-1", "type": "tool_call"}
    return ToolFakeChatModel(messages=iter([AIMessage(content="", tool_calls=[call])]), seen=[])


def _ticket():
    return {"messages": [{"role": "user", "content": "T-1042"}]}


def _thread():
    # build_agent has a checkpointer (needed for escalation interrupts), so it needs a thread.
    return {"configurable": {"thread_id": "test"}}


def test_build_agent_invalid_output_raises():
    model = _fake_model({**DECISION.model_dump(), "priority": "P9"})
    with pytest.raises(StructuredOutputError):
        asyncio.run(agent.build_agent(model, []).ainvoke(_ticket(), _thread()))


def test_build_agent_valid_output_and_system_prompt():
    model = _fake_model(DECISION.model_dump())
    result = asyncio.run(agent.build_agent(model, []).ainvoke(_ticket(), _thread()))
    assert result["structured_response"] == DECISION

    policy = (agent.ROOT / "TRIAGE_POLICY.md").read_text(encoding="utf-8").strip()
    system = [m for m in model.seen[0] if isinstance(m, SystemMessage)]
    assert len(system) == 1
    assert policy in system[0].content


# --- human-gated escalation (story 2.2) --------------------------------------

import io  # noqa: E402

from langchain_core.messages import ToolMessage  # noqa: E402

P1_DECISION = TriageDecision(
    category="bug",
    priority="P1",
    route="bug-team",
    rationale="Outage for an Enterprise customer is P1 and escalated under the escalation rule.",
)
REASON = "P1 outage for an Enterprise customer."


def _escalating_model(*, then=None) -> ToolFakeChatModel:
    escalate = {
        "name": "escalate_to_human",
        "args": {"ticket_id": "T-1044", "reason": REASON},
        "id": "call-esc",
        "type": "tool_call",
    }
    final = {"name": "TriageDecision", "args": then or P1_DECISION.model_dump(), "id": "call-dec", "type": "tool_call"}
    return ToolFakeChatModel(
        messages=iter([AIMessage(content="", tool_calls=[escalate]), AIMessage(content="", tool_calls=[final])]),
        seen=[],
    )


@pytest.fixture
def escalation(monkeypatch):
    """Run the real build_agent on a fake model; record every real escalation."""
    escalated = []

    def install(model, stdin_text=None):
        monkeypatch.setattr(agent, "build_model", lambda: model)

        async def fake_tools():
            return []

        monkeypatch.setattr(agent, "load_tools", fake_tools)
        real = agent._hand_to_person

        def record(ticket_id, reason):
            escalated.append((ticket_id, reason))
            return real(ticket_id, reason)

        monkeypatch.setattr(agent, "_hand_to_person", record)
        if stdin_text is None:
            monkeypatch.setattr("sys.stdin", _NoStdin())
        else:
            monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
        return escalated

    return install


class _NoStdin(io.StringIO):
    def readline(self, *args):
        raise AssertionError("stdin must not be read")

    read = readline


def _tool_messages(model):
    return [m for m in model.seen[-1] if isinstance(m, ToolMessage) and m.name == "escalate_to_human"]


def test_escalation_approved_on_yes(escalation, capsys):
    model = _escalating_model()
    escalated = escalation(model, "yes\n")
    result = asyncio.run(agent.triage("T-1044"))

    assert escalated == [("T-1044", REASON)]
    assert result == P1_DECISION.model_dump()
    assert set(result) == {"category", "priority", "route", "rationale"}
    out = capsys.readouterr().out
    assert "T-1044" in out and REASON in out
    assert agent.APPROVED_LINE in out and agent.DECLINED_LINE not in out
    # Resumed on the same thread: the second model call sees the first turn and the tool result.
    assert len(model.seen) == 2
    assert "escalated to a person" in _tool_messages(model)[0].content


def test_escalation_rejected_on_no(escalation, capsys):
    model = _escalating_model()
    escalated = escalation(model, "no\n")
    result = asyncio.run(agent.triage("T-1044"))

    assert escalated == []
    assert result == P1_DECISION.model_dump()
    out = capsys.readouterr().out
    assert agent.DECLINED_LINE in out and agent.APPROVED_LINE not in out
    rejection = _tool_messages(model)
    assert len(rejection) == 1 and "declined" in rejection[0].content


def test_unclear_answer_reasks_once_then_approves(escalation, capsys):
    escalated = escalation(_escalating_model(), "maybe\ny\n")
    asyncio.run(agent.triage("T-1044"))
    out = capsys.readouterr().out
    assert escalated == [("T-1044", REASON)]
    assert out.count("Please answer yes or no.") == 1
    assert agent.APPROVED_LINE in out


def test_eof_rejects(escalation, capsys):
    escalated = escalation(_escalating_model(), "")
    result = asyncio.run(agent.triage("T-1044"))
    assert escalated == []
    assert result == P1_DECISION.model_dump()
    assert agent.DECLINED_LINE in capsys.readouterr().out


def test_no_escalation_no_prompt(escalation, capsys):
    escalated = escalation(_fake_model(DECISION.model_dump()))  # stdin raises if read
    assert asyncio.run(agent.triage("T-1042")) == DECISION.model_dump()

    def never(request):
        raise AssertionError("approver must not be asked")

    escalation(_fake_model(DECISION.model_dump()))
    assert asyncio.run(agent.triage("T-1042", approve=never)) == DECISION.model_dump()
    assert escalated == []
    out = capsys.readouterr().out
    assert agent.APPROVED_LINE not in out and agent.DECLINED_LINE not in out


def test_custom_approver_skips_terminal(escalation, capsys):
    escalated = escalation(_escalating_model())  # stdin raises if read
    seen = []

    def approve(request):
        seen.append(request)
        return True

    assert asyncio.run(agent.triage("T-1044", approve=approve)) == P1_DECISION.model_dump()
    assert escalated == [("T-1044", REASON)]
    assert seen[0]["action_requests"][0]["name"] == "escalate_to_human"
    assert seen[0]["action_requests"][0]["args"] == {"ticket_id": "T-1044", "reason": REASON}
    assert agent.APPROVED_LINE in capsys.readouterr().out


def test_lambda_approver(escalation):
    escalated = escalation(_escalating_model())
    asyncio.run(agent.triage("T-1044", approve=lambda req: True))
    assert escalated == [("T-1044", REASON)]


@pytest.mark.parametrize("answer", [False, None, "maybe", "", 1, "yes please"])
def test_custom_approver_non_yes_rejects(escalation, answer):
    escalated = escalation(_escalating_model())
    asyncio.run(agent.triage("T-1044", approve=lambda req: answer))
    assert escalated == []


@pytest.mark.parametrize("text,expected", [("YES\n", True), ("Y\n", True), (" y \n", True), ("N\n", False), ("No\n", False)])
def test_ask_terminal_answers(monkeypatch, text, expected):
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    request = {"action_requests": [{"name": "escalate_to_human", "args": {"ticket_id": "T-1", "reason": "r"}}]}
    assert agent.ask_terminal(request) is expected


def _esc(call_id="call-esc", ticket_id="T-1044"):
    return {
        "name": "escalate_to_human",
        "args": {"ticket_id": ticket_id, "reason": REASON},
        "id": call_id,
        "type": "tool_call",
    }


def _final(call_id, args=None):
    return {"name": "TriageDecision", "args": args or P1_DECISION.model_dump(), "id": call_id, "type": "tool_call"}


def _script(*turns) -> ToolFakeChatModel:
    """A fake model that answers each call with the next list of tool calls."""
    return ToolFakeChatModel(messages=iter([AIMessage(content="", tool_calls=list(t)) for t in turns]), seen=[])


BAD = {**P1_DECISION.model_dump(), "priority": "P9"}


@pytest.mark.parametrize("approves", [False, True])
def test_retry_wraps_the_resumable_run(escalation, approves):
    # First run escalates then emits an invalid decision; the retry runs again on a fresh thread
    # and its escalation is rejected without asking again.
    model = _script([_esc()], [_final("call-bad", BAD)], [_esc("call-esc-2")], [_final("call-ok")])
    escalated = escalation(model)
    asks = []
    result = asyncio.run(agent.triage("T-1044", approve=lambda req: asks.append(req) or approves))
    assert result == P1_DECISION.model_dump()
    assert len(asks) == 1
    assert escalated == ([("T-1044", REASON)] if approves else [])

    retry_first_call = model.seen[2]
    assert [type(m).__name__ for m in retry_first_call] == ["SystemMessage", "HumanMessage"]
    assert retry_first_call[1].content == "T-1044"
    outcome = "approved" if approves else "declined"
    assert f"already {outcome}" in _tool_messages(model)[-1].content


def test_recall_after_no_asks_once(escalation, capsys):
    model = _script([_esc()], [_esc("call-esc-2")], [_esc("call-esc-3")], [_final("call-ok")])
    escalated = escalation(model)
    asks = []
    result = asyncio.run(agent.triage("T-1044", approve=lambda req: asks.append(req) or False))
    assert result == P1_DECISION.model_dump()
    assert len(asks) == 1
    assert escalated == []
    out = capsys.readouterr().out
    assert out.count(agent.DECLINED_LINE) == 1 and agent.APPROVED_LINE not in out
    assert "already declined" in _tool_messages(model)[-1].content


def test_mismatched_ticket_id_never_reaches_approver(escalation, capsys):
    model = _script([_esc(ticket_id="T-9999")], [_final("call-ok")])
    escalated = escalation(model)

    def never(request):
        raise AssertionError("approver must not be asked")

    assert asyncio.run(agent.triage("T-1044", approve=never)) == P1_DECISION.model_dump()
    assert escalated == []
    rejection = _tool_messages(model)[-1].content
    assert "T-1044" in rejection
    out = capsys.readouterr().out
    assert agent.APPROVED_LINE not in out and agent.DECLINED_LINE not in out


def test_mismatch_does_not_spend_the_one_question(escalation):
    model = _script([_esc(ticket_id="T-9999")], [_esc("call-esc-2")], [_final("call-ok")])
    escalated = escalation(model)
    asks = []
    asyncio.run(agent.triage("T-1044", approve=lambda req: asks.append(req) or True))
    assert len(asks) == 1
    assert escalated == [("T-1044", REASON)]


async def _async_yes(request):
    return True


class _AsyncCallApprover:
    async def __call__(self, request):
        return True


@pytest.mark.parametrize("approver", [_async_yes, _AsyncCallApprover()], ids=["async-def", "async-call"])
def test_async_approvers_approve(escalation, approver):
    escalated = escalation(_escalating_model())
    assert asyncio.run(agent.triage("T-1044", approve=approver)) == P1_DECISION.model_dump()
    assert escalated == [("T-1044", REASON)]


@pytest.mark.parametrize("answer", ["yes", "Y", " y "])
def test_custom_approver_string_yes_approves(escalation, answer):
    escalated = escalation(_escalating_model())
    asyncio.run(agent.triage("T-1044", approve=lambda req: answer))
    assert escalated == [("T-1044", REASON)]


def test_two_escalations_in_one_message_share_one_approval(escalation, capsys):
    model = _script([_esc("call-a"), _esc("call-b")], [_final("call-ok")])
    escalated = escalation(model)
    asks = []
    result = asyncio.run(agent.triage("T-1044", approve=lambda req: asks.append(req) or True))
    assert result == P1_DECISION.model_dump()
    assert len(asks) == 1 and len(asks[0]["action_requests"]) == 2
    assert escalated == [("T-1044", REASON), ("T-1044", REASON)]
    assert capsys.readouterr().out.count(agent.APPROVED_LINE) == 1


def test_escalate_tool_not_in_mcp_server():
    tools = asyncio.run(agent.load_tools())
    assert "escalate_to_human" not in {t.name for t in tools}
