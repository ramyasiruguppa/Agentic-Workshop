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

    async def ainvoke(self, payload):
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


def test_build_agent_invalid_output_raises():
    model = _fake_model({**DECISION.model_dump(), "priority": "P9"})
    with pytest.raises(StructuredOutputError):
        asyncio.run(agent.build_agent(model, []).ainvoke(_ticket()))


def test_build_agent_valid_output_and_system_prompt():
    model = _fake_model(DECISION.model_dump())
    result = asyncio.run(agent.build_agent(model, []).ainvoke(_ticket()))
    assert result["structured_response"] == DECISION

    policy = (agent.ROOT / "TRIAGE_POLICY.md").read_text(encoding="utf-8").strip()
    system = [m for m in model.seen[0] if isinstance(m, SystemMessage)]
    assert len(system) == 1
    assert policy in system[0].content
