"""The triage-decision contract shared by Epic 2 (agent output) and Epic 3 (valid_schema scorer)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator

Category = Literal["billing", "bug", "access", "performance", "how-to"]
Priority = Literal["P1", "P2", "P3", "P4"]
Route = Literal["billing-team", "bug-team", "access-team", "performance-team", "how-to-team"]

# Category -> route pairing from TRIAGE_POLICY.md.
ROUTE_FOR_CATEGORY: dict[str, str] = {
    "billing": "billing-team",
    "bug": "bug-team",
    "access": "access-team",
    "performance": "performance-team",
    "how-to": "how-to-team",
}


class TriageValidationError(Exception):
    """Raised by callers (Epic 2) when a triage decision still fails validation after a retry."""


class TriageDecision(BaseModel):
    """One triage decision: category, priority, route and a rationale."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Declaration order matters: `category` must validate before `route`.
    category: Category
    priority: Priority
    route: Route
    rationale: str

    @field_validator("route")
    @classmethod
    def _route_matches_category(cls, route: str, info: ValidationInfo) -> str:
        category = info.data.get("category")
        if category is None:
            # category already failed; let only that error be reported.
            return route
        expected = ROUTE_FOR_CATEGORY[category]
        if route != expected:
            raise ValueError(f"route {route!r} does not match category {category!r}; expected {expected!r}")
        return route

    @field_validator("rationale")
    @classmethod
    def _rationale_not_blank(cls, rationale: str) -> str:
        if not rationale.strip():
            raise ValueError("rationale must not be empty")
        return rationale
