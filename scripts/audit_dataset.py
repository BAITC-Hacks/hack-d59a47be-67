#!/usr/bin/env python3
"""Reproducible, aggregate-only audit of the local Career Quest starter kit.

This is an audit utility, not the application's importer or recommendation service.
The imported history has no completion timestamp; reconstruction uses `date` as
an explicitly documented proxy, never as evidence of on-time completion.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date
import hashlib
import json
import math
from pathlib import Path

GRADES = ("Junior", "Middle", "Senior", "Lead")
STATUSES = {"completed", "in_progress", "dropped", "no_show", "declined", "overdue"}
CSV_FIELDS = ["record_id", "employee_id", "event_id", "date", "due_date", "status",
              "completion_pct", "score", "feedback_rating", "assigned_by"]
REPEATABLE_EVENTS = {"EV_036"}  # Starter-kit README rule, not inferred from duplicates.


def is_number(value):
    return type(value) is int or type(value) is float and math.isfinite(value)


def load_json(contents):
    """Reject ambiguous duplicate keys and non-standard JSON numeric literals."""
    def reject_constant(value):
        raise ValueError(f"Non-standard JSON numeric value: {value}")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(contents, parse_constant=reject_constant, object_pairs_hook=unique_object)


def is_level(value):
    return type(value) is int and 0 <= value <= 5


def is_date(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def count_duplicates(rows, fields):
    return sum(n - 1 for n in Counter(tuple(row.get(f) for f in fields) for row in rows).values())


def schema(rows):
    result = {}
    for key in sorted({key for row in rows for key in row}):
        values = [row.get(key) for row in rows]
        result[key] = {"types": sorted({type(v).__name__ for v in values}),
                       "null": sum(v is None for v in values),
                       "empty_string": sum(v == "" for v in values),
                       "missing": sum(key not in row for row in rows)}
    return result


def reconstruct_skills(employee, history, events_by_id, as_of_date):
    """Replay eligible completions after review once; caller validates unique IDs.

    For imported rows only, date is a completion-date proxy. A baseline above a
    course cap must never be reduced. Missing skills are zero.
    """
    levels = dict(employee["skills"])
    replayed = zero_effect = above_cap = changes = 0
    for row in sorted(history, key=lambda item: (item["date"], item["record_id"])):
        if row["employee_id"] != employee["employee_id"] or row["status"] != "completed":
            continue
        if not employee["last_review_date"] < row["date"] <= as_of_date:
            continue
        replayed += 1
        changed = False
        for gain in events_by_id[row["event_id"]]["develops_skills"]:
            key = gain["skill_id"]
            before = levels.get(key, 0)
            above_cap += before > gain["max_level"]
            after = max(before, min(5, gain["max_level"], before + gain["gain"]))
            levels[key] = after
            changed |= after > before
            changes += after > before
        zero_effect += not changed
    return levels, {"replayed_completions": replayed, "zero_effect_completions": zero_effect,
                    "skill_updates": changes, "baseline_above_cap_updates": above_cap}


def candidate_reasons(employee, event, current, history, as_of_date, include_goal_role=False):
    """Catalog hard filters from architecture; policy, not source facts.

    include_goal_role is only a sensitivity check, never the accepted policy.
    """
    reasons = []
    if event["mandatory"]:
        reasons.append("mandatory")
    roles = {employee["role"]}
    if include_goal_role and employee["career_goal"]:
        roles.add(employee["career_goal"]["target_role"])
    if not roles.intersection(event["target_roles"]):
        reasons.append("role")
    if employee["grade"] not in event["target_grades"]:
        reasons.append("grade")
    if any(current.get(k, 0) < v for k, v in event["prerequisites"].items()):
        reasons.append("prerequisites")
    if event["format"] != "self_paced" and not any(day >= as_of_date for day in event["upcoming_sessions"]):
        reasons.append("no_future_session")
    own = [row for row in history if row["event_id"] == event["event_id"] and row["date"] <= as_of_date]
    if event["event_id"] not in REPEATABLE_EVENTS and any(row["status"] == "completed" for row in own):
        reasons.append("already_completed")
    if any(row["status"] == "in_progress" for row in own):
        reasons.append("in_progress")
    return reasons


def select_target(employee, profiles_by_key):
    """Accepted architecture: explicit goal, next grade, or no target for Lead."""
    goal = employee["career_goal"]
    if goal:
        return profiles_by_key[goal["target_role"], goal["target_grade"]]
    if employee["grade"] == "Lead":
        return None
    next_grade = GRADES[GRADES.index(employee["grade"]) + 1]
    return profiles_by_key[employee["role"], next_grade]


def audit(raw: Path):
    blobs = {name: (raw / name).read_bytes() for name in
             ("employees.json", "events.json", "skills.json", "activity_history.csv")}
    docs = {name: load_json(blob) for name, blob in blobs.items() if name.endswith(".json")}
    employees = docs["employees.json"]["employees"]
    events = docs["events.json"]["events"]
    skills = docs["skills.json"]["skills"]
    profiles = docs["skills.json"]["role_profiles"]
    history_reader = csv.DictReader(blobs["activity_history.csv"].decode("utf-8-sig").splitlines())
    history = list(history_reader)
    violations = Counter()
    checks = Counter()

    def check(name, passed):
        checks[name] += 1
        violations[name] += not bool(passed)

    as_of = docs["employees.json"]["meta"]["as_of_date"]
    check("as_of_date_valid", is_date(as_of))
    check("metadata_consistent", all(d["meta"] == docs["employees.json"]["meta"] for d in docs.values()))
    check("csv_header", history_reader.fieldnames == CSV_FIELDS)
    tables = {"employees": employees, "events": events, "skills": skills,
              "role_profiles": profiles, "activity_history": history}
    report = {"as_of_date": as_of,
              "source_sha256": {name: hashlib.sha256(blob).hexdigest() for name, blob in blobs.items()},
              "counts": {name: len(rows) for name, rows in tables.items()},
              "schema": {name: schema(rows) for name, rows in tables.items()},
              "duplicates": {}, "checks": checks, "violations": violations}
    for name, key in (("employees", ["employee_id"]), ("events", ["event_id"]),
                      ("skills", ["skill_id"]), ("role_profiles", ["role", "grade"]),
                      ("activity_history", ["record_id"])):
        report["duplicates"][name] = {
            "key": count_duplicates(tables[name], key),
            "exact": len(tables[name]) - len({json.dumps(row, sort_keys=True) for row in tables[name]})}
        check(f"{name}_unique_key", not report["duplicates"][name]["key"])
    report["duplicates"]["history_employee_event_date"] = count_duplicates(history, ["employee_id", "event_id", "date"])
    skill_ids = {s["skill_id"] for s in skills}
    employee_by_id = {e["employee_id"]: e for e in employees}
    event_by_id = {e["event_id"]: e for e in events}
    profile_by_key = {(p["role"], p["grade"]): p for p in profiles}
    roles = {p["role"] for p in profiles}
    check("proficiency_scale", set(docs["skills.json"]["proficiency_scale"]) == set(map(str, range(6))))
    for skill in skills:
        check("skill_string_fields", all(isinstance(skill.get(k), str) and skill[k].strip() for k in ("skill_id", "name", "category", "description")))
        check("skill_type", skill["type"] in ("hard", "soft"))
    for profile in profiles:
        check("profile_grade", profile["grade"] in GRADES)
        check("profile_skill_refs", set(profile["required_skills"]) <= skill_ids)
        check("profile_skill_levels", all(is_level(v) for v in profile["required_skills"].values()))
        check("critical_subset", set(profile["critical_skills"]) <= set(profile["required_skills"]))
        check("critical_unique", len(profile["critical_skills"]) == len(set(profile["critical_skills"])))
    for role in roles:
        check("four_grades_per_role", all((role, grade) in profile_by_key for grade in GRADES))
        for before, after in zip(GRADES, GRADES[1:]):
            if (role, before) in profile_by_key and (role, after) in profile_by_key:
                previous = profile_by_key[role, before]["required_skills"]
                following = profile_by_key[role, after]["required_skills"]
                check("requirements_monotonic", all(following.get(k, 0) >= v for k, v in previous.items()))
    for employee in employees:
        check("employee_string_fields", all(isinstance(employee.get(k), str) and employee[k].strip() for k in ("employee_id", "full_name", "department", "role", "grade")))
        check("employee_role_grade", (employee["role"], employee["grade"]) in profile_by_key)
        check("employee_skill_refs", set(employee["skills"]) <= skill_ids)
        check("employee_skill_levels", all(is_level(v) for v in employee["skills"].values()))
        check("employee_work_format", employee["work_format"] in ("office", "hybrid", "remote"))
        check("employee_language", employee["preferred_language"] in ("ru", "en", "kk"))
        check("employee_tenure_type", type(employee["tenure_months"]) is int and employee["tenure_months"] >= 0)
        valid_dates = is_date(employee["hire_date"]) and is_date(employee["last_review_date"])
        check("employee_dates", valid_dates)
        if valid_dates:
            check("employee_date_order", employee["hire_date"] <= employee["last_review_date"] <= as_of)
            hired, today = date.fromisoformat(employee["hire_date"]), date.fromisoformat(as_of)
            tenure = (today.year - hired.year) * 12 + today.month - hired.month - (today.day < hired.day)
            check("tenure_matches_hire", employee["tenure_months"] == tenure)
        manager = employee["manager_id"]
        check("manager_ref", manager is None or manager in employee_by_id)
        if manager in employee_by_id:
            boss = employee_by_id[manager]
            check("manager_lead_department", boss["grade"] == "Lead" and boss["department"] == employee["department"] and manager != employee["employee_id"])
        check("null_manager_is_lead", manager is not None or employee["grade"] == "Lead")
        goal = employee["career_goal"]
        check("career_goal_shape", goal is None or isinstance(goal, dict) and set(goal) == {"target_role", "target_grade"})
        check("career_goal_ref", goal is None or (goal.get("target_role"), goal.get("target_grade")) in profile_by_key)
    for event in events:
        check("event_string_fields", all(isinstance(event.get(k), str) and event[k].strip() for k in ("event_id", "title", "description")))
        check("event_type", event["type"] in ("compliance", "onboarding", "course", "workshop", "mentoring", "certification", "meetup"))
        check("event_format", event["format"] in ("online", "offline", "self_paced"))
        check("event_mandatory_bool", type(event["mandatory"]) is bool)
        check("event_duration", is_number(event["duration_hours"]) and event["duration_hours"] > 0)
        check("event_roles", bool(event["target_roles"]) and set(event["target_roles"]) <= roles)
        check("event_grades", bool(event["target_grades"]) and set(event["target_grades"]) <= set(GRADES))
        check("event_prerequisite_refs", set(event["prerequisites"]) <= skill_ids)
        check("event_prerequisite_levels", all(is_level(v) for v in event["prerequisites"].values()))
        check("event_developed_unique", len(event["develops_skills"]) == len({g["skill_id"] for g in event["develops_skills"]}))
        for gain in event["develops_skills"]:
            check("growth_shape", set(gain) == {"skill_id", "gain", "max_level"})
            check("growth_skill_ref", gain["skill_id"] in skill_ids)
            check("growth_gain", type(gain["gain"]) is int and gain["gain"] >= 0)
            check("growth_cap", is_level(gain["max_level"]))
        check("event_session_dates", all(is_date(d) for d in event["upcoming_sessions"]))
        check("event_sessions_future", all(d >= as_of for d in event["upcoming_sessions"]))
        check("event_sessions_unique", len(event["upcoming_sessions"]) == len(set(event["upcoming_sessions"])))
        check("self_paced_empty_sessions", event["format"] != "self_paced" or not event["upcoming_sessions"])
    history_by_employee = defaultdict(list)
    missing_scores = Counter()
    history_sorted = sorted(history, key=lambda r: (r["date"], r["employee_id"], r["event_id"]))
    check("history_sorted", history == history_sorted)
    completed_pairs = set()
    mandatory_repeats = Counter()
    mandatory_repeat_statuses = Counter()
    for row in history_sorted:
        check("history_columns", set(row) == set(CSV_FIELDS) and all(isinstance(v, str) for v in row.values()))
        check("history_employee_ref", row["employee_id"] in employee_by_id)
        check("history_event_ref", row["event_id"] in event_by_id)
        check("history_date", is_date(row["date"]))
        check("history_due_date", not row["due_date"] or is_date(row["due_date"]))
        check("history_status", row["status"] in STATUSES)
        check("history_assigned_by", row["assigned_by"] in ("self", "manager", "hr"))
        for field, lo, hi, optional in (("completion_pct", 0, 100, False), ("score", 0, 100, True), ("feedback_rating", 1, 5, True)):
            value = row[field]
            check(f"history_{field}_range", optional and value == "" or value.isdecimal() and lo <= int(value) <= hi)
        if row["completion_pct"].isdecimal():
            pct = int(row["completion_pct"])
            bounds = {"completed": (100, 100), "in_progress": (0, 95), "dropped": (5, 95), "no_show": (0, 0), "declined": (0, 0), "overdue": (0, 95)}
            lo, hi = bounds.get(row["status"], (-1, -1))
            check("status_completion_consistency", lo <= pct <= hi)
        if row["employee_id"] not in employee_by_id or row["event_id"] not in event_by_id:
            continue
        employee, event = employee_by_id[row["employee_id"]], event_by_id[row["event_id"]]
        history_by_employee[row["employee_id"]].append(row)
        check("history_after_hire", row["date"] >= employee["hire_date"])
        check("history_before_snapshot", row["date"] < as_of)
        check("due_date_only_mandatory", not row["due_date"] or event["mandatory"])
        check("mandatory_has_due_date", not event["mandatory"] or bool(row["due_date"]))
        check("due_after_start", not row["due_date"] or row["due_date"] >= row["date"])
        check("no_show_scheduled", row["status"] != "no_show" or event["format"] != "self_paced")
        check("declined_assigned", row["status"] != "declined" or row["assigned_by"] in ("manager", "hr"))
        check("overdue_consistency", row["status"] != "overdue" or event["mandatory"] and bool(row["due_date"]) and row["due_date"] < as_of)
        check("score_event_type", not row["score"] or event["type"] in ("course", "certification", "compliance"))
        check("score_only_completed", not row["score"] or row["status"] == "completed")
        if row["status"] == "completed" and event["type"] in ("course", "certification", "compliance") and not row["score"]:
            missing_scores[event["type"]] += 1
        pair = row["employee_id"], row["event_id"]
        # The README's general no-repeat rule conflicts with annual mandatory
        # training in this kit. Report that contradiction, without treating
        # repeated compliance assignments as duplicate data or new skill gains.
        check("no_voluntary_attempt_after_completed", event["mandatory"] or event["event_id"] in REPEATABLE_EVENTS or pair not in completed_pairs)
        if event["mandatory"] and pair in completed_pairs:
            mandatory_repeats[event["event_id"]] += 1
            mandatory_repeat_statuses[row["status"]] += 1
        if row["status"] == "completed":
            completed_pairs.add(pair)
    report["violations"] = {k: v for k, v in sorted(violations.items()) if v}
    report["checks"] = dict(sorted(checks.items()))
    # Bad source values must not flow into computed career metrics.
    if report["violations"]:
        report["derived_metrics_skipped"] = True
        return report
    report["distribution"] = {
        "grades": dict(Counter(e["grade"] for e in employees)),
        "roles": dict(Counter(e["role"] for e in employees)),
        "skill_types": dict(Counter(s["type"] for s in skills)),
        "event_types": dict(Counter(e["type"] for e in events)),
        "event_formats": dict(Counter(e["format"] for e in events)),
        "mandatory_events": sum(e["mandatory"] for e in events),
        "statuses": dict(Counter(r["status"] for r in history)),
        "assigned_by": dict(Counter(r["assigned_by"] for r in history)),
        "career_goals": dict(Counter("none" if not e["career_goal"] else "same_role" if e["career_goal"]["target_role"] == e["role"] else "cross_role" for e in employees)),
        "completed_without_expected_score": dict(missing_scores),
        "mandatory_attempts_after_prior_completion": dict(mandatory_repeats),
        "mandatory_repeat_statuses": dict(mandatory_repeat_statuses),
        "history_months": dict(sorted(Counter(r["date"][:7] for r in history).items())),
        "history_status_by_mandatory": {
            kind: dict(Counter(r["status"] for r in history if event_by_id[r["event_id"]]["mandatory"] == mandatory))
            for kind, mandatory in (("mandatory", True), ("voluntary", False))},
        "history_months_by_mandatory": {
            kind: dict(sorted(Counter(r["date"][:7] for r in history if event_by_id[r["event_id"]]["mandatory"] == mandatory).items()))
            for kind, mandatory in (("mandatory", True), ("voluntary", False))},
    }
    report["history_measures"] = {
        field: {"populated": sum(bool(r[field]) for r in history),
                "empty": sum(not r[field] for r in history),
                "min": min((int(r[field]) for r in history if r[field]), default=None),
                "max": max((int(r[field]) for r in history if r[field]), default=None)}
        for field in ("completion_pct", "score", "feedback_rating")}
    report["history_measures"]["due_date_populated"] = sum(bool(r["due_date"]) for r in history)
    report["dates"] = {"history": [min(r["date"] for r in history), max(r["date"] for r in history)],
                       "reviews": [min(e["last_review_date"] for e in employees), max(e["last_review_date"] for e in employees)],
                       "hire": [min(e["hire_date"] for e in employees), max(e["hire_date"] for e in employees)],
                       "sessions": [min(d for e in events for d in e["upcoming_sessions"]), max(d for e in events for d in e["upcoming_sessions"])]}
    developed = {g["skill_id"] for event in events for g in event["develops_skills"]}
    max_cap = {key: max((g["max_level"] for event in events if not event["mandatory"] for g in event["develops_skills"] if g["skill_id"] == key), default=0) for key in skill_ids}
    report["catalog"] = {
        "undeveloped_skills": sorted(skill_ids - developed),
        "zero_declared_gain": sum(g["gain"] == 0 for e in events for g in e["develops_skills"]),
        "growth_rules": sum(len(e["develops_skills"]) for e in events),
        "empty_growth_events": sum(not e["develops_skills"] for e in events),
        "scheduled_without_future_session": sum(e["format"] != "self_paced" and not any(d >= as_of for d in e["upcoming_sessions"]) for e in events),
        "requirements_above_catalog_cap": sum(level > max_cap[key] for p in profiles for key, level in p["required_skills"].items()),
        "critical_requirements_above_catalog_cap": sum(p["required_skills"][key] > max_cap[key] for p in profiles for key in p["critical_skills"]),
        "skills_with_requirements_above_catalog_cap": sorted({key for p in profiles for key, level in p["required_skills"].items() if level > max_cap[key]}),
    }
    replay = Counter()
    counters = Counter()
    exclusion_counts = Counter()
    eligible_counts = Counter()
    useful_counts = Counter()
    positive_counts = Counter()
    gap_skills = Counter()
    uncovered_gap_skills = Counter()
    planning_states = Counter({"no_target": 0, "target_satisfied": 0,
                               "no_candidates": 0, "actionable": 0})
    for employee in employees:
        own = history_by_employee[employee["employee_id"]]
        current, stats = reconstruct_skills(employee, own, event_by_id, as_of)
        replay.update(stats)
        replay["employees_with_replayed_completion"] += stats["replayed_completions"] > 0
        replay["employees_with_skill_change"] += current != employee["skills"] and any(current[k] != employee["skills"].get(k, 0) for k in current)
        replay["completed_on_review_date"] += sum(r["status"] == "completed" and r["date"] == employee["last_review_date"] for r in own)
        replay["self_paced_completions_after_review_proxy"] += sum(r["status"] == "completed" and r["date"] > employee["last_review_date"] and event_by_id[r["event_id"]]["format"] == "self_paced" for r in own)
        target = select_target(employee, profile_by_key)
        requirements = target["required_skills"] if target is not None else {}
        gaps = {k: v - current.get(k, 0) for k, v in requirements.items() if v > current.get(k, 0)}
        gap_skills.update(gaps.keys())
        candidates = useful = positive = 0
        goal_union_candidates = 0
        covered = set()
        for event in events:
            reasons = candidate_reasons(employee, event, current, own, as_of)
            exclusion_counts.update(reasons)
            if not candidate_reasons(employee, event, current, own, as_of, include_goal_role=True):
                goal_union_candidates += 1
            if reasons:
                continue
            candidates += 1
            deltas = {g["skill_id"]: max(0, min(g["gain"], g["max_level"] - current.get(g["skill_id"], 0), 5 - current.get(g["skill_id"], 0))) for g in event["develops_skills"]}
            effective = {k for k, value in deltas.items() if value > 0}
            positive += bool(effective)
            useful += bool(effective & set(gaps))
            covered.update(effective & set(gaps))
        eligible_counts[candidates] += 1
        positive_counts[positive] += 1
        if target is not None:
            useful_counts[useful] += 1
        state = ("no_target" if target is None else "target_satisfied" if not gaps
                 else "no_candidates" if useful == 0 else "actionable")
        planning_states[state] += 1
        counters["employee_event_eligible_pairs"] += candidates
        counters["employee_event_zero_effect_pairs"] += candidates - positive
        counters["employee_event_target_gap_pairs"] += useful
        counters["employees_with_no_candidate"] += candidates == 0
        counters["employees_with_no_positive_gain_candidate"] += positive == 0
        counters["employees_with_gaps_but_no_gap_candidate"] += bool(gaps) and useful == 0
        counters["employees_with_no_target_gap"] += target is not None and not bool(gaps)
        counters["employees_with_target"] += target is not None
        counters["employees_with_at_least_one_uncovered_gap"] += bool(set(gaps) - covered)
        counters["employees_whose_candidates_expand_for_goal_role"] += goal_union_candidates > candidates
        counters["employees_with_history"] += bool(own)
        counters["completed_before_or_on_review"] += sum(r["status"] == "completed" and r["date"] <= employee["last_review_date"] for r in own)
        uncovered_gap_skills.update(set(gaps) - covered)
    report["reconstruction_date_proxy"] = dict(replay)
    report["availability_current_role_policy"] = {
        **dict(counters), "eligible_count_distribution": dict(sorted(eligible_counts.items())),
        "planning_states": dict(planning_states),
        "positive_gain_count_distribution": dict(sorted(positive_counts.items())),
        "gap_reducing_count_distribution": dict(sorted(useful_counts.items())),
        "nonexclusive_exclusion_counts": dict(exclusion_counts),
        "employees_with_gap_by_skill": dict(gap_skills.most_common()),
        "employees_with_uncovered_gap_by_skill": dict(uncovered_gap_skills.most_common()),
    }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", nargs="?", type=Path, default=Path("data/raw"))
    args = parser.parse_args()
    try:
        report = audit(args.raw)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        parser.exit(2, f"Dataset cannot be audited (invalid/missing input): {error}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if report["violations"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
