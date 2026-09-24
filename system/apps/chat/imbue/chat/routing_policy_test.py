"""Routing must preserve capability requirements across providers and exhaustion."""

import pytest

from imbue.chat.harnesses.model import EffortChoice
from imbue.chat.harnesses.model import ModelIdentity
from imbue.chat.harnesses.model import ModelOption
from imbue.chat.routing_policy import RoutingAssessment
from imbue.chat.routing_policy import RoutingCandidate
from imbue.chat.routing_policy import RoutingTier
from imbue.chat.routing_policy import assess_routing_task
from imbue.chat.routing_policy import candidate_from_option
from imbue.chat.routing_policy import rank_routing_candidates
from imbue.imbue_common.model_update import to_update


@pytest.mark.parametrize(
    ("message", "tier"),
    [
        ("Fix the typo in this heading", RoutingTier.ROUTINE),
        ("Summarize this paragraph", RoutingTier.ROUTINE),
        ("Build a search page", RoutingTier.STANDARD),
        ("Help", RoutingTier.STANDARD),
        ("Fix the production authentication race condition", RoutingTier.COMPLEX),
        ("Summarize this security architecture", RoutingTier.COMPLEX),
        ("Refactor across multiple files", RoutingTier.COMPLEX),
        ("It is still broken", RoutingTier.COMPLEX),
    ],
)
def test_assessment(message: str, tier: RoutingTier) -> None:
    result = assess_routing_task(message)
    assert result.tier == tier
    assert result.reasons


@pytest.mark.parametrize("message", ["", "yes", "build it", "keep going", "3"])
def test_continuations_preserve_complexity(message: str) -> None:
    assert assess_routing_task(message, RoutingTier.COMPLEX).tier == RoutingTier.COMPLEX


def test_new_bounded_task_can_lower_previous_complexity() -> None:
    assert assess_routing_task("Fix a typo", RoutingTier.COMPLEX).tier == RoutingTier.ROUTINE


def test_ranking_uses_requirements_not_provider_order() -> None:
    candidates = [
        RoutingCandidate(
            account_id="slow",
            provider="one",
            model=ModelIdentity(model_id="a", effort=None, fast=False),
            capability=3,
            speed=1,
        ),
        RoutingCandidate(
            account_id="quick",
            provider="two",
            model=ModelIdentity(model_id="b", effort=None, fast=False),
            capability=1,
            speed=3,
        ),
        RoutingCandidate(
            account_id="balanced",
            provider="three",
            model=ModelIdentity(model_id="c", effort=None, fast=False),
            capability=2,
            speed=2,
        ),
    ]
    for tier, expected in [
        (RoutingTier.ROUTINE, "quick"),
        (RoutingTier.STANDARD, "balanced"),
        (RoutingTier.COMPLEX, "slow"),
    ]:
        assessment = RoutingAssessment(tier=tier, reasons=())
        ranking = rank_routing_candidates(assessment, candidates)
        assert ranking[0].account_id == expected
        assert ranking == rank_routing_candidates(assessment, list(reversed(candidates)))
    assert (
        rank_routing_candidates(
            RoutingAssessment(tier=RoutingTier.COMPLEX, reasons=()), candidates, frozenset({"slow"})
        )
        == []
    )


def test_equivalent_current_account_prevents_unnecessary_switch() -> None:
    candidates = [
        RoutingCandidate(
            account_id=account,
            provider=account,
            model=ModelIdentity(model_id="same", effort="high", fast=False),
            capability=3,
            speed=1,
        )
        for account in ("a", "b")
    ]
    assessment = RoutingAssessment(tier=RoutingTier.COMPLEX, reasons=())
    assert rank_routing_candidates(assessment, candidates, current_account="b")[0].account_id == "b"
    assert rank_routing_candidates(assessment, candidates, frozenset({"b"}), "b")[0].account_id == "a"


@pytest.mark.parametrize("model_id", ["unknown-new-model", "gpt-6-unreviewed", "gemini-8-pro", "claude-opus-99"])
def test_unknown_models_are_not_assumed_capable(model_id: str) -> None:
    option = ModelOption(id=model_id, label=model_id, efforts=(), supports_fast=False)
    assert candidate_from_option("account", "provider", option) is None


def test_effort_selection_respects_visibility_and_capability() -> None:
    option = ModelOption(
        id="gpt-6-astra",
        label="Astra",
        efforts=(EffortChoice(level="low"), EffortChoice(level="high")),
        supports_fast=True,
    )
    routine = candidate_from_option("a", "openai", option, RoutingTier.ROUTINE)
    complex_candidate = candidate_from_option("a", "openai", option, RoutingTier.COMPLEX)
    assert routine is not None and routine.model.effort == "low"
    assert complex_candidate is not None and complex_candidate.model.effort == "high"
    assert not complex_candidate.model.fast
    hidden = option.model_copy_update(to_update(option.field_ref().in_picker, False))
    assert candidate_from_option("a", "openai", hidden) is None
    hidden_effort = option.model_copy_update(
        to_update(option.field_ref().efforts, (EffortChoice(level="high", in_picker=False),))
    )
    assert candidate_from_option("a", "openai", hidden_effort, RoutingTier.COMPLEX) is None
    low = option.model_copy_update(to_update(option.field_ref().id, "haiku"))
    assert candidate_from_option("a", "anthropic", low, RoutingTier.COMPLEX) is None


def test_embedded_effort_models_do_not_get_invented_efforts() -> None:
    option = ModelOption(id="gemini-3.1-pro-high", label="Pro", efforts=(), supports_fast=False)
    candidate = candidate_from_option("a", "google", option, RoutingTier.COMPLEX)
    assert candidate is not None
    assert candidate.model.effort is None
