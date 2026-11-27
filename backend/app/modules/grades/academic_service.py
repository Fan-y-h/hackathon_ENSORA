"""Grades P1: validation rules (T44), saved scenarios (T39), feedback/diagnostics (T45, R42),
trends (R43). Builds on ``GradesService`` helpers; never writes actual marks or mastery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.common import check_revision
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.grades import domain, forecast, validation_rules
from app.modules.grades.models import (
    AcademicFeedback,
    AcademicProgram,
    AcademicRuleSet,
    CorrectedPaper,
    GradeRecord,
    GradeScenario,
    GradeScenarioValue,
)
from app.modules.grades.schemas import (
    AllocationOut,
    AllocationRequest,
    AssumptionIn,
    CommentAnalysisOut,
    CorrectedPaperOut,
    CorrectedPaperPut,
    DiagnosticActivity,
    FeedbackCreate,
    FeedbackOut,
    FeedbackPatch,
    ForecastItem,
    ForecastOut,
    ForecastRequest,
    NodeValidationOut,
    RuleOut,
    RulesOut,
    RulesPut,
    ScenarioCreate,
    ScenarioEvaluationOut,
    ScenarioOut,
    ScenarioPatch,
    SimulateRequest,
    TrendOut,
    TrendsOut,
    ValidationOut,
)
from app.modules.grades.service import GradesService, _Loaded

# GRADE_TRACKER §6 routing: difficulty → activity (artifact generated only at session start).
FLAG_ROUTES: dict[str, tuple[str, str | None, int, str]] = {
    "calculation": ("practice", "quiz", 40, "Exercices de calcul progressifs"),
    "formula_recall": ("active_recall", "flashcards", 25, "Rappel actif des formules oubliées"),
    "comprehension": ("discovery", "revision_sheet", 40, "Nouvelle explication et exemples"),
    "reasoning": ("practice", "quiz", 40, "Reconstruction progressive du raisonnement"),
    "programming": ("practice", None, 45, "Débogage et exercices de code ciblés"),
    "recurring_errors": ("practice", "quiz", 30, "Consolidation via le carnet d'erreurs"),
    "autonomy": ("practice", "quiz", 40, "Exercice sans aide pour vérifier l'autonomie"),
    "method": ("revision", "revision_sheet", 30, "Fiche méthode et exercice type"),
    "time_management": ("practice", "quiz", 45, "Entraînement chronométré"),
}
COMMENT_KEYWORDS = {
    "calculation": ("calcul", "erreur de calcul", "arithm", "calculation"),
    "formula_recall": ("formule", "oubli", "formula", "définition", "definition"),
    "comprehension": ("compris", "incompréhension", "confusion", "sens", "misunderstand"),
    "reasoning": ("raisonnement", "justif", "démonstration", "preuve", "proof", "rigueur"),
    "programming": ("code", "programme", "bug", "python", "algorithme"),
    "method": ("méthode", "démarche", "method"),
    "time_management": ("temps", "inachevé", "pas fini", "unfinished", "time"),
    "autonomy": ("aide", "guidé", "help"),
}
DIAGNOSTIC_MIN = 30
DIAGNOSTIC_WINDOW = timedelta(days=30)
DEFAULT_ALERT_THRESHOLD = Decimal(10)


def _status_map(computed: str, verified: bool) -> tuple[str, bool]:
    if not verified:
        return "unknown", computed == "validated_by_compensation"
    if computed == "validated_by_compensation":
        return "validated", True
    return computed, False


class AcademicService(GradesService):
    # ==================================================================== rules ====
    def _rules_out(self, ld: _Loaded, rs: AcademicRuleSet | None) -> RulesOut:
        if rs is None:
            return RulesOut(program_id=ld.program.id, version=0, verification_status="none", source_ref=None, rules=[])
        return RulesOut(
            program_id=ld.program.id, version=rs.version, verification_status=rs.verification_status,  # type: ignore[arg-type]
            source_ref=rs.source_ref,
            rules=[
                RuleOut(type=r["type"], node_id=ld.by_key[r["node_key"]].id if r["node_key"] in ld.by_key else None,
                        node_key=r["node_key"], value=Decimal(r["value"]))
                for r in rs.rules
            ],
        )

    def get_rules(self, user_id: str, program_id: str) -> RulesOut:
        ld = self._load(user_id, program_id)
        return self._rules_out(ld, self._latest_rules(program_id))

    def put_rules(self, user_id: str, program_id: str, body: RulesPut) -> RulesOut:
        ld = self._load(user_id, program_id)
        tree = self._require_tree(ld)
        latest = self._latest_rules(program_id)
        current = latest.version if latest else 0
        if body.expected_version is not None and body.expected_version != current:
            raise Conflict("Stale rule set version", code="stale_revision",
                           details={"expected_version": body.expected_version, "current_version": current})
        if body.verification_status == "verified" and not (body.source_ref or "").strip():
            raise ValidationFailed("Verified rules require a source reference (provenance)", code="missing_provenance")
        rules = []
        for r in body.rules:
            node = ld.by_id.get(r.node_id)
            if node is None:
                raise NotFound(f"Node {r.node_id} not found in this program")
            rules.append(validation_rules.Rule(r.type, node.key, r.value))
        try:
            validation_rules.validate_rules(tree, rules)
        except validation_rules.RuleError as exc:
            raise ValidationFailed("Invalid rules", details=exc.errors, code="invalid_rules") from exc
        rs = AcademicRuleSet(
            program_id=program_id, version=current + 1, verification_status=body.verification_status,
            source_ref=body.source_ref,
            rules=[{"type": r.type, "node_key": r.node_key, "value": str(r.value)} for r in rules],
        )
        self.db.add(rs)
        self._bump(ld.program, "academic.rules_changed", program_id, version=rs.version)
        self.db.flush()
        return self._rules_out(ld, rs)

    def validation(self, user_id: str, program_id: str) -> ValidationOut:
        ld = self._load(user_id, program_id)
        tree = self._require_tree(ld)
        rs = self._latest_rules(program_id)
        verified = rs is not None and rs.verification_status == "verified"
        evals = validation_rules.evaluate(tree, self._marks(ld, "actual"), self._rules(ld, rs))
        nodes = []
        for k in tree.walk():
            if k not in evals:
                continue
            e = evals[k]
            official, via = _status_map(e.computed_status, verified)
            nodes.append(NodeValidationOut(
                node_id=ld.by_key[k].id, node_key=k, title=ld.by_key[k].title,
                computed_status=e.computed_status, official_status=official, via_compensation=via,  # type: ignore[arg-type]
                outlook=e.outlook, risk=e.risk, mean=e.mean, bound_min=e.bound_min, bound_max=e.bound_max,  # type: ignore[arg-type]
                reason_codes=list(e.reason_codes),
            ))
        notes = ["Evaluation under the weighted-average model and the configured rules only."]
        if rs is None:
            notes.append("No rules configured: official status unknown.")
        elif not verified:
            notes.append("Rules are unverified: official status stays unknown; computed status is indicative.")
        return ValidationOut(
            program_id=program_id, program_revision=ld.program.revision, rule_set_version=rs.version if rs else 0,
            verification_status=rs.verification_status if rs else "none", nodes=nodes, notes=notes,  # type: ignore[arg-type]
        )

    # ================================================================ scenarios ====
    def _owned_scenario(self, user_id: str, scenario_id: str) -> GradeScenario:
        sc = self.db.get(GradeScenario, scenario_id)
        if sc is None or sc.user_id != user_id:
            raise NotFound("Scenario not found")
        return sc

    def _scenario_values(self, sc: GradeScenario) -> list[AssumptionIn]:
        rows = self.db.scalars(select(GradeScenarioValue).where(GradeScenarioValue.scenario_id == sc.id)
                               .order_by(GradeScenarioValue.node_id))
        return [AssumptionIn(node_id=r.node_id, value=Decimal(r.value)) for r in rows]

    def _scenario_out(self, sc: GradeScenario) -> ScenarioOut:
        program = self.db.get(AcademicProgram, sc.program_id)
        return ScenarioOut(
            id=sc.id, program_id=sc.program_id, name=sc.name, base_revision=sc.base_revision,
            values=self._scenario_values(sc), stale=program is not None and program.revision != sc.base_revision,
            revision=sc.revision,
        )

    def _store_values(self, ld: _Loaded, sc: GradeScenario, values: list[AssumptionIn]) -> None:
        tree = self._require_tree(ld)
        self._assumption_map(ld, tree, values, self._marks(ld, "actual"))  # same validation as simulate
        for old in self.db.scalars(select(GradeScenarioValue).where(GradeScenarioValue.scenario_id == sc.id)):
            self.db.delete(old)
        self.db.flush()
        seen = set()
        for v in values:
            if v.node_id in seen:
                raise ValidationFailed("Duplicate node in scenario", details=[v.node_id])
            seen.add(v.node_id)
            self.db.add(GradeScenarioValue(scenario_id=sc.id, node_id=v.node_id, value=str(v.value)))

    def list_scenarios(self, user_id: str, program_id: str) -> list[ScenarioOut]:
        self._program(user_id, program_id)
        rows = self.db.scalars(select(GradeScenario).where(GradeScenario.program_id == program_id)
                               .order_by(GradeScenario.created_at))
        return [self._scenario_out(sc) for sc in rows]

    def create_scenario(self, user_id: str, body: ScenarioCreate) -> ScenarioOut:
        ld = self._load(user_id, body.program_id)
        sc = GradeScenario(user_id=user_id, program_id=body.program_id, name=body.name,
                           base_revision=ld.program.revision)
        self.db.add(sc)
        self.db.flush()
        self._store_values(ld, sc, body.values)
        self.db.flush()
        return self._scenario_out(sc)  # scenarios never bump the program revision

    def patch_scenario(self, user_id: str, scenario_id: str, body: ScenarioPatch) -> ScenarioOut:
        sc = self._owned_scenario(user_id, scenario_id)
        check_revision(sc.revision, body.expected_revision)
        ld = self._load(user_id, sc.program_id)
        if body.name is not None:
            sc.name = body.name
        if body.values is not None:
            self._store_values(ld, sc, body.values)
            sc.base_revision = ld.program.revision
        sc.revision += 1
        self.db.flush()
        return self._scenario_out(sc)

    def delete_scenario(self, user_id: str, scenario_id: str) -> None:
        self.db.delete(self._owned_scenario(user_id, scenario_id))

    def evaluate_scenario(self, user_id: str, scenario_id: str) -> ScenarioEvaluationOut:
        sc = self._owned_scenario(user_id, scenario_id)
        ld = self._load(user_id, sc.program_id)
        actual_ids = {
            r.node_id for r in self.db.scalars(
                select(GradeRecord).where(GradeRecord.program_id == sc.program_id, GradeRecord.kind == "actual"))
        }
        values = self._scenario_values(sc)
        kept = [v for v in values if v.node_id not in actual_ids and v.node_id in ld.by_id]
        dropped = [v for v in values if v not in kept]
        sim = self.simulate(user_id, SimulateRequest.model_construct(
            program_id=sc.program_id, expected_revision=None, values=kept))
        warnings = list(sim.warnings)
        if dropped:
            warnings.append("SCENARIO_VALUES_SUPERSEDED_BY_ACTUAL_MARKS")
        if sc.base_revision != ld.program.revision:
            warnings.append("SCENARIO_BASED_ON_OLDER_REVISION")
        return ScenarioEvaluationOut(**{**sim.model_dump(), "warnings": warnings}, scenario_id=sc.id,
                                     dropped_values=dropped)

    # ================================================================= feedback ====
    def _subject_of(self, ld: _Loaded, node_key: str) -> str | None:
        tree = ld.tree
        if tree is None:
            return None
        for k in [node_key, *tree.ancestors(node_key)]:
            if tree.nodes[k].subject_id:
                return tree.nodes[k].subject_id
        return None

    @staticmethod
    def proposal(fb: AcademicFeedback) -> list[DiagnosticActivity]:
        out = []
        for flag in fb.flags:
            activity, artifact, minutes, why = FLAG_ROUTES[flag]
            out.append(DiagnosticActivity(flag=flag, activity_type=activity, artifact_type=artifact,
                                          estimated_min=minutes, concept_ids=fb.concept_ids, rationale=why))
        return out

    def _feedback_out(self, fb: AcademicFeedback) -> FeedbackOut:
        notes = ["A global mark never changes concept mastery; only diagnostic attempts do."]
        if fb.corrected_document_id and not fb.external_ai_consent:
            notes.append("Corrected paper stored as reference only; no external AI analysis without consent.")
        elif fb.corrected_document_id:
            notes.append("Corrected-paper AI analysis is planned (P2, T46); not performed.")
        return FeedbackOut(
            id=fb.id, grade_record_id=fb.grade_record_id, subject_id=fb.subject_id, flags=fb.flags,
            concept_ids=fb.concept_ids, comments=fb.comments, status=fb.status,  # type: ignore[arg-type]
            external_ai_consent=fb.external_ai_consent, proposal=self.proposal(fb), notes=notes, revision=fb.revision,
        )

    def create_feedback(self, user_id: str, record_id: str, body: FeedbackCreate) -> FeedbackOut:
        r = self._owned_record(user_id, record_id)
        if r.kind != "actual":
            raise ValidationFailed("Feedback applies to actual marks only")
        ld = self._load(user_id, r.program_id)
        fb = AcademicFeedback(
            user_id=user_id, grade_record_id=r.id, subject_id=self._subject_of(ld, ld.by_id[r.node_id].key),
            flags=list(dict.fromkeys(body.flags)), concept_ids=list(dict.fromkeys(body.concept_ids)),
            comments=body.comments, corrected_document_id=body.corrected_document_id,
            external_ai_consent=body.external_ai_consent,
        )
        self.db.add(fb)
        self.db.flush()
        self._bump(ld.program, "grade.feedback_added", fb.id, record_id=r.id)
        return self._feedback_out(fb)

    def patch_feedback(self, user_id: str, feedback_id: str, body: FeedbackPatch) -> FeedbackOut:
        fb = self.db.get(AcademicFeedback, feedback_id)
        if fb is None or fb.user_id != user_id:
            raise NotFound("Feedback not found")
        if fb.status != body.status:
            fb.status = body.status
            fb.revision += 1
            r = self.db.get(GradeRecord, fb.grade_record_id)
            if r is not None:
                self._bump(self._program(user_id, r.program_id), "grade.feedback_changed", fb.id)
        return self._feedback_out(fb)

    def list_feedback(self, user_id: str, program_id: str) -> list[FeedbackOut]:
        self._program(user_id, program_id)
        rows = self.db.scalars(
            select(AcademicFeedback).join(GradeRecord, GradeRecord.id == AcademicFeedback.grade_record_id)
            .where(GradeRecord.program_id == program_id).order_by(AcademicFeedback.created_at)
        )
        return [self._feedback_out(f) for f in rows]

    # ============================================================ corrected paper (T46) ===
    def _paper_out(self, paper: CorrectedPaper, record: GradeRecord, ld: _Loaded, recorded: int,
                   warnings: list[str]) -> CorrectedPaperOut:
        node = ld.by_id[record.node_id]
        rec20 = domain.to_twenty(ld.tree.nodes[node.key], Decimal(record.value))  # type: ignore[union-attr]
        earned = sum((Decimal(i["earned"]) for i in paper.items), Decimal(0))
        mx = sum((Decimal(i["max"]) for i in paper.items), Decimal(0))
        total = earned / mx * 20 if mx > 0 else None
        consistent = total is not None and abs(total - rec20) <= Decimal("0.5")
        if not consistent:
            warnings = [*warnings, "ITEM_TOTAL_DIFFERS_FROM_RECORDED_MARK"]
        return CorrectedPaperOut(
            id=paper.id, grade_record_id=record.id, document_id=paper.document_id, confirmed=paper.confirmed,
            items=paper.items, total_on_20=total, record_on_20=rec20, consistent=consistent,
            evidence_recorded=recorded, warnings=warnings, revision=paper.revision,
        )

    def put_corrected_paper(self, user_id: str, record_id: str, body: CorrectedPaperPut) -> CorrectedPaperOut:
        """Itemized corrected paper. Only *confirmed* question↔concept links become evidence
        (R42: never infer concept mastery from the overall mark)."""
        from app.modules.learning.service import record_external_evidence

        r = self._owned_record(user_id, record_id)
        if r.kind != "actual":
            raise ValidationFailed("Corrected papers apply to actual marks only")
        ld = self._load(user_id, r.program_id)
        warnings: list[str] = []
        for i, it in enumerate(body.items):
            if it.earned > it.max:
                raise ValidationFailed(f"item {i}: earned > max", code="item_out_of_range")
        paper = self.db.scalar(select(CorrectedPaper).where(CorrectedPaper.grade_record_id == r.id))
        if paper is not None and paper.confirmed:
            raise Conflict("Confirmed paper evidence is immutable; add a new mark instead", code="paper_confirmed")
        if paper is None:
            paper = CorrectedPaper(user_id=user_id, grade_record_id=r.id)
            self.db.add(paper)
        else:
            paper.revision += 1
        paper.document_id, paper.confirmed = body.document_id, body.confirmed
        paper.external_ai_consent = body.external_ai_consent
        paper.items = [{"id": f"item-{i}", **it.model_dump(mode="json")} for i, it in enumerate(body.items)]
        self.db.flush()
        recorded = 0
        if body.confirmed:
            subject_id = self._subject_of(ld, ld.by_id[r.node_id].key)
            when = r.observed_at or r.created_at
            for it in paper.items:
                score = float(Decimal(it["earned"]) / Decimal(it["max"]))
                for cid in it["concept_ids"]:
                    recorded += 1
                    record_external_evidence(
                        self.db, user_id, concept_id=cid, subject_id=subject_id, dimension=it["dimension"],
                        activity_kind="corrected_paper", activity_id=paper.id, item_id=it["id"], score=score,
                        weight=1.0, occurred_at=when, clock=self.clock,
                    )
            if not any(it["concept_ids"] for it in paper.items):
                warnings.append("NO_CONCEPT_LINKS_NO_EVIDENCE")
        else:
            warnings.append("NOT_CONFIRMED_NO_EVIDENCE")
        if body.document_id and not body.external_ai_consent:
            warnings.append("DOCUMENT_KEPT_LOCAL_NO_AI_ANALYSIS")
        self._bump(ld.program, "grade.corrected_paper", paper.id, record_id=r.id)
        return self._paper_out(paper, r, ld, recorded, warnings)

    def get_corrected_paper(self, user_id: str, record_id: str) -> CorrectedPaperOut:
        r = self._owned_record(user_id, record_id)
        paper = self.db.scalar(select(CorrectedPaper).where(CorrectedPaper.grade_record_id == r.id))
        if paper is None:
            raise NotFound("No corrected paper for this mark")
        return self._paper_out(paper, r, self._load(user_id, r.program_id), 0, [])

    @staticmethod
    def analyze_comments(text: str) -> CommentAnalysisOut:
        """Keyword heuristic over teacher comments → *tentative* difficulty flags for the
        student to confirm. No AI provider is called (that adapter belongs to G, with consent)."""
        lowered = text.lower()
        proposals = []
        for flag, words in COMMENT_KEYWORDS.items():
            hits = sorted({w for w in words if w in lowered})
            if hits:
                proposals.append({"flag": flag, "matched": hits, "confirmed": False})
        return CommentAnalysisOut(
            method="keyword-heuristic-v1", ai_used=False, proposed_flags=proposals,
            notes=["Tentative suggestions: confirm them through POST /grades/records/{id}/feedback.",
                   "AI analysis of corrected papers requires explicit consent and a configured provider (not available)."],
        )

    # ======================================================== forecast & time (T47) ===
    def forecast(self, user_id: str, body: ForecastRequest) -> ForecastOut:
        ld = self._load(user_id, body.program_id)
        tree = self._require_tree(ld)
        actual = self._marks(ld, "actual")
        model = forecast.fit([float(domain.to_twenty(tree.nodes[k], v)) for k, v in sorted(actual.items())])
        targets = {o.node_key: o.target for _, o in self._objectives(ld)}
        keys = [k for k in tree.walk() if not tree.is_leaf(k)]
        items = []
        for k in keys:
            f = forecast.forecast_node(tree, actual, k, targets.get(k), model, body.samples, body.seed)
            items.append(ForecastItem(
                node_id=ld.by_key[k].id, node_key=k, title=ld.by_key[k].title, target=f.target,
                probability_reach_target=f.probability, p10=f.p10, p50=f.p50, p90=f.p90,
                deterministic_min=f.deterministic_min, deterministic_max=f.deterministic_max,
                remaining_weight=f.remaining_weight,
            ))
        notes = ["Assumption: remaining marks independent, normal around your past marks (shrunk to 12±3), clipped to [0, 20].",
                 "Not a guarantee; official validation rules are not applied in this forecast."]
        if not model.calibrated:
            notes.append(f"Uncalibrated: fewer than {forecast.MIN_MARKS_FOR_CALIBRATION} actual marks.")
        return ForecastOut(program_id=body.program_id, method=forecast.METHOD,
                           model={"mean": model.mean, "sd": model.sd, "n_marks": model.n_marks},
                           calibrated=model.calibrated, forecasts=items, notes=notes)

    def allocate_time(self, user_id: str, body: AllocationRequest) -> AllocationOut:
        ld = self._load(user_id, body.program_id)
        tree = self._require_tree(ld)
        actual = self._marks(ld, "actual")
        sigs = {s.node_key: s for s in domain.academic_signals(
            tree, actual, [o for _, o in self._objectives(ld)], self._verified_risk(ld))}
        risk = self._verified_risk(ld)
        weights, basis = {}, {}
        for k, s in sigs.items():
            if s.remaining_weight <= domain.FRACTION_TOLERANCE:
                continue
            gap = 0.0 if s.required_mean is None or s.provisional is None else max(0.0, float(s.required_mean - s.provisional)) / 20
            if s.feasibility in ("guaranteed", "achieved"):
                gap = 0.0
            w = float(s.remaining_weight) * (1 + gap) * (1 + float(risk.get(k, 0)))
            weights[k], basis[k] = w, float(s.remaining_weight)
        optimized = forecast.allocate(weights, body.available_min, body.min_per_subject)
        baseline = forecast.proportional(basis, body.available_min)
        rows = [
            {"node_id": ld.by_key[k].id, "node_key": k, "title": ld.by_key[k].title,
             "subject_id": ld.by_key[k].subject_id, "weight": round(weights[k], 6),
             "optimized_min": optimized.get(k, 0), "baseline_min": baseline.get(k, 0),
             "feasibility": sigs[k].feasibility, "reason_codes": list(sigs[k].reason_codes)}
            for k in sorted(weights)
        ]
        return AllocationOut(
            program_id=body.program_id, available_min=body.available_min, method="concave-sqrt-allocation-v1",
            allocations=rows,
            notes=["Weights = remaining effective weight × (1 + target gap) × (1 + verified risk).",
                   "Diminishing returns (√minutes) is an assumption; no grade gain per hour is predicted.",
                   "Baseline = allocation proportional to remaining weight only."],
        )

    # =================================================================== trends ====
    def trends(self, user_id: str, program_id: str) -> TrendsOut:
        ld = self._load(user_id, program_id)
        tree = self._require_tree(ld)
        records = sorted(
            self.db.scalars(select(GradeRecord).where(GradeRecord.program_id == program_id, GradeRecord.kind == "actual")),
            key=lambda r: (r.observed_at or r.created_at, r.id),
        )
        targets = [k for k in tree.walk() if not tree.is_leaf(k)]
        series = {k: [] for k in targets}
        marks: dict[str, Decimal] = {}
        for r in records:
            key = ld.by_id[r.node_id].key
            marks[key] = Decimal(r.value)
            for k in targets:
                if k == key or k in tree.ancestors(key):
                    s = domain.summarize(tree, marks, k)
                    series[k].append({"at": r.observed_at or r.created_at, "record_id": r.id,
                                      "provisional": s.provisional, "coverage": s.coverage})
        return TrendsOut(program_id=program_id, series=[
            TrendOut(node_id=ld.by_key[k].id, node_key=k, kind=ld.by_key[k].kind, title=ld.by_key[k].title,  # type: ignore[arg-type]
                     points=series[k])
            for k in targets
        ])


# ---- query interface for the planner (read-only) -------------------------------------------
@dataclass(frozen=True)
class DiagnosticNeed:
    key: str
    subject_id: str | None
    concept_ids: tuple[str, ...]
    activity_type: str
    estimated_min: int
    title: str
    reason: str


def academic_diagnostic_needs(db: Session, user_id: str, now: datetime) -> list[DiagnosticNeed]:
    """R42: a weak mark → subject-level diagnostic; reported difficulties → targeted activities.
    Nothing here writes mastery; outcomes of these activities produce evidence normally."""
    svc = AcademicService(db)
    needs: list[DiagnosticNeed] = []
    feedback_by_record: dict[str, list[AcademicFeedback]] = {}
    for fb in db.scalars(select(AcademicFeedback).where(AcademicFeedback.user_id == user_id)):
        feedback_by_record.setdefault(fb.grade_record_id, []).append(fb)
    for p in db.scalars(select(AcademicProgram).where(AcademicProgram.user_id == user_id)):
        try:
            ld = svc._load(user_id, p.id)
        except domain.TreeError:
            continue
        if ld.tree is None:
            continue
        tree = ld.tree
        targets = {o.node_key: o.target for _, o in svc._objectives(ld)}
        for r in db.scalars(select(GradeRecord).where(GradeRecord.program_id == p.id, GradeRecord.kind == "actual")):
            if (r.observed_at or r.created_at) < now - DIAGNOSTIC_WINDOW:
                continue
            node = ld.by_id.get(r.node_id)
            if node is None:
                continue
            fbs = feedback_by_record.get(r.id, [])
            subject_id = svc._subject_of(ld, node.key)
            if fbs:
                for fb in fbs:
                    if fb.status == "dismissed":
                        continue
                    for a in AcademicService.proposal(fb):
                        needs.append(DiagnosticNeed(
                            key=f"feedback:{fb.id}:{a.flag}", subject_id=subject_id, concept_ids=tuple(a.concept_ids),
                            activity_type=a.activity_type, estimated_min=a.estimated_min,
                            title=f"Remédiation ({a.flag}) — {node.title}", reason=f"GRADE_FEEDBACK_{a.flag.upper()}",
                        ))
                continue
            mark20 = domain.to_twenty(tree.nodes[node.key], Decimal(r.value))
            applicable = [targets[k] for k in [node.key, *tree.ancestors(node.key)] if k in targets]
            threshold = max(applicable) if applicable else DEFAULT_ALERT_THRESHOLD
            if mark20 < threshold:
                needs.append(DiagnosticNeed(
                    key=f"diagnostic:{r.id}", subject_id=subject_id, concept_ids=(), activity_type="diagnostic",
                    estimated_min=DIAGNOSTIC_MIN, title=f"Diagnostic — {node.title}",
                    reason="LOW_GRADE_DIAGNOSTIC" if applicable else "BELOW_10_DIAGNOSTIC",
                ))
    return needs
