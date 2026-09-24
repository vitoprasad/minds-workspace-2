"""Routing must move a chat only when it is worth it, and never back into an account that just failed."""

from collections.abc import Mapping
from typing import Any

import pytest

from imbue.chat.accounts import Account
from imbue.chat.accounts import AccountIndex
from imbue.chat.harnesses.model import EffortChoice
from imbue.chat.harnesses.model import ModelIdentity
from imbue.chat.harnesses.model import ModelOption
from imbue.chat.harnesses.model import SwitchMode
from imbue.chat.routing_policy import RoutingAssessment
from imbue.chat.routing_policy import RoutingCandidate
from imbue.chat.routing_policy import RoutingTier
from imbue.chat.routing_service import RoutingAction
from imbue.chat.routing_service import RoutingDecision
from imbue.chat.routing_service import collect_candidates
from imbue.chat.routing_service import decide_route
from imbue.chat.routing_service import is_model_switchable
from imbue.chat.routing_service import is_provider_exhausted
from imbue.chat.routing_service import options_for_account

_OPUS = ModelOption(
    id="opus[1m]",
    label="Opus 5 (1M)",
    efforts=(EffortChoice(level="medium"), EffortChoice(level="high")),
    supports_fast=True,
)
_HAIKU = ModelOption(id="haiku", label="Haiku 4.5", efforts=(), supports_fast=False)


def _assistant_event(**overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": "assistant_message",
        "is_auth_error": False,
        "is_api_error": False,
        "api_error_kind": None,
    }
    event.update(overrides)
    return event


def _candidate(account_id: str, model_id: str, capability: int, speed: int, effort: str | None = None) -> RoutingCandidate:
    return RoutingCandidate(
        account_id=account_id,
        provider=account_id.title(),
        model=ModelIdentity(model_id=model_id, effort=effort, fast=False),
        capability=capability,
        speed=speed,
    )


def _decide(
    tier: RoutingTier,
    candidates: list[RoutingCandidate],
    current_account_id: str = "own",
    current_identity: ModelIdentity | None = None,
    *,
    is_current_exhausted: bool = False,
    is_switchable: bool = True,
    is_current_account_known: bool = True,
    exhausted_accounts: frozenset[str] = frozenset(),
) -> RoutingDecision:
    return decide_route(
        RoutingAssessment(tier=tier, reasons=("because",)),
        candidates,
        current_account_id,
        current_identity,
        is_current_exhausted=is_current_exhausted,
        is_model_switchable=is_switchable,
        is_current_account_known=is_current_account_known,
        exhausted_accounts=exhausted_accounts,
    )


@pytest.mark.parametrize(
    ("event", "is_exhausted"),
    [
        (_assistant_event(is_auth_error=True), True),
        (_assistant_event(is_api_error=True, api_error_kind="rate_limit"), True),
        (_assistant_event(is_api_error=True, api_error_kind="overloaded"), True),
        (_assistant_event(is_api_error=True, api_error_kind="api_error"), True),
        (_assistant_event(is_api_error=True, api_error_kind="invalid_request"), False),
        (_assistant_event(is_api_error=True, api_error_kind="not_found"), False),
        (_assistant_event(), False),
    ],
)
def test_only_a_failure_another_provider_would_avoid_counts_as_exhaustion(
    event: Mapping[str, Any], is_exhausted: bool
) -> None:
    assert is_provider_exhausted([event]) is is_exhausted


def test_exhaustion_is_judged_on_the_latest_reply_only() -> None:
    failed = _assistant_event(is_auth_error=True)
    answered = _assistant_event()
    assert is_provider_exhausted([failed, answered]) is False
    assert is_provider_exhausted([answered, failed]) is True
    # A user turn after the failure does not clear it: only another reply shows the account working.
    assert is_provider_exhausted([failed, {"type": "user_message"}]) is True
    assert is_provider_exhausted([]) is False


def test_an_account_offers_its_live_set_when_it_has_one_and_its_catalog_otherwise() -> None:
    assert options_for_account((_OPUS,), None) == (_OPUS,)
    assert options_for_account((), (_HAIKU,)) == (_HAIKU,)
    # A dynamic harness whose agent has been offered nothing yet offers nothing, rather than a catalog it lacks.
    assert options_for_account((), ()) == ()


def test_candidates_come_from_the_signed_in_accounts_and_skip_unreviewed_models() -> None:
    index = AccountIndex(
        accounts=(
            Account(id="anthropic-1", lane="anthropic", seq=1, display="Anthropic"),
            Account(id="openai-1", lane="openai", seq=1, display="OpenAI"),
            Account(id="no-agent-yet", lane="openai", seq=2, display="OpenAI"),
        )
    )
    unreviewed = ModelOption(id="brand-new-model", label="New", efforts=(), supports_fast=False)
    candidates = collect_candidates(
        index,
        {"anthropic-1": (_OPUS, _HAIKU, unreviewed), "openai-1": (unreviewed,)},
        RoutingTier.ROUTINE,
    )
    assert {(candidate.account_id, candidate.model.model_id) for candidate in candidates} == {
        ("anthropic-1", "opus[1m]"),
        ("anthropic-1", "haiku"),
    }
    assert all(candidate.provider == "Anthropic" for candidate in candidates)


def test_work_the_current_model_suits_moves_nowhere() -> None:
    on_own = _candidate("own", "opus[1m]", capability=3, speed=1, effort="high")
    decision = _decide(RoutingTier.COMPLEX, [on_own], current_identity=on_own.model)
    assert decision.action is RoutingAction.STAY


def test_easy_work_drops_to_a_quicker_model_on_the_same_account() -> None:
    decision = _decide(
        RoutingTier.ROUTINE,
        [_candidate("own", "opus[1m]", 3, 1, "medium"), _candidate("own", "haiku", 1, 3)],
        current_identity=ModelIdentity(model_id="opus[1m]", effort="medium", fast=False),
    )
    assert decision.action is RoutingAction.SWITCH_MODEL
    assert decision.target is not None and decision.target.model.model_id == "haiku"
    assert decision.target.account_id == "own"


def test_a_better_model_elsewhere_is_never_worth_moving_account_for() -> None:
    decision = _decide(
        RoutingTier.STANDARD,
        [_candidate("own", "sonnet[1m]", 2, 2, "medium"), _candidate("other", "opus[1m]", 3, 1, "high")],
        current_identity=ModelIdentity(model_id="sonnet[1m]", effort="medium", fast=False),
    )
    assert decision.action is RoutingAction.STAY


def test_work_beyond_the_account_moves_to_one_that_can_take_it() -> None:
    decision = _decide(
        RoutingTier.COMPLEX,
        [_candidate("other", "opus[1m]", 3, 1, "high")],
        current_identity=ModelIdentity(model_id="haiku", effort=None, fast=False),
    )
    assert decision.action is RoutingAction.SWITCH_ACCOUNT
    assert decision.target is not None and decision.target.account_id == "other"


def test_a_model_the_policy_does_not_know_is_not_trusted_with_hard_work() -> None:
    decision = _decide(
        RoutingTier.COMPLEX,
        [_candidate("other", "opus[1m]", 3, 1, "high")],
        current_identity=ModelIdentity(model_id="something-unreviewed", effort=None, fast=False),
    )
    assert decision.action is RoutingAction.SWITCH_ACCOUNT


def test_an_account_that_stopped_answering_is_left_even_when_it_fits() -> None:
    decision = _decide(
        RoutingTier.STANDARD,
        [_candidate("own", "opus[1m]", 3, 1, "medium"), _candidate("other", "sonnet[1m]", 2, 2, "medium")],
        current_identity=ModelIdentity(model_id="opus[1m]", effort="medium", fast=False),
        is_current_exhausted=True,
    )
    assert decision.action is RoutingAction.SWITCH_ACCOUNT
    assert decision.target is not None and decision.target.account_id == "other"
    assert "stopped answering" in decision.reason


def test_an_account_that_already_failed_this_chat_is_not_chosen_again() -> None:
    decision = _decide(
        RoutingTier.COMPLEX,
        [_candidate("burned", "opus[1m]", 3, 1, "high"), _candidate("other", "opus[1m]", 3, 1, "high")],
        current_identity=ModelIdentity(model_id="haiku", effort=None, fast=False),
        exhausted_accounts=frozenset({"burned"}),
    )
    assert decision.action is RoutingAction.SWITCH_ACCOUNT
    assert decision.target is not None and decision.target.account_id == "other"


def test_a_chat_with_nowhere_to_go_runs_where_it_is() -> None:
    decision = _decide(
        RoutingTier.COMPLEX,
        [_candidate("own", "opus[1m]", 3, 1, "high")],
        current_identity=ModelIdentity(model_id="opus[1m]", effort="high", fast=False),
        is_current_exhausted=True,
    )
    assert decision.action is RoutingAction.STAY
    assert decision.target is None


def test_a_harness_that_cannot_change_model_in_place_is_left_alone_while_it_copes() -> None:
    decision = _decide(
        RoutingTier.ROUTINE,
        [_candidate("own", "opus[1m]", 3, 1, "medium"), _candidate("own", "haiku", 1, 3)],
        current_identity=ModelIdentity(model_id="opus[1m]", effort="medium", fast=False),
        is_switchable=False,
    )
    assert decision.action is RoutingAction.STAY


def test_only_a_display_only_harness_is_treated_as_unswitchable() -> None:
    assert is_model_switchable(SwitchMode.EAGER_THEN_RECONCILE) is True
    assert is_model_switchable(SwitchMode.ON_CHANGE) is True
    assert is_model_switchable(SwitchMode.READ_ONLY) is False


def test_the_effort_a_turn_needs_counts_as_a_model_change() -> None:
    decision = _decide(
        RoutingTier.COMPLEX,
        [_candidate("own", "opus[1m]", 3, 1, "high")],
        current_identity=ModelIdentity(model_id="opus[1m]", effort="medium", fast=False),
    )
    assert decision.action is RoutingAction.SWITCH_MODEL
    assert decision.target is not None and decision.target.model.effort == "high"


def test_an_account_nobody_has_seen_the_models_of_is_left_alone() -> None:
    """Contributing no candidates means "cannot serve this" only for an account whose models are known."""
    arguments: dict[str, Any] = {
        "current_identity": ModelIdentity(model_id="haiku", effort=None, fast=False),
    }
    elsewhere = [_candidate("other", "opus[1m]", 3, 1, "high")]
    assert _decide(RoutingTier.COMPLEX, elsewhere, **arguments).action is RoutingAction.SWITCH_ACCOUNT
    unknown = _decide(RoutingTier.COMPLEX, elsewhere, is_current_account_known=False, **arguments)
    assert unknown.action is RoutingAction.STAY
    # Unless it has stopped answering, which needs no knowledge of its models at all.
    moved = _decide(
        RoutingTier.COMPLEX, elsewhere, is_current_account_known=False, is_current_exhausted=True, **arguments
    )
    assert moved.action is RoutingAction.SWITCH_ACCOUNT


def test_a_model_that_scores_as_well_as_the_best_keeps_its_place() -> None:
    """Two models an account scores alike must not be swapped for one another; only the effort moves."""
    equals = [
        _candidate("own", "fable[1m]", 3, 1, "high"),
        _candidate("own", "opus[1m]", 3, 1, "high"),
    ]
    for order in (equals, list(reversed(equals))):
        decision = _decide(
            RoutingTier.COMPLEX,
            order,
            current_identity=ModelIdentity(model_id="opus[1m]", effort="high", fast=False),
        )
        assert decision.action is RoutingAction.STAY
    raising_effort = _decide(
        RoutingTier.COMPLEX,
        equals,
        current_identity=ModelIdentity(model_id="opus[1m]", effort="low", fast=False),
    )
    assert raising_effort.action is RoutingAction.SWITCH_MODEL
    assert raising_effort.target is not None
    assert raising_effort.target.model.model_id == "opus[1m]"
    assert raising_effort.target.model.effort == "high"


def test_a_model_that_scores_worse_than_the_best_is_still_left_behind() -> None:
    decision = _decide(
        RoutingTier.ROUTINE,
        [_candidate("own", "haiku", 1, 3), _candidate("own", "opus[1m]", 3, 1, "low")],
        current_identity=ModelIdentity(model_id="opus[1m]", effort="low", fast=False),
    )
    assert decision.action is RoutingAction.SWITCH_MODEL
    assert decision.target is not None and decision.target.model.model_id == "haiku"
