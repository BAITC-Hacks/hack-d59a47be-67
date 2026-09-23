"""Validate prepared facts and render explanations without model-written claims.

The backend owns eligibility and skill calculations. This boundary only checks
their internal consistency; it cannot recover missing facts or infer new ones.
"""

from math import isclose

from pydantic import ValidationError

from backend.app.contracts import (
    EligibleCandidate,
    EvidenceFact,
    Explanation,
    RecommendationContext,
    RecommendationResult,
    RecommendationSelection,
)


class InvalidContext(ValueError):
    """Prepared input is inconsistent or lacks usable supporting evidence."""


class InvalidSelection(ValueError):
    """The model did not select a bounded set of eligible event IDs."""


def _same(left: float, right: float) -> bool:
    return isclose(left, right, rel_tol=1e-9, abs_tol=1e-9)


def _positive_skills(context: RecommendationContext, candidate: EligibleCandidate) -> set[str]:
    gaps = {gap.skill_id: gap for gap in context.gaps}
    return {
        effect.skill_id
        for effect in candidate.effects
        if effect.delta > 0
        and effect.skill_id in gaps
        and gaps[effect.skill_id].gap > 0
        and min(effect.after, gaps[effect.skill_id].target_level) > effect.before
    }


def _explanation(context: RecommendationContext, candidate: EligibleCandidate) -> Explanation:
    """Pick one relevant fact per category, without truncating a supporting fact.

    Choosing the shortest fact in each category gives a deterministic bounded
    explanation whenever such a four-category explanation can fit the contract.
    IDs break ties and have no assumed prefix or other semantic meaning.
    """
    profile_subjects = {"profile", context.profile.profile_ref}
    affected = _positive_skills(context, candidate)
    candidate_evidence = set(candidate.evidence_ids)
    groups: dict[str, list[EvidenceFact]] = {kind: [] for kind in ("goal", "gap", "history", "candidate")}
    for fact in context.facts:
        if fact.kind == "goal" and fact.subject_id in profile_subjects:
            groups["goal"].append(fact)
        elif fact.kind == "gap" and fact.subject_id in affected:
            groups["gap"].append(fact)
        elif fact.kind == "history" and fact.subject_id in profile_subjects | {candidate.event_id}:
            groups["history"].append(fact)
        elif (
            fact.kind == "candidate"
            and fact.subject_id == candidate.event_id
            and fact.evidence_id in candidate_evidence
        ):
            groups["candidate"].append(fact)
    if any(not facts for facts in groups.values()):
        raise InvalidContext("Required supporting evidence is missing.")
    chosen = [min(facts, key=lambda fact: (len(fact.fact), fact.evidence_id)) for facts in groups.values()]
    # Event-specific participation is more informative when present. A long
    # specific fact may be replaced by a complete truthful global summary, never
    # shortened in a way that changes its meaning.
    specific_history = [fact for fact in groups["history"] if fact.subject_id == candidate.event_id]
    if specific_history:
        history = min(specific_history, key=lambda fact: (len(fact.fact), fact.evidence_id))
        preferred = [*chosen[:2], history, chosen[3]]
        if len(" ".join(fact.fact for fact in preferred)) <= 2000:
            chosen = preferred
    text = " ".join(fact.fact for fact in chosen)
    if len(text) > 2000:
        raise InvalidContext("Supporting evidence exceeds the explanation limit.")
    return Explanation(text=text, evidence_ids=[fact.evidence_id for fact in chosen])


def _validated_context(context: RecommendationContext) -> RecommendationContext:
    if not isinstance(context, RecommendationContext):
        raise InvalidContext("Expected a recommendation context.")
    try:
        # A shared Pydantic model is mutable: revalidate nested fields even if a
        # caller changed an already-created instance or used model_construct.
        checked = RecommendationContext.model_validate(context.model_dump(warnings=False))
    except (ValidationError, TypeError, ValueError, AttributeError):
        raise InvalidContext("Recommendation context does not satisfy the contract.") from None

    skills = {skill.skill_id: skill.level for skill in checked.current_skills}
    if len(skills) != len(checked.current_skills):
        raise InvalidContext("Current skill identifiers must be unique.")
    gaps = {gap.skill_id: gap for gap in checked.gaps}
    if len(gaps) != len(checked.gaps):
        raise InvalidContext("Gap skill identifiers must be unique.")
    for gap in checked.gaps:
        if gap.skill_id not in skills or not _same(gap.current_level, skills[gap.skill_id]):
            raise InvalidContext("Gap levels do not match current skills.")
        if not _same(gap.gap, max(0.0, gap.target_level - gap.current_level)):
            raise InvalidContext("Prepared gap values are inconsistent.")

    for candidate in checked.eligible_candidates:
        effect_ids = [effect.skill_id for effect in candidate.effects]
        if len(effect_ids) != len(set(effect_ids)):
            raise InvalidContext("Effect skill identifiers must be unique.")
        if len(candidate.evidence_ids) != len(set(candidate.evidence_ids)):
            raise InvalidContext("Candidate evidence identifiers must be unique.")
        for effect in candidate.effects:
            if effect.skill_id not in skills or not _same(effect.before, skills[effect.skill_id]):
                raise InvalidContext("Effect levels do not match current skills.")
            if effect.delta < 0 or effect.after < effect.before:
                raise InvalidContext("Prepared skill effects cannot decrease levels.")
            if not _same(effect.delta, effect.after - effect.before):
                raise InvalidContext("Prepared skill effects are inconsistent.")
        if not _positive_skills(checked, candidate):
            raise InvalidContext("A candidate must reduce a positive target gap.")
        _explanation(checked, candidate)
    return checked


def validate_context(context: RecommendationContext) -> None:
    """Check the shared schema plus consistency of the backend's prepared facts.

    Public exceptions deliberately contain no input values, facts, or profile
    data. Empty candidates are valid input; the caller handles no_candidates.
    """
    _validated_context(context)


def build_result(context: RecommendationContext, event_ids: list[str]) -> RecommendationResult:
    """Resolve only selected eligible IDs into an evidence-backed shared result."""
    context = _validated_context(context)
    if not isinstance(event_ids, list) or not all(isinstance(event_id, str) for event_id in event_ids):
        raise InvalidSelection("Expected a list of event identifiers.")
    if not 1 <= len(event_ids) <= context.limit:
        raise InvalidSelection("Selection count is outside the requested limit.")
    if len(set(event_ids)) != len(event_ids):
        raise InvalidSelection("Selected event identifiers must be unique.")
    candidates = {candidate.event_id: candidate for candidate in context.eligible_candidates}
    if any(event_id not in candidates for event_id in event_ids):
        raise InvalidSelection("Selection contains an ineligible event.")
    return RecommendationResult(
        status="ok",
        engine="oleg",
        recommendations=[
            RecommendationSelection(
                event_id=event_id, explanation=_explanation(context, candidates[event_id])
            )
            for event_id in event_ids
        ],
    )
