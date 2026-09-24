"""Conservative task routing policy; model scores are policy estimates, not benchmarks.

Only catalog entries with an explicit profile can be selected. Capability is a
floor: exhaustion never silently relaxes it. Provider order has no significance.
"""

import hashlib
import re
from collections.abc import Sequence
from enum import StrEnum
from typing import assert_never

from pydantic import Field

from imbue.chat.harnesses.model import ModelIdentity
from imbue.chat.harnesses.model import ModelOption
from imbue.imbue_common.frozen_model import FrozenModel


class RoutingTier(StrEnum):
    """The reasoning requirements of a task."""

    ROUTINE = "routine"
    STANDARD = "standard"
    COMPLEX = "complex"


class RoutingAssessment(FrozenModel):
    """An explainable conservative classification."""

    tier: RoutingTier
    reasons: tuple[str, ...]


class RoutingCandidate(FrozenModel):
    """One available account and valid model selection, scored by policy."""

    account_id: str
    provider: str
    model: ModelIdentity
    capability: int = Field(ge=1, le=3)
    speed: int = Field(ge=1, le=3)


def assess_routing_task(message: str, previous_tier: RoutingTier | None = None) -> RoutingAssessment:
    """Keep continuation context and require positive evidence for routine work."""
    text = " ".join(message.lower().split()).strip(".!?")
    if previous_tier is not None and (
        not text or text in {"yes", "ok", "okay", "continue", "keep going", "build it", "do it", "3", "go ahead"}
    ):
        return RoutingAssessment(tier=previous_tier, reasons=("Continuation retains the task's requirements.",))
    if (
        re.search(
            r"\b(architecture|architect|redesign|security|authentication|authorization|production|"
            r"migration|migrate|irreversible|payments|financial|medical|legal|distributed|"
            r"race condition|deadlock|multi[- ]file|across (?:the |multiple )?(?:files|services)|"
            r"still (?:broken|failing)|failed repeatedly)\b",
            text,
        )
        or len(text.split()) > 250
    ):
        return RoutingAssessment(
            tier=RoutingTier.COMPLEX, reasons=("Scope, uncertainty, or consequences need deeper reasoning.",)
        )
    if len(text.split()) <= 50 and re.match(
        r"^(?:please )?(?:fix (?:a |the )?typo\b|correct (?:the )?spelling\b|"
        r"summari[sz]e (?:this|the following)\b|translate (?:this|the following)\b|"
        r"format (?:this|the following)\b|what (?:time|date) is\b|count the (?:words|lines)\b)",
        text,
    ):
        return RoutingAssessment(tier=RoutingTier.ROUTINE, reasons=("Clearly bounded text or lookup task.",))
    return RoutingAssessment(
        tier=RoutingTier.STANDARD, reasons=("Unspecified or bounded implementation needs standard reasoning.",)
    )


def requirement_of(tier: RoutingTier) -> int:
    """The capability a model must have to be allowed to take on work at this tier."""
    match tier:
        case RoutingTier.ROUTINE:
            return 1
        case RoutingTier.STANDARD:
            return 2
        case RoutingTier.COMPLEX:
            return 3
        case _ as unreachable:
            assert_never(unreachable)


def _profile(model_id: str) -> tuple[int, int] | None:
    """Explicit reviewed families from local catalogs; new families need review."""
    name = model_id.rsplit("/", 1)[-1]
    if name in {
        "opus[1m]",
        "fable[1m]",
        "claude-opus-5",
        "claude-fable-5-1",
        "claude-opus-4-6-thinking",
        "gpt-6-astra",
        "gpt-5.6-sol",
        "gpt-5.5",
    }:
        return 3, 1
    if name in {"sonnet[1m]", "claude-sonnet-5", "claude-sonnet-4-6", "gpt-5.6-terra", "gpt-oss-120b-medium"}:
        return 2, 2
    if name in {"haiku", "claude-haiku-4-5", "gpt-5.6-luna"}:
        return 1, 3
    if re.fullmatch(r"gemini-3\.[567]-flash-(high|medium|low)", name):
        return (1, 3) if name.endswith("-low") else (2, 3)
    if name == "gemini-3.1-pro-high":
        return 3, 1
    if name == "gemini-3.1-pro-low":
        return 2, 2
    return None


def candidate_from_option(
    account_id: str, provider: str, option: ModelOption, tier: RoutingTier = RoutingTier.STANDARD
) -> RoutingCandidate | None:
    """Choose a visible supported effort, without inventing options or paid fast mode."""
    profile = _profile(option.id)
    if not option.in_picker or profile is None:
        return None
    capability, speed = profile
    if capability < requirement_of(tier):
        return None
    effort: str | None = None
    if option.efforts:
        available = {choice.level for choice in option.efforts if choice.in_picker}
        match tier:
            case RoutingTier.ROUTINE:
                preferred = ("low", "minimal", "off", "medium", "high", "xhigh", "max")
            case RoutingTier.STANDARD:
                preferred = ("medium", "high", "xhigh", "max")
            case RoutingTier.COMPLEX:
                preferred = ("high", "xhigh", "max")
            case _ as unreachable:
                assert_never(unreachable)
        effort = next((value for value in preferred if value in available), None)
        if effort is None:
            return None
    return RoutingCandidate(
        account_id=account_id,
        provider=provider,
        model=ModelIdentity(model_id=option.id, effort=effort, fast=False),
        capability=capability,
        speed=speed,
    )


def score_routing_candidate(
    candidate: RoutingCandidate, tier: RoutingTier, current_account: str | None
) -> tuple[int, int, bool, str]:
    """A candidate's sort key for work at ``tier``: how well it suits the work, then where it already is.

    Lower sorts first on every element. The last element is a hash of the candidate's own identity,
    which is what makes the order depend on nothing but the candidates themselves -- not on the order
    the accounts were read in, nor on the order a harness happens to list its models.
    """
    match tier:
        case RoutingTier.ROUTINE:
            suitability = (-candidate.speed, candidate.capability)
        case RoutingTier.STANDARD:
            suitability = (candidate.capability - requirement_of(tier), -candidate.speed)
        case RoutingTier.COMPLEX:
            suitability = (-candidate.capability, -candidate.speed)
        case _ as unreachable:
            assert_never(unreachable)
    identity = f"{candidate.account_id}:{candidate.provider}:{candidate.model.model_dump_json()}"
    return (*suitability, candidate.account_id != current_account, hashlib.sha256(identity.encode()).hexdigest())


def rank_routing_candidates(
    assessment: RoutingAssessment,
    candidates: Sequence[RoutingCandidate],
    excluded_accounts: frozenset[str] = frozenset(),
    current_account: str | None = None,
) -> list[RoutingCandidate]:
    """Rank suitable options independently of provider/catalog enumeration order."""
    requirement = requirement_of(assessment.tier)
    return sorted(
        (
            candidate
            for candidate in candidates
            if candidate.account_id not in excluded_accounts and candidate.capability >= requirement
        ),
        key=lambda candidate: score_routing_candidate(candidate, assessment.tier, current_account),
    )
