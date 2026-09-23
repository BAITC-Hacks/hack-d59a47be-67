"""Internal, verified kit-file schemas. HTTP and AI schemas live in contracts.py."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    model_validator,
)

Text = Annotated[str, Field(min_length=1, max_length=4096)]
Label = Annotated[str, Field(min_length=1, max_length=200)]
Identifier = Annotated[str, Field(min_length=1, max_length=128)]
Level = Annotated[StrictInt, Field(ge=0, le=5)]
Grade = Literal["Junior", "Middle", "Senior", "Lead"]


def _iso_date(value: object) -> date:
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("ISO calendar date required")
    result = date.fromisoformat(value)
    if result.isoformat() != value:
        raise ValueError("ISO calendar date required")
    return result


SourceDate = Annotated[date, BeforeValidator(_iso_date)]


class SourceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SourceMeta(SourceModel):
    dataset: Text
    version: Annotated[str, Field(min_length=1, max_length=80)]
    as_of_date: SourceDate


class SourceSkill(SourceModel):
    skill_id: Identifier
    name: Label
    type: Literal["hard", "soft"]
    category: Label
    description: Annotated[str, Field(max_length=1000)]


class SourceRoleProfile(SourceModel):
    role: Identifier
    grade: Grade
    required_skills: dict[Identifier, Level]
    critical_skills: list[Identifier]


class SourceSkills(SourceModel):
    meta: SourceMeta
    proficiency_scale: dict[str, Text]
    skills: Annotated[list[SourceSkill], Field(min_length=1)]
    role_profiles: Annotated[list[SourceRoleProfile], Field(min_length=1)]

    @model_validator(mode="after")
    def scale(self) -> SourceSkills:
        if set(self.proficiency_scale) != {str(level) for level in range(6)}:
            raise ValueError("Proficiency scale must describe levels 0 through 5")
        return self


class SourceGoal(SourceModel):
    target_role: Identifier
    target_grade: Grade


class SourceEmployee(SourceModel):
    employee_id: Identifier
    full_name: Label
    department: Label
    role: Identifier
    grade: Grade
    manager_id: Identifier | None
    hire_date: SourceDate
    tenure_months: Annotated[StrictInt, Field(ge=0)]
    work_format: Literal["office", "hybrid", "remote"]
    preferred_language: Literal["kk", "ru", "en"]
    career_goal: SourceGoal | None
    skills: dict[Identifier, Level]
    last_review_date: SourceDate


class SourceEmployees(SourceModel):
    meta: SourceMeta
    employees: list[SourceEmployee]


class SourceEffect(SourceModel):
    skill_id: Identifier
    gain: Annotated[StrictInt, Field(ge=0, le=5)]
    max_level: Level


class SourceEvent(SourceModel):
    event_id: Identifier
    title: Label
    description: Annotated[str, Field(max_length=4000)]
    type: Literal["compliance", "onboarding", "course", "workshop", "mentoring", "certification", "meetup"]
    format: Literal["online", "offline", "self_paced"]
    duration_hours: Annotated[StrictFloat, Field(gt=0)]
    mandatory: StrictBool
    target_roles: list[Identifier]
    target_grades: list[Grade]
    develops_skills: list[SourceEffect]
    prerequisites: dict[Identifier, Level]
    upcoming_sessions: list[SourceDate]


class SourceEvents(SourceModel):
    meta: SourceMeta
    events: list[SourceEvent]


class SourceHistory(SourceModel):
    record_id: Identifier
    employee_id: Identifier
    event_id: Identifier
    date: SourceDate
    due_date: SourceDate | None
    status: Literal["completed", "in_progress", "dropped", "no_show", "declined", "overdue"]
    completion_pct: Annotated[StrictInt, Field(ge=0, le=100)]
    score: Annotated[StrictInt, Field(ge=0, le=100)] | None
    feedback_rating: Annotated[StrictInt, Field(ge=1, le=5)] | None
    assigned_by: Literal["self", "manager", "hr"]

    @model_validator(mode="after")
    def status_progress(self) -> SourceHistory:
        lower, upper = {
            "completed": (100, 100),
            "in_progress": (0, 95),
            "dropped": (5, 95),
            "no_show": (0, 0),
            "declined": (0, 0),
            "overdue": (0, 95),
        }[self.status]
        if not lower <= self.completion_pct <= upper:
            raise ValueError("Progress does not match history status")
        return self
