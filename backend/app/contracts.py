"""The single v1 schema source for the API and Oleg's recommendation boundary.

These are team-owned API contracts, informed by the inspected source README.
The source dataset is not bundled. Transport models do not replace source validation.
The AI module must import these models instead of defining competing versions.
"""

from datetime import date
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    model_validator,
)

Identifier = Annotated[str, Field(min_length=1, max_length=128)]
Version = Annotated[str, Field(min_length=1, max_length=128)]
Score = Annotated[float, Field(ge=0, le=5, allow_inf_nan=False)]
Grade = Literal["Junior", "Middle", "Senior", "Lead"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AICapability(ContractModel):
    status: Literal["not_configured", "configured"]
    engine: Literal["none", "oleg"]

    @model_validator(mode="after")
    def consistent_engine(self) -> "AICapability":
        if (self.status == "not_configured") != (self.engine == "none"):
            raise ValueError("AI status and engine must agree")
        return self


class DatasetCapability(ContractModel):
    status: Literal["not_loaded", "loaded"]
    version: Version | None = None

    @model_validator(mode="after")
    def consistent_version(self) -> "DatasetCapability":
        if (self.status == "not_loaded") != (self.version is None):
            raise ValueError("Dataset status and version must agree")
        return self


class Capabilities(ContractModel):
    ai: AICapability
    dataset: DatasetCapability


class HealthResponse(ContractModel):
    status: Literal["ok"] = "ok"
    capabilities: Capabilities


class ReadinessResponse(ContractModel):
    status: Literal["ready", "not_ready"]
    database: Literal["ready", "unavailable", "migration_required"]
    capabilities: Capabilities

    @model_validator(mode="after")
    def consistent_readiness(self) -> "ReadinessResponse":
        if (self.status == "ready") != (self.database == "ready"):
            raise ValueError("Readiness status must reflect database readiness")
        return self


class VersionResponse(ContractModel):
    api_version: Literal["1.0.0"] = "1.0.0"
    commit_sha: Annotated[str, Field(pattern=r"^(unknown|[0-9a-f]{7,64})$")]


class ErrorResponse(ContractModel):
    code: Annotated[str, Field(min_length=1, max_length=80)]
    message: Annotated[str, Field(min_length=1, max_length=240)]
    request_id: Annotated[str, Field(min_length=1, max_length=128)]
    details: dict[str, JsonValue] = Field(default_factory=dict)


class LoginRequest(ContractModel):
    username: Annotated[str, Field(min_length=1, max_length=80)]
    password: SecretStr = Field(min_length=1, max_length=256)


class UserIdentity(ContractModel):
    id: Identifier
    username: Annotated[str, Field(min_length=1, max_length=80)]
    role: Literal["employee", "hr"]
    employee_id: Identifier | None

    @model_validator(mode="after")
    def employee_binding(self) -> "UserIdentity":
        if self.role == "employee" and self.employee_id is None:
            raise ValueError("Employee accounts require a server-side employee binding")
        return self


class SessionResponse(ContractModel):
    user: UserIdentity
    csrf_token: Annotated[str, Field(min_length=1, max_length=256)]
    expires_at: AwareDatetime = Field(strict=False)


class PublicDemoConfig(ContractModel):
    enabled: bool
    session_ttl_seconds: int
    ai_enabled: bool


class PublicSessionRequest(ContractModel):
    role: Literal["employee", "hr"]


class LogoutResponse(ContractModel):
    status: Literal["logged_out"] = "logged_out"


class SkillLevel(ContractModel):
    skill_id: Identifier
    level: Score


class SkillGap(ContractModel):
    skill_id: Identifier
    current_level: Score
    target_level: Score
    gap: Score


class SkillEffect(ContractModel):
    skill_id: Identifier
    before: Score
    after: Score
    delta: Annotated[float, Field(ge=-5, le=5, allow_inf_nan=False)]


class Goal(ContractModel):
    target_role: Identifier
    target_grade: Grade


class CatalogSkill(ContractModel):
    skill_id: Identifier
    name: Annotated[str, Field(min_length=1, max_length=200)]
    type: Literal["hard", "soft"]
    category: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str, Field(max_length=1000)]


class CatalogRole(ContractModel):
    role: Identifier
    grade: Grade
    required_skills: dict[Identifier, Score]
    critical_skills: list[Identifier]


class CatalogSkillGain(ContractModel):
    skill_id: Identifier
    gain: Score
    max_level: Score


class CatalogEvent(ContractModel):
    event_id: Identifier
    title: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str, Field(max_length=4000)]
    type: Literal["compliance", "onboarding", "course", "workshop", "mentoring", "certification", "meetup"]
    format: Literal["online", "offline", "self_paced"]
    duration_hours: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    mandatory: bool
    target_roles: list[Identifier]
    target_grades: list[Grade]
    develops_skills: list[CatalogSkillGain]
    prerequisites: dict[Identifier, Score]
    upcoming_sessions: list[Annotated[date, Field(strict=False)]]


class CatalogResponse(ContractModel):
    data_version: Version
    proficiency_scale: dict[str, str]
    skills: list[CatalogSkill]
    role_profiles: list[CatalogRole]
    events: list[CatalogEvent] = Field(default_factory=list)


class EmployeeProfile(ContractModel):
    employee_id: Identifier
    full_name: Annotated[str, Field(min_length=1, max_length=200)]
    department: Annotated[str, Field(min_length=1, max_length=200)]
    role: Identifier
    grade: Grade


class EmployeeListResponse(ContractModel):
    items: list[EmployeeProfile]
    total: Annotated[int, Field(ge=0)]
    limit: Annotated[int, Field(ge=1, le=100)]
    offset: Annotated[int, Field(ge=0)]


class CompletionRecord(ContractModel):
    completion_id: Identifier
    event_id: Identifier
    mode: Literal["completion", "demo_simulation"]
    completed_at: AwareDatetime = Field(strict=False)
    effects: list[SkillEffect]


class ActivityRecord(ContractModel):
    record_id: Identifier
    event_id: Identifier
    status: Literal["completed", "in_progress", "dropped", "no_show", "declined", "overdue"]
    activity_date: date = Field(strict=False)
    date_source: Literal["historical_proxy", "completed_at"]
    completed_at: AwareDatetime | None = Field(default=None, strict=False)
    mode: Literal["import", "completion", "demo_simulation"] = "import"
    effects: list[SkillEffect] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_date_source(self) -> "ActivityRecord":
        if (self.date_source == "completed_at") != (self.completed_at is not None):
            raise ValueError("Exact completion timestamps require date_source=completed_at")
        return self


class Progress(ContractModel):
    percent: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
    met_skills: Annotated[int, Field(ge=0)]
    total_skills: Annotated[int, Field(ge=0)]
    critical_met: Annotated[int, Field(ge=0)]
    critical_total: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def consistent_counts(self) -> "Progress":
        if self.met_skills > self.total_skills or self.critical_met > self.critical_total:
            raise ValueError("Met counts cannot exceed their totals")
        if self.critical_total > self.total_skills or self.critical_met > self.met_skills:
            raise ValueError("Critical counts must be subsets of skill counts")
        return self


class EmployeeDetailResponse(ContractModel):
    data_version: Version
    state_version: Annotated[int, Field(ge=0)]
    profile: EmployeeProfile
    goal: Goal | None
    current_skills: list[SkillLevel]
    gaps: list[SkillGap]
    history: list[ActivityRecord]
    scenario_date: date = Field(default=date(2026, 10, 1), strict=False)
    target_status: Literal["explicit", "next_grade", "no_target"] = "explicit"
    progress: Progress | None = None


class GoalUpdateRequest(ContractModel):
    expected_state_version: Annotated[int, Field(ge=0)]
    goal: Goal


class RecommendationRequest(ContractModel):
    scenario_date: date = Field(strict=False)
    limit: Annotated[int, Field(ge=1, le=3)] = 3


class Explanation(ContractModel):
    text: Annotated[str, Field(min_length=1, max_length=2000)]
    evidence_ids: Annotated[list[Identifier], Field(min_length=1, max_length=50)]


class RecommendationSelection(ContractModel):
    event_id: Identifier
    explanation: Explanation


class RecommendationResult(ContractModel):
    status: Literal["ok", "no_candidates", "not_configured", "unavailable"]
    engine: Literal["none", "oleg"]
    recommendations: Annotated[list[RecommendationSelection], Field(max_length=3)]

    @model_validator(mode="after")
    def consistent_result(self) -> "RecommendationResult":
        if (self.status == "not_configured") != (self.engine == "none"):
            raise ValueError("Only not_configured uses engine=none")
        if self.status == "ok" and not self.recommendations:
            raise ValueError("ok requires at least one recommendation")
        if self.status != "ok" and self.recommendations:
            raise ValueError("Non-ok results cannot contain recommendations")
        event_ids = [item.event_id for item in self.recommendations]
        if len(set(event_ids)) != len(event_ids):
            raise ValueError("Selected event_id values must be unique")
        return self


class AnonymizedProfile(ContractModel):
    profile_ref: Identifier
    role: Identifier
    grade: Grade


class EvidenceFact(ContractModel):
    evidence_id: Identifier
    kind: Literal["skill", "gap", "goal", "candidate", "history"]
    subject_id: Identifier
    fact: Annotated[str, Field(min_length=1, max_length=2000)]


class EligibleCandidate(ContractModel):
    event_id: Identifier
    title: Annotated[str, Field(min_length=1, max_length=200)]
    effects: list[SkillEffect]
    evidence_ids: Annotated[list[Identifier], Field(min_length=1, max_length=50)]


class RecommendationContext(ContractModel):
    contract_version: Literal["1"] = "1"
    data_version: Version
    state_version: Annotated[int, Field(ge=0)]
    scenario_date: date = Field(strict=False)
    profile: AnonymizedProfile
    goal: Goal
    current_skills: list[SkillLevel]
    gaps: list[SkillGap]
    eligible_candidates: list[EligibleCandidate]
    facts: list[EvidenceFact]
    limit: Annotated[int, Field(ge=1, le=3)] = 3

    @model_validator(mode="after")
    def consistent_references(self) -> "RecommendationContext":
        evidence_ids = [fact.evidence_id for fact in self.facts]
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("evidence_id values must be unique")
        candidate_ids = [item.event_id for item in self.eligible_candidates]
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("Candidate event_id values must be unique")
        known = set(evidence_ids)
        if any(not set(item.evidence_ids) <= known for item in self.eligible_candidates):
            raise ValueError("Candidate references unknown evidence")
        return self


class RecommendedEvent(ContractModel):
    event_id: Identifier
    title: Annotated[str, Field(min_length=1, max_length=200)]
    effects: list[SkillEffect]
    explanation: Explanation


class RecommendationResponse(ContractModel):
    data_version: Version
    state_version: Annotated[int, Field(ge=0)]
    scenario_date: date = Field(strict=False)
    status: Literal["ok", "no_candidates", "no_target", "not_configured", "unavailable"]
    engine: Literal["none", "oleg"]
    recommendations: Annotated[list[RecommendedEvent], Field(max_length=3)]
    recommendation_id: Identifier | None = None
    stale: bool = False

    @model_validator(mode="after")
    def consistent_result(self) -> "RecommendationResponse":
        if self.status in {"no_target", "no_candidates"} and self.engine == "none":
            if self.recommendations:
                raise ValueError("Non-ok results cannot contain recommendations")
            return self
        if self.status == "no_target":
            raise ValueError("no_target is determined by the backend with engine=none")
        RecommendationResult(
            status=self.status,
            engine=self.engine,
            recommendations=[
                RecommendationSelection(event_id=item.event_id, explanation=item.explanation)
                for item in self.recommendations
            ],
        )
        return self


class PreviewRequest(ContractModel):
    expected_state_version: Annotated[int, Field(ge=0)]
    scenario_date: date = Field(strict=False)
    event_ids: Annotated[list[Identifier], Field(min_length=1, max_length=3)]


class PreviewResponse(ContractModel):
    data_version: Version
    state_version: Annotated[int, Field(ge=0)]
    scenario_date: date = Field(strict=False)
    persisted: Literal[False] = False
    effects: list[SkillEffect]
    projected_skills: list[SkillLevel]
    projected_gaps: list[SkillGap]


class CompletionRequest(ContractModel):
    expected_state_version: Annotated[int, Field(ge=0)]
    event_id: Identifier
    mode: Literal["completion", "demo_simulation"]
    record_id: Identifier | None = None


class CompletionResponse(ContractModel):
    state_version: Annotated[int, Field(ge=0)]
    completion: CompletionRecord


class HRSummaryResponse(ContractModel):
    data_version: Version
    employee_count: Annotated[int, Field(ge=0)]
    employees_with_goal: Annotated[int, Field(ge=0)]
    completion_count: Annotated[int, Field(ge=0)]
    demo_simulation_count: Annotated[int, Field(ge=0)]


class HRSkillGap(ContractModel):
    skill_id: Identifier
    skill_name: str
    employees_requiring: Annotated[int, Field(ge=1)]
    employees_with_gap: Annotated[int, Field(ge=0)]
    gap_percent: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
    average_gap: Score
    critical_gap_count: Annotated[int, Field(ge=0)]


class HRAttentionReason(ContractModel):
    code: Literal[
        "no_history",
        "no_recent_completion",
        "repeated_no_show",
        "no_candidates",
        "no_target",
        "recommendation_missing",
        "recommendation_stale",
        "recommendation_unavailable",
        "ai_not_configured",
    ]
    message: str


class HRAttentionEmployee(ContractModel):
    profile: EmployeeProfile
    reasons: list[HRAttentionReason]
    last_completed_date: date | None = Field(strict=False)
    history_records_in_period: Annotated[int, Field(ge=0)]
    completed_in_period: Annotated[int, Field(ge=0)]
    no_show_in_period: Annotated[int, Field(ge=0)]
    eligible_event_count: Annotated[int, Field(ge=0)]
    progress_percent: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] | None


class HRParticipation(ContractModel):
    event_id: Identifier
    title: str
    type: str
    format: str
    mandatory: bool
    record_count: Annotated[int, Field(ge=0)]
    participant_count: Annotated[int, Field(ge=0)]
    completed: Annotated[int, Field(ge=0)]
    in_progress: Annotated[int, Field(ge=0)]
    dropped: Annotated[int, Field(ge=0)]
    no_show: Annotated[int, Field(ge=0)]
    declined: Annotated[int, Field(ge=0)]
    overdue: Annotated[int, Field(ge=0)]


class HRAnalyticsResponse(ContractModel):
    """A deterministic, HR-only snapshot; observations are not a motivation score."""

    data_version: Version
    state_version: Annotated[int, Field(ge=0)]
    scenario_date: date = Field(strict=False)
    period_start: date = Field(strict=False)
    period_end: date = Field(strict=False)
    window_days: Annotated[int, Field(ge=1, le=365)]
    employee_count: Annotated[int, Field(ge=0)]
    employees_with_goal: Annotated[int, Field(ge=0)]
    employees_with_history: Annotated[int, Field(ge=0)]
    employees_with_completion_in_period: Annotated[int, Field(ge=0)]
    history_records_in_period: Annotated[int, Field(ge=0)]
    historical_proxy_records_in_period: Annotated[int, Field(ge=0)]
    excluded_simulations: Annotated[int, Field(ge=0)]
    skill_gaps: list[HRSkillGap]
    attention: list[HRAttentionEmployee]
    participation: list[HRParticipation]
    notes: list[str]


class SourceFile(ContractModel):
    """Transport for an original source file; its content is validated by the importer."""

    source_filename: Literal["skills.json", "employees.json", "events.json", "activity_history.csv"]
    source_format: Literal["json", "csv"]
    content: Annotated[str, Field(min_length=1, max_length=2_000_000)]

    @model_validator(mode="after")
    def matching_format(self) -> "SourceFile":
        if not self.source_filename.endswith("." + self.source_format):
            raise ValueError("source_filename and source_format must agree")
        return self


class ImportRequest(SourceFile):
    dry_run: bool = True
    preview_token: Identifier | None = None


class BatchImportRequest(ContractModel):
    dry_run: bool = True
    files: Annotated[list[SourceFile], Field(min_length=1, max_length=4)]
    preview_token: Identifier | None = None

    @model_validator(mode="after")
    def unique_filenames(self) -> "BatchImportRequest":
        filenames = [source.source_filename for source in self.files]
        if len(set(filenames)) != len(filenames):
            raise ValueError("A batch cannot contain duplicate source filenames")
        return self


class ImportResponse(ContractModel):
    dry_run: bool
    status: Literal["validated", "imported"]
    data_version: Version | None
    imported_records: Annotated[int, Field(ge=0)]
    warnings: list[str] = Field(default_factory=list)
    preview_token: Identifier | None = None
    revision: Annotated[int, Field(ge=0)] = 0
    counts: dict[str, Annotated[int, Field(ge=0)]] = Field(default_factory=dict)
