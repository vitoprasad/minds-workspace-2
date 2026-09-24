"""Turning the routing policy into one decision about one turn.

``routing_policy.py`` answers two questions in the abstract -- how hard is this work, and which of
these models fit it. This module supplies the real inputs (the accounts the user has signed in to,
the models each offers, what the chat is running now, whether that has just stopped answering) and
turns the answer into one of three actions the chat app can carry out.

The split between the three actions is where the cost lives, so the rules are deliberately uneven:

* **Changing model inside the chat's own account is cheap** -- one command to a running agent, no
  new process, nothing lost. So it happens whenever a better-fitting model is there.
* **Changing account is expensive** -- it retires the agent, summarizes, and starts a successor.
  So it happens only when it must: the chat's own account cannot reach the work's floor, or it has
  stopped answering. A merely better model elsewhere is never worth it, which is also what keeps a
  chat from walking back and forth between two providers as its turns vary in difficulty.
* **Staying put is the default** and needs no justification.

Every decision is made at a user-turn boundary and never mid-turn. That is what makes the recovery
path safe: a turn that half-ran is left alone rather than replayed somewhere else, so nothing a
tool already did -- a message sent, a file written -- can happen twice because a provider failed.
"""

from collections.abc import Mapping
from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from imbue.chat.accounts import Account
from imbue.chat.accounts import AccountIndex
from imbue.chat.harnesses.model import ModelIdentity
from imbue.chat.harnesses.model import ModelOption
from imbue.chat.harnesses.model import SwitchMode
from imbue.chat.routing_policy import RoutingAssessment
from imbue.chat.routing_policy import RoutingCandidate
from imbue.chat.routing_policy import RoutingTier
from imbue.chat.routing_policy import candidate_from_option
from imbue.chat.routing_policy import rank_routing_candidates
from imbue.imbue_common.frozen_model import FrozenModel

# The API failures that mean this account cannot serve the work, whoever asked. A bad request or a
# missing file is the chat's own doing and moving it elsewhere would only repeat it; a spent quota,
# a rate limit, and an overloaded provider are facts about the provider, and another one is free of
# them. Auth failures ride their own flag rather than this kind (``auth_errors.py``), and the
# exhausted-entitlement case -- out of credits -- is deliberately in that family.
_EXHAUSTION_KINDS: frozenset[str] = frozenset({"rate_limit", "overloaded", "api_error"})


class RoutingAction(StrEnum):
    """What routing decided to do about one turn."""

    # Run the turn on what the chat is already on.
    STAY = "stay"
    # Put the chat's own agent on a different model first, then run the turn.
    SWITCH_MODEL = "switch_model"
    # Move the chat to another account, which runs the turn once it is up.
    SWITCH_ACCOUNT = "switch_account"


class RoutingDecision(FrozenModel):
    """One routing answer, carrying why -- the reason is written for the user, not the log."""

    action: RoutingAction
    assessment: RoutingAssessment
    # Where to move to; None when staying put.
    target: RoutingCandidate | None = None
    reason: str


def is_provider_exhausted(events: Sequence[Mapping[str, Any]]) -> bool:
    """Whether this chat's last model reply failed in a way that another provider would not.

    Reads the flags every harness's parser already stamps on the event (``auth_errors.py`` and
    ``error_patterns.py`` do the classifying), so no error text is re-parsed here. Only the most
    recent reply counts: an old failure the chat has since recovered from says nothing about the
    account now, and a chat that has answered since is plainly still being served.
    """
    for event in reversed(events):
        if event.get("type") != "assistant_message":
            continue
        if event.get("is_auth_error") is True:
            return True
        return event.get("is_api_error") is True and event.get("api_error_kind") in _EXHAUSTION_KINDS
    return False


def options_for_account(
    catalog_options: tuple[ModelOption, ...],
    persisted_options: tuple[ModelOption, ...] | None,
) -> tuple[ModelOption, ...]:
    """The models an account can actually offer: its live per-agent set when it has one, else its catalog.

    A harness whose set is per agent (codex reads it off its daemon) has an empty catalog, so the
    only honest answer is what one of its agents was last offered. An account that has never run an
    agent therefore offers nothing and is not routable yet -- which is correct: routing to a model
    set nobody has seen would be guessing.
    """
    if persisted_options is not None:
        return persisted_options
    return catalog_options


def collect_candidates(
    index: AccountIndex,
    options_by_account: Mapping[str, tuple[ModelOption, ...]],
    tier: RoutingTier,
) -> list[RoutingCandidate]:
    """Every (account, model) pairing the user could be moved to for work at ``tier``.

    Built from the account index rather than from a list of providers, so an account the user signs
    in to is routable the moment it exists and one they delete stops being offered, with nothing to
    keep in step. Models the policy has no reviewed profile for are dropped by
    ``candidate_from_option``, so an unfamiliar model is never chosen on a guess.
    """
    candidates: list[RoutingCandidate] = []
    for account in index.accounts:
        for option in options_by_account.get(account.id, ()):
            candidate = candidate_from_option(account.id, _provider_of(account), option, tier)
            if candidate is not None:
                candidates.append(candidate)
    return candidates


def _provider_of(account: Account) -> str:
    """The provider noun for an account, ignoring any name the user gave it."""
    return account.display


def decide_route(
    assessment: RoutingAssessment,
    candidates: Sequence[RoutingCandidate],
    current_account_id: str,
    current_identity: ModelIdentity | None,
    *,
    is_current_exhausted: bool,
    is_model_switchable: bool,
    is_current_account_known: bool = True,
    exhausted_accounts: frozenset[str] = frozenset(),
) -> RoutingDecision:
    """Decide what to do about a turn the chat is about to run.

    ``candidates`` are every pairing available for this tier (``collect_candidates``);
    ``is_model_switchable`` is whether the chat's harness can change model in a running session at
    all -- a display-only harness cannot, so for it the only way to a different model is a different
    account. An exhausted account is excluded from the ranking outright, including the chat's own.

    ``is_current_account_known`` separates the two ways the chat's own account can contribute no
    candidates: it offers nothing that reaches the work's floor, or nobody has yet seen what it
    offers at all (a harness whose model set is per agent, before one has run). The first is
    grounds to move; the second is grounds to do nothing, because moving on no evidence would drag
    the chat off a perfectly good account every turn.
    """
    excluded: frozenset[str] = exhausted_accounts | (
        frozenset({current_account_id}) if is_current_exhausted else frozenset()
    )
    ranked = rank_routing_candidates(assessment, candidates, excluded, current_account_id)
    if not ranked:
        return RoutingDecision(
            action=RoutingAction.STAY,
            assessment=assessment,
            reason="No other signed-in account offers a model suited to this, so the chat stays where it is.",
        )
    best = ranked[0]

    # The account stopped answering: the work moves, whatever it is worth, because staying means not
    # running at all.
    if is_current_exhausted:
        return RoutingDecision(
            action=RoutingAction.SWITCH_ACCOUNT,
            assessment=assessment,
            target=best,
            reason=f"{best.provider} takes over because the previous one stopped answering.",
        )

    if not is_current_account_known:
        return RoutingDecision(
            action=RoutingAction.STAY,
            assessment=assessment,
            reason="What this account offers is not known yet, so the chat stays where it is.",
        )

    on_own_account = [candidate for candidate in ranked if candidate.account_id == current_account_id]

    # The chat's own account can serve the work. Nothing here is worth retiring an agent for, so the
    # most that happens is a model change inside the session.
    if on_own_account:
        preferred = on_own_account[0]
        # An account can offer two models that score exactly alike (Anthropic's two top models do),
        # and the ranking then has to break the tie on something arbitrary. Doing that while the chat
        # is already sitting on one of them would swap it for its equal for no reason the user could
        # name, so a current model that scores as well as the best keeps its place; only its effort
        # moves. A model that scores WORSE is still left behind -- that is the whole point.
        current_match = next(
            (
                candidate
                for candidate in on_own_account
                if current_identity is not None and candidate.model.model_id == current_identity.model_id
            ),
            None,
        )
        if current_match is not None and (current_match.capability, current_match.speed) == (
            preferred.capability,
            preferred.speed,
        ):
            preferred = current_match
        is_already_there = current_identity is not None and (
            current_identity.model_id == preferred.model.model_id
            and current_identity.effort == preferred.model.effort
        )
        if is_already_there or not is_model_switchable:
            return RoutingDecision(
                action=RoutingAction.STAY, assessment=assessment, reason="The current model suits this work."
            )
        return RoutingDecision(
            action=RoutingAction.SWITCH_MODEL,
            assessment=assessment,
            target=preferred,
            reason=f"Moved to a model that fits {assessment.tier.value} work.",
        )

    # Nothing on this account reaches the floor, so the work has to move.
    return RoutingDecision(
        action=RoutingAction.SWITCH_ACCOUNT,
        assessment=assessment,
        target=best,
        reason=f"{best.provider} takes over because this work needs more reasoning than this account offers.",
    )


def is_model_switchable(switch_mode: SwitchMode) -> bool:
    """Whether a running agent on this harness can be put on another model without being replaced."""
    return switch_mode is not SwitchMode.READ_ONLY
