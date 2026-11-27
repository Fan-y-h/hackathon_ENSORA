"""Academic DTOs. Decimals are accepted as strings or numbers and serialized as strings."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from app.core.common import UtcDatetime
from app.modules.grades.domain import QUANT


def dec_str(d: Decimal) -> str:
    """7-decimal fixed-point string without trailing zeros (never scientific notation)."""
    text = format(d.quantize(QUANT), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


Dec = Annotated[Decimal, PlainSerializer(dec_str, return_type=str)]
NodeKind = Literal["year", "semester", "unit", "subject", "assessment_group", "assessment"]
Feasibility = Literal["feasible", "guaranteed", "achieved", "impossible", "unknown"]
KEY_PATTERN = r"^[A-Za-z0-9_.-]{1,64}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProgramCreate(_Strict):
    title: str = Field(min_length=1, max_length=200)
    academic_year: str | None = Field(default=None, pattern=r"^\d{4}-\d{4}$")
    cohort: Literal["1A", "2A", "3A"] | None = None
    specialization: str | None = Field(default=None, max_length=120)


class ProgramOut(BaseModel):
    id: str
    title: str
    academic_year: str | None
    cohort: str | None
    specialization: str | None
    provenance: str
    official_status: Literal["unknown"] = "unknown"
    revision: int


class NodeIn(_Strict):
    key: str = Field(pattern=KEY_PATTERN, description="Stable key, unique within the program")
    parent_key: str | None = Field(default=None, pattern=KEY_PATTERN)
    kind: NodeKind
    title: str = Field(min_length=1, max_length=200)
    weight: Decimal = Field(default=Decimal(1), description="Relative to siblings")
    weight_mode: Literal["coefficient", "fraction"] = Field(
        default="coefficient", description="How this node's children weights are interpreted"
    )
    scale_min: Decimal = Decimal(0)
    scale_max: Decimal = Decimal(20)
    subject_id: str | None = None
    event_id: str | None = None


class NodeOut(BaseModel):
    id: str
    key: str
    parent_key: str | None
    kind: NodeKind
    title: str
    weight: Dec
    weight_mode: str
    scale_min: Dec
    scale_max: Dec
    subject_id: str | None
    event_id: str | None
    is_leaf: bool
    weight_in_parent: Dec | None
    weight_in_root: Dec


class CurriculumPut(_Strict):
    expected_revision: int | None = None
    nodes: list[NodeIn] = Field(min_length=1, max_length=2000)


class CurriculumOut(BaseModel):
    program_id: str
    revision: int
    nodes: list[NodeOut]
    removed_records: int = 0


class RecordCreate(_Strict):
    node_id: str
    kind: Literal["actual", "estimated"] = "actual"
    value: Decimal
    observed_at: UtcDatetime | None = None


class RecordPatch(_Strict):
    expected_revision: int | None = None
    value: Decimal


class RecordOut(BaseModel):
    id: str
    program_id: str
    node_id: str
    node_key: str
    kind: Literal["actual", "estimated"]
    value: Dec
    value_on_20: Dec
    provenance: str
    observed_at: UtcDatetime | None
    revision: int


class ObjectiveCreate(_Strict):
    program_id: str
    target: Decimal = Field(ge=0, le=20, description="/20")
    node_id: str | None = None
    node_kind: NodeKind | None = Field(
        default=None, description="Apply the same target to every node of this kind (e.g. 12 in every UE)"
    )


class ObjectivePatch(_Strict):
    expected_revision: int | None = None
    target: Decimal | None = Field(default=None, ge=0, le=20)
    active: bool | None = None


class ObjectiveOut(BaseModel):
    id: str
    program_id: str
    node_id: str
    node_key: str
    target: Dec
    active: bool
    revision: int


class SummaryOut(BaseModel):
    node_id: str
    node_key: str
    kind: NodeKind
    title: str
    coverage: Dec
    provisional: Dec | None
    final: Dec | None
    forecast: Dec | None
    forecast_coverage: Dec
    remaining_weight: Dec
    bound_min: Dec
    bound_max: Dec
    official_status: Literal["unknown"] = "unknown"


class ProgramSummaryOut(BaseModel):
    program_id: str
    revision: int
    nodes: list[SummaryOut]


class AssumptionIn(_Strict):
    node_id: str
    value: Decimal


class RequiredRequest(_Strict):
    program_id: str
    objective_ids: list[str] | None = None
    assumptions: list[AssumptionIn] = Field(default_factory=list, max_length=2000)


class RequirementOut(BaseModel):
    objective_id: str
    node_id: str
    node_key: str
    target: Dec
    required_mean: Dec | None
    feasibility: Feasibility
    bound_min: Dec
    bound_max: Dec
    remaining_weight: Dec
    reason_codes: list[str]


class LeafNeedOut(BaseModel):
    node_id: str
    node_key: str
    needed: Dec
    objective_ids: list[str]
    note: str = "sufficient uniform plan, not a unique minimum"


class RequiredOut(BaseModel):
    program_id: str
    revision: int
    assumptions: list[AssumptionIn]
    requirements: list[RequirementOut]
    leaves: list[LeafNeedOut]


class SimulateRequest(_Strict):
    program_id: str
    expected_revision: int | None = None
    values: list[AssumptionIn] = Field(min_length=1, max_length=2000)


class ProjectionOut(BaseModel):
    node_id: str
    node_key: str
    title: str
    baseline: Dec | None
    projected: Dec | None
    delta: Dec | None
    baseline_coverage: Dec
    projected_coverage: Dec


class SimulateOut(BaseModel):
    program_id: str
    base_revision: int
    simulated: Literal[True] = True
    projections: list[ProjectionOut]
    requirements: list[RequirementOut]
    warnings: list[str]


class SignalOut(BaseModel):
    node_id: str
    node_key: str
    subject_id: str | None
    effective_weight: Dec
    remaining_weight: Dec
    coverage: Dec
    provisional: Dec | None
    target: Dec | None
    required_mean: Dec | None
    feasibility: Feasibility
    score: Dec | None
    reason_codes: list[str]


class SignalsOut(BaseModel):
    program_id: str
    input_revision: int
    signals: list[SignalOut]


# ---- P1: rules & validation (T44) -------------------------------------------------------
RuleType = Literal["pass_threshold", "eliminatory_minimum", "compensation"]


class RuleIn(_Strict):
    type: RuleType
    node_id: str
    value: Decimal = Field(ge=0, le=20, description="threshold or minimum on /20")


class RuleOut(BaseModel):
    type: RuleType
    node_id: str | None
    node_key: str
    value: Dec


class RulesPut(_Strict):
    expected_version: int | None = None
    verification_status: Literal["unverified", "verified"] = "unverified"
    source_ref: str | None = Field(default=None, max_length=2000, description="Where the rule comes from")
    rules: list[RuleIn] = Field(default_factory=list, max_length=500)


class RulesOut(BaseModel):
    program_id: str
    version: int
    verification_status: Literal["none", "unverified", "verified"]
    source_ref: str | None
    rules: list[RuleOut]


class NodeValidationOut(BaseModel):
    node_id: str
    node_key: str
    title: str
    computed_status: Literal["validated", "validated_by_compensation", "not_validated", "pending"]
    official_status: Literal["validated", "not_validated", "pending", "unknown"]
    via_compensation: bool
    outlook: Literal["guaranteed", "possible", "impossible", "decided", "n/a"]
    risk: Literal["none", "low", "medium", "high"]
    mean: Dec | None
    bound_min: Dec
    bound_max: Dec
    reason_codes: list[str]


class ValidationOut(BaseModel):
    program_id: str
    program_revision: int
    rule_set_version: int
    verification_status: Literal["none", "unverified", "verified"]
    nodes: list[NodeValidationOut]
    notes: list[str]


# ---- P1: saved scenarios (T39) ------------------------------------------------------------
class ScenarioCreate(_Strict):
    program_id: str
    name: str = Field(min_length=1, max_length=120)
    values: list[AssumptionIn] = Field(default_factory=list, max_length=2000)


class ScenarioPatch(_Strict):
    expected_revision: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    values: list[AssumptionIn] | None = Field(default=None, max_length=2000)


class ScenarioOut(BaseModel):
    id: str
    program_id: str
    name: str
    base_revision: int
    values: list[AssumptionIn]
    stale: bool
    revision: int


class ScenarioEvaluationOut(SimulateOut):
    scenario_id: str
    dropped_values: list[AssumptionIn]


# ---- P1: grade feedback & diagnostics (T45 / R42) --------------------------------------------
FeedbackFlag = Literal[
    "calculation", "formula_recall", "comprehension", "reasoning", "programming",
    "recurring_errors", "autonomy", "method", "time_management",
]


class FeedbackCreate(_Strict):
    flags: list[FeedbackFlag] = Field(min_length=1, max_length=9)
    concept_ids: list[str] = Field(default_factory=list, max_length=50)
    comments: str | None = Field(default=None, max_length=5000)
    corrected_document_id: str | None = None
    external_ai_consent: bool = False


class FeedbackPatch(_Strict):
    status: Literal["confirmed", "dismissed"]


class DiagnosticActivity(BaseModel):
    flag: str
    activity_type: str
    artifact_type: str | None
    estimated_min: int
    concept_ids: list[str]
    rationale: str


class FeedbackOut(BaseModel):
    id: str
    grade_record_id: str
    subject_id: str | None
    flags: list[str]
    concept_ids: list[str]
    comments: str | None
    status: Literal["proposed", "confirmed", "dismissed"]
    external_ai_consent: bool
    proposal: list[DiagnosticActivity]
    notes: list[str]
    revision: int


# ---- P1: trends (R43) ---------------------------------------------------------------------
class TrendPoint(BaseModel):
    at: UtcDatetime
    record_id: str
    provisional: Dec | None
    coverage: Dec


class TrendOut(BaseModel):
    node_id: str
    node_key: str
    kind: NodeKind
    title: str
    points: list[TrendPoint]


class TrendsOut(BaseModel):
    program_id: str
    series: list[TrendOut]


# ---- P2: corrected paper (T46) -------------------------------------------------------------
class PaperItemIn(_Strict):
    label: str = Field(min_length=1, max_length=120, description="e.g. 'Exercice 2.b'")
    earned: Decimal = Field(ge=0)
    max: Decimal = Field(gt=0)
    concept_ids: list[str] = Field(default_factory=list, max_length=20)
    dimension: Literal["theory", "memory", "reasoning", "application", "implementation", "autonomy"] = "application"
    comment: str | None = Field(default=None, max_length=5000)


class CorrectedPaperPut(_Strict):
    document_id: str | None = None
    items: list[PaperItemIn] = Field(min_length=1, max_length=200)
    confirmed: bool = Field(default=False, description="student confirms the item ↔ concept links")
    external_ai_consent: bool = False


class CorrectedPaperOut(BaseModel):
    id: str
    grade_record_id: str
    document_id: str | None
    confirmed: bool
    items: list[dict]
    total_on_20: Dec | None
    record_on_20: Dec
    consistent: bool
    evidence_recorded: int
    warnings: list[str]
    revision: int


class CommentAnalysisRequest(_Strict):
    text: str = Field(min_length=1, max_length=20000)


class CommentAnalysisOut(BaseModel):
    method: str
    ai_used: bool
    proposed_flags: list[dict]
    notes: list[str]


# ---- P2: forecast & time allocation (T47) --------------------------------------------------
class ForecastRequest(_Strict):
    program_id: str
    samples: int = Field(default=2000, ge=200, le=20000)
    seed: int = 0


class ForecastItem(BaseModel):
    node_id: str
    node_key: str
    title: str
    target: float | None
    probability_reach_target: float | None
    p10: float
    p50: float
    p90: float
    deterministic_min: float
    deterministic_max: float
    remaining_weight: float


class ForecastOut(BaseModel):
    program_id: str
    method: str
    model: dict
    calibrated: bool
    forecasts: list[ForecastItem]
    notes: list[str]


class AllocationRequest(_Strict):
    program_id: str
    available_min: int = Field(ge=0, le=100_000)
    min_per_subject: int = Field(default=0, ge=0, le=10_000)


class AllocationOut(BaseModel):
    program_id: str
    available_min: int
    method: str
    allocations: list[dict]
    notes: list[str]
