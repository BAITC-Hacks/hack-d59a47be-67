"""Backend-only deterministic arithmetic with hand-written synthetic records."""

from copy import deepcopy
from datetime import date

import pytest

from backend.app.contracts import SkillEffect, SkillGap
from backend.app.domain import (
    DEMO_DATE,
    DomainError,
    apply_event,
    effective_date,
    eligible_events,
    gaps,
    is_available,
    progress,
    replay,
    resolve_goal,
)
from backend.tests.fixtures import SKILL_IDS, employee, event, history, role_profiles


def test_replay_uses_snapshot_review_boundary_cutoff_and_completed_only():
    events = {f"SYNTH_{i}": event(f"SYNTH_{i}") for i in range(8)}
    dates = ["2026-08-31", "2026-09-01", "2026-09-02", "2026-10-01", "2026-10-02"]
    rows = [history(str(i), event_id=f"SYNTH_{i}", date=day) for i, day in enumerate(dates)]
    rows.extend(
        [
            history("progress", event_id="SYNTH_5", status="in_progress", completion_pct=95),
            history("dropped", event_id="SYNTH_6", status="dropped", completion_pct=95),
            history("other", event_id="SYNTH_7", employee_id="SYNTH_OTHER"),
        ]
    )
    original = deepcopy((events, rows))
    expected = {"SYNTH_PYTHON": 3.0, "SYNTH_TEAMWORK": 0.0}
    assert replay(employee(), events, rows, SKILL_IDS, DEMO_DATE) == expected
    assert replay(employee(), events, list(reversed(rows)), SKILL_IDS, DEMO_DATE) == expected
    assert (events, rows) == original


def test_replay_repeatable_mandatory_and_distinct_record_ids_are_never_deduplicated():
    source_event = event(mandatory=True)
    rows = [history(f"SYNTH_{index}") for index in range(3)]
    before = deepcopy(rows)
    assert (
        replay(employee(), {source_event["event_id"]: source_event}, rows, SKILL_IDS, DEMO_DATE)[
            "SYNTH_PYTHON"
        ]
        == 4
    )
    assert rows == before


def test_replay_keeps_same_tuple_different_record_ids_even_for_voluntary_source_history():
    # New requests enforce nonrepeatability; imported facts are keyed by record_id.
    rows = [history("SYNTH_A"), history("SYNTH_B")]
    assert replay(employee(), {"SYNTH_EVENT_1": event()}, rows, SKILL_IDS, DEMO_DATE)["SYNTH_PYTHON"] == 3


def test_replay_uses_chronological_order_with_record_id_tiebreaker():
    # Applying the lower cap first gives 4; applying it after the high cap gives 3.
    low = event("SYNTH_LOW", develops_skills=[{"skill_id": "SYNTH_PYTHON", "gain": 2, "max_level": 2}])
    high = event("SYNTH_HIGH", develops_skills=[{"skill_id": "SYNTH_PYTHON", "gain": 2, "max_level": 5}])
    rows = [history("B", event_id="SYNTH_HIGH"), history("A", event_id="SYNTH_LOW")]
    actual = replay(employee(), {"SYNTH_LOW": low, "SYNTH_HIGH": high}, rows, SKILL_IDS, DEMO_DATE)
    assert actual["SYNTH_PYTHON"] == 4


@pytest.mark.parametrize("later_timestamp", ["2026-10-01T11:00:00+05:00", "2026-10-01T06:00:00Z"])
def test_exact_same_day_completions_use_aware_time_before_random_record_id(later_timestamp):
    low = event("SYNTH_LOW", develops_skills=[{"skill_id": "SYNTH_PYTHON", "gain": 1, "max_level": 2}])
    high = event("SYNTH_HIGH", develops_skills=[{"skill_id": "SYNTH_PYTHON", "gain": 1, "max_level": 5}])
    rows = [
        history("A_SECOND", event_id="SYNTH_HIGH", date_source="completed_at", completed_at=later_timestamp),
        history(
            "Z_FIRST",
            event_id="SYNTH_LOW",
            date_source="completed_at",
            completed_at="2026-10-01T10:00:00+05:00",
        ),
    ]
    events = {"SYNTH_LOW": low, "SYNTH_HIGH": high}
    assert replay(employee(), events, rows, SKILL_IDS, DEMO_DATE)["SYNTH_PYTHON"] == 3
    assert replay(employee(), events, rows[::-1], SKILL_IDS, DEMO_DATE)["SYNTH_PYTHON"] == 3


def test_historical_proxies_on_same_day_precede_exact_completion_times():
    low = event("SYNTH_LOW", develops_skills=[{"skill_id": "SYNTH_PYTHON", "gain": 1, "max_level": 2}])
    high = event("SYNTH_HIGH", develops_skills=[{"skill_id": "SYNTH_PYTHON", "gain": 1, "max_level": 5}])
    rows = [
        history(
            "A_EXACT",
            event_id="SYNTH_HIGH",
            date_source="completed_at",
            completed_at="2026-10-01T00:00:00+05:00",
        ),
        history("Z_PROXY", event_id="SYNTH_LOW", date="2026-10-01"),
    ]
    assert (
        replay(employee(), {"SYNTH_LOW": low, "SYNTH_HIGH": high}, rows, SKILL_IDS, DEMO_DATE)["SYNTH_PYTHON"]
        == 3
    )


@pytest.mark.parametrize(
    ("before", "gain", "cap", "after"),
    [
        (1.0, 2.0, 4.0, 3.0),
        (3.5, 2.0, 4.0, 4.0),
        (4.5, 1.0, 4.0, 4.5),
        (4.8, 2.0, 5.0, 5.0),
        (5.0, 1.0, 2.0, 5.0),
        (0.0, 0.5, 3.0, 0.5),
        (2.0, 0.0, 3.0, 2.0),
    ],
)
def test_caps_respect_gain_event_cap_scale_and_never_lower_a_high_level(before, gain, cap, after):
    source_event = event(develops_skills=[{"skill_id": "SYNTH_PYTHON", "gain": gain, "max_level": cap}])
    levels, effects = apply_event({"SYNTH_PYTHON": before}, source_event)
    assert levels == {"SYNTH_PYTHON": after}
    assert effects[0]["delta"] == pytest.approx(after - before)
    assert SkillEffect.model_validate(effects[0]).after == after


def test_replay_missing_catalog_skill_is_zero_unknown_skill_is_error():
    assert replay(employee(), {}, [], SKILL_IDS, DEMO_DATE)["SYNTH_TEAMWORK"] == 0
    with pytest.raises(DomainError, match="unknown skill"):
        replay(employee(skills={"SYNTH_UNKNOWN": 2}), {}, [], SKILL_IDS, DEMO_DATE)
    with pytest.raises(DomainError, match="unknown skill"):
        apply_event({"SYNTH_TEAMWORK": 0.0}, event())


@pytest.mark.parametrize("value", [-1, 6, float("nan"), float("inf"), True, "2"])
def test_invalid_baseline_levels_are_not_silently_clamped(value):
    with pytest.raises(DomainError):
        replay(employee(skills={"SYNTH_PYTHON": value}), {}, [], SKILL_IDS, DEMO_DATE)


def test_negative_gain_and_duplicate_development_are_errors():
    with pytest.raises(DomainError):
        apply_event(
            {"SYNTH_PYTHON": 1},
            event(develops_skills=[{"skill_id": "SYNTH_PYTHON", "gain": -1, "max_level": 5}]),
        )
    development = {"skill_id": "SYNTH_PYTHON", "gain": 1, "max_level": 5}
    with pytest.raises(DomainError, match="same skill"):
        apply_event({"SYNTH_PYTHON": 1}, event(develops_skills=[development, development]))


def test_replay_cannot_reconstruct_a_date_before_assessment():
    with pytest.raises(DomainError, match="before their assessment"):
        replay(employee(), {}, [], SKILL_IDS, date(2026, 8, 31))


def test_replay_unknown_completed_event_is_error():
    with pytest.raises(DomainError, match="unknown event_id"):
        replay(employee(), {}, [history()], SKILL_IDS, DEMO_DATE)


def test_new_completion_uses_completed_at_and_preserves_audit_provenance():
    row = history(
        date="2026-08-01",
        date_source="completed_at",
        completed_at="2026-10-01T12:34:00+05:00",
        recorded_at="2026-09-23T07:34:00Z",
        effective_date="2026-10-01",
    )
    original = deepcopy(row)
    assert effective_date(row) == DEMO_DATE
    assert replay(employee(), {"SYNTH_EVENT_1": event()}, [row], SKILL_IDS, DEMO_DATE)["SYNTH_PYTHON"] == 2
    assert row == original
    with pytest.raises(DomainError, match="conflicts"):
        effective_date({**row, "effective_date": "2026-09-23"})
    with pytest.raises(DomainError, match="aware"):
        effective_date({**row, "completed_at": "2026-10-01T12:34:00"})


def test_historical_date_remains_an_explicit_proxy():
    row = history()
    assert effective_date(row) == date(2026, 9, 15)
    assert row["date_source"] == "historical_proxy"
    assert "completed_at" not in row


def test_goal_priority_next_grade_and_lead_without_goal():
    target = {"target_role": "Synthetic Analyst", "target_grade": "Senior"}
    assert resolve_goal(employee(career_goal=target), role_profiles()) == (target, "explicit")
    assert resolve_goal(employee(), role_profiles()) == (
        {"target_role": "Synthetic Engineer", "target_grade": "Middle"},
        "next_grade",
    )
    assert resolve_goal(employee(grade="Lead"), role_profiles()) == (None, "no_target")
    assert resolve_goal(employee(grade="Lead", career_goal=target), role_profiles()) == (target, "explicit")
    with pytest.raises(DomainError, match="no catalog profile"):
        resolve_goal(
            employee(career_goal={"target_role": "SYNTH_UNKNOWN", "target_grade": "Lead"}), role_profiles()
        )


def test_gaps_and_progress_use_required_skill_totals_without_extra_credit():
    goal, _ = resolve_goal(employee(), role_profiles())
    levels = {"SYNTH_PYTHON": 5, "SYNTH_TEAMWORK": 1}
    actual = gaps(levels, goal, role_profiles())
    assert actual == [{"skill_id": "SYNTH_TEAMWORK", "current_level": 1.0, "target_level": 3.0, "gap": 2.0}]
    assert SkillGap.model_validate(actual[0]).gap == 2
    assert progress(levels, goal, role_profiles()) == {
        "percent": 66.67,
        "met_skills": 1,
        "total_skills": 2,
        "critical_met": 1,
        "critical_total": 1,
    }
    assert progress(levels, None, role_profiles()) is None
    assert gaps(levels, None, role_profiles()) == []


def test_empty_or_zero_goal_requirements_have_no_progress_percentage():
    goal = {"target_role": "Synthetic Engineer", "target_grade": "Middle"}
    for requirements in ({}, {"SYNTH_PYTHON": 0}):
        profiles = [
            {
                "role": goal["target_role"],
                "grade": "Middle",
                "required_skills": requirements,
                "critical_skills": [],
            }
        ]
        assert progress({"SYNTH_PYTHON": 0}, goal, profiles) is None


def candidates(source_events=None, rows=(), **profile_overrides):
    profile = employee(**profile_overrides)
    levels = replay(profile, {}, [], SKILL_IDS, DEMO_DATE)
    goal, _ = resolve_goal(profile, role_profiles())
    source_events = source_events or {"SYNTH_EVENT_1": event()}
    return eligible_events(profile, levels, rows, source_events, DEMO_DATE, goal, role_profiles())


def test_eligibility_requires_useful_positive_effect_and_does_not_invent_events():
    result = candidates()
    assert len(result) == 1
    assert result[0]["gap_reduction"] == 1
    assert result[0]["effects"][0]["delta"] == 1
    assert candidates(skills={"SYNTH_PYTHON": 4, "SYNTH_TEAMWORK": 0}) == []
    assert candidates(grade="Lead") == []
    assert candidates({"SYNTH_EVENT_1": event(develops_skills=[])}) == []


def test_eligibility_uses_current_role_and_grade_not_career_goal():
    foreign_event = event(target_roles=["Synthetic Analyst"])
    assert (
        candidates(
            {"SYNTH_EVENT_1": foreign_event},
            career_goal={"target_role": "Synthetic Analyst", "target_grade": "Middle"},
        )
        == []
    )
    assert candidates({"SYNTH_EVENT_1": event(target_grades=["Senior", "Lead"])}) == []


def test_eligibility_checks_prerequisites_and_calendar_inclusive_cutoff():
    assert candidates({"SYNTH_EVENT_1": event(prerequisites={"SYNTH_TEAMWORK": 1})}) == []
    assert candidates({"SYNTH_EVENT_1": event(prerequisites={"SYNTH_PYTHON": 1})})
    assert candidates({"SYNTH_EVENT_1": event(format="online", upcoming_sessions=[])}) == []
    assert candidates({"SYNTH_EVENT_1": event(format="online", upcoming_sessions=["2026-09-30"])}) == []
    assert candidates({"SYNTH_EVENT_1": event(format="online", upcoming_sessions=["2026-10-01"])})
    assert candidates({"SYNTH_EVENT_1": event(format="online", upcoming_sessions=["2026-10-02"])})
    assert candidates({"SYNTH_EVENT_1": event(format="self_paced", upcoming_sessions=[])})


def test_completed_before_assessment_blocks_voluntary_recommendation_but_not_other_employee():
    assert candidates(rows=[history(date="2026-08-01")]) == []
    assert candidates(rows=[history(employee_id="SYNTH_OTHER")])
    assert candidates(rows=[history(date="2026-10-02")])


def test_mandatory_never_recommended_but_is_available_for_completion():
    mandatory = event(mandatory=True)
    assert candidates({"SYNTH_EVENT_1": mandatory}) == []
    assert is_available(employee(), {"SYNTH_PYTHON": 1, "SYNTH_TEAMWORK": 0}, mandatory, DEMO_DATE)


def test_active_participation_is_not_recommended_as_new_activity():
    assert candidates(rows=[history(status="in_progress", completion_pct=10)]) == []
    for status in ("dropped", "declined", "no_show", "overdue"):
        assert candidates(rows=[history(status=status)])


def test_ev036_can_repeat_but_not_the_same_completed_session():
    club = event("EV_036", format="online", upcoming_sessions=["2026-10-01"])
    old = history(event_id="EV_036", date="2026-09-01")
    today = history("SYNTH_TODAY", event_id="EV_036", date="2026-10-01")
    assert candidates({"EV_036": club}, rows=[old])
    assert candidates({"EV_036": club}, rows=[old, today]) == []
    assert candidates(
        {"EV_036": {**club, "upcoming_sessions": ["2026-10-01", "2026-10-08"]}}, rows=[old, today]
    )
    assert candidates({"EV_036": club}, rows=[history(event_id="EV_036", status="in_progress")]) == []


def test_unknown_prerequisite_or_goal_skill_is_error():
    with pytest.raises(DomainError, match="unknown skill"):
        candidates({"SYNTH_EVENT_1": event(prerequisites={"SYNTH_UNKNOWN": 1})})
    goal = {"target_role": "Synthetic Engineer", "target_grade": "Middle"}
    profiles = [
        {
            "role": "Synthetic Engineer",
            "grade": "Middle",
            "required_skills": {"SYNTH_UNKNOWN": 1},
            "critical_skills": [],
        }
    ]
    with pytest.raises(DomainError, match="unknown skill"):
        gaps({"SYNTH_PYTHON": 1}, goal, profiles)
