import pytest
from pydantic import ValidationError

from triage.schema import TriageDecision, TriageValidationError

VALID = {
    "category": "billing",
    "priority": "P2",
    "route": "billing-team",
    "rationale": "Double charge puts money at stake.",
}


def error_fields(exc: ValidationError) -> set[str]:
    return {str(part) for err in exc.errors() for part in err["loc"]}


def assert_rejected(data: dict, field: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        TriageDecision.model_validate(data)
    fields = error_fields(exc_info.value)
    assert field in fields, f"expected error naming {field!r}, got {fields}"
    assert field in str(exc_info.value)


def test_valid():
    decision = TriageDecision.model_validate(VALID)
    assert decision.category == "billing"
    assert decision.priority == "P2"
    assert decision.route == "billing-team"
    assert decision.rationale == "Double charge puts money at stake."


def test_model_dump_has_exactly_four_fields():
    dumped = TriageDecision.model_validate(VALID).model_dump()
    assert isinstance(dumped, dict)
    assert set(dumped) == {"category", "priority", "route", "rationale"}


@pytest.mark.parametrize(
    ("category", "route"),
    [
        ("billing", "billing-team"),
        ("bug", "bug-team"),
        ("access", "access-team"),
        ("performance", "performance-team"),
        ("how-to", "how-to-team"),
    ],
)
def test_every_policy_pairing_validates(category, route):
    TriageDecision.model_validate({**VALID, "category": category, "route": route})


def test_bad_priority():
    assert_rejected({**VALID, "priority": "P5"}, "priority")


def test_bad_category():
    data = {**VALID, "category": "sales"}
    assert_rejected(data, "category")
    # Pairing check is skipped when category itself is invalid.
    with pytest.raises(ValidationError) as exc_info:
        TriageDecision.model_validate(data)
    assert error_fields(exc_info.value) == {"category"}


def test_bad_route_value():
    assert_rejected({**VALID, "route": "sales-team"}, "route")


def test_mismatched_pair():
    data = {**VALID, "route": "bug-team"}
    assert_rejected(data, "route")
    with pytest.raises(ValidationError) as exc_info:
        TriageDecision.model_validate(data)
    assert error_fields(exc_info.value) == {"route"}


@pytest.mark.parametrize("priority", ["P1", "P2", "P3", "P4"])
def test_every_priority_validates(priority):
    assert TriageDecision.model_validate({**VALID, "priority": priority}).priority == priority


def test_fields_cannot_be_reassigned():
    decision = TriageDecision.model_validate(VALID)
    with pytest.raises(ValidationError):
        decision.route = "bug-team"


def test_missing_field():
    data = {k: v for k, v in VALID.items() if k != "rationale"}
    assert_rejected(data, "rationale")


def test_wrong_type():
    assert_rejected({**VALID, "priority": 2}, "priority")


def test_empty_rationale():
    assert_rejected({**VALID, "rationale": "   "}, "rationale")


def test_rationale_not_mutated():
    decision = TriageDecision.model_validate({**VALID, "rationale": "  Padded.  "})
    assert decision.rationale == "  Padded.  "


def test_extra_field():
    assert_rejected({**VALID, "confidence": 0.9}, "confidence")


def test_triage_validation_error_is_exception():
    assert issubclass(TriageValidationError, Exception)
    with pytest.raises(TriageValidationError):
        raise TriageValidationError("still invalid after retry")
