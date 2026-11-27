"""Grades use cases: curriculum, marks, objectives, summaries, required marks, what-if, signals.

Every write bumps the program revision and emits a ``grade.*`` / ``curriculum.changed``
outbox event carrying IDs and revisions only — never mark values.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.common import Clock, check_revision
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.events import record_event
from app.modules.grades import domain, validation_rules
from app.modules.grades.models import (
    AcademicNode,
    AcademicObjective,
    AcademicProgram,
    AcademicRuleSet,
    GradeRecord,
)
from app.modules.grades.schemas import (
    AssumptionIn,
    CurriculumOut,
    CurriculumPut,
    LeafNeedOut,
    NodeOut,
    ObjectiveCreate,
    ObjectiveOut,
    ObjectivePatch,
    ProgramCreate,
    ProgramOut,
    ProgramSummaryOut,
    ProjectionOut,
    RecordCreate,
    RecordOut,
    RecordPatch,
    RequiredOut,
    RequiredRequest,
    RequirementOut,
    SignalOut,
    SignalsOut,
    SimulateOut,
    SimulateRequest,
    SummaryOut,
)


@dataclass
class _Loaded:
    program: AcademicProgram
    nodes: list[AcademicNode]
    tree: domain.CurriculumTree | None
    by_id: dict[str, AcademicNode]
    by_key: dict[str, AcademicNode]


def _spec(n: AcademicNode) -> domain.NodeSpec:
    return domain.NodeSpec(
        key=n.key,
        parent_key=n.parent_key,
        kind=n.kind,
        title=n.title,
        weight=Decimal(n.weight),
        weight_mode=n.weight_mode,
        scale_min=Decimal(n.scale_min),
        scale_max=Decimal(n.scale_max),
        subject_id=n.subject_id,
        event_id=n.event_id,
    )


class GradesService:
    def __init__(self, db: Session, clock: Clock | None = None) -> None:
        self.db = db
        self.clock = clock

    # ----------------------------------------------------------------- loading ----
    def _program(self, user_id: str, program_id: str) -> AcademicProgram:
        p = self.db.get(AcademicProgram, program_id)
        if p is None or p.user_id != user_id:
            raise NotFound("Program not found")
        return p

    def _load(self, user_id: str, program_id: str) -> _Loaded:
        p = self._program(user_id, program_id)
        nodes = list(
            self.db.scalars(
                select(AcademicNode).where(AcademicNode.program_id == p.id).order_by(AcademicNode.ordinal)
            )
        )
        tree = domain.build_tree([_spec(n) for n in nodes]) if nodes else None
        return _Loaded(p, nodes, tree, {n.id: n for n in nodes}, {n.key: n for n in nodes})

    def _require_tree(self, ld: _Loaded) -> domain.CurriculumTree:
        if ld.tree is None:
            raise ValidationFailed("Program has no curriculum yet", code="empty_curriculum")
        return ld.tree

    def _marks(self, ld: _Loaded, kind: str) -> dict[str, Decimal]:
        rows = self.db.scalars(
            select(GradeRecord).where(GradeRecord.program_id == ld.program.id, GradeRecord.kind == kind)
        )
        return {ld.by_id[r.node_id].key: Decimal(r.value) for r in rows if r.node_id in ld.by_id}

    def _objectives(self, ld: _Loaded, ids: list[str] | None = None) -> list[tuple[AcademicObjective, domain.Objective]]:
        q = select(AcademicObjective).where(
            AcademicObjective.program_id == ld.program.id, AcademicObjective.active.is_(True)
        )
        if ids is not None:
            q = q.where(AcademicObjective.id.in_(ids))
        rows = list(self.db.scalars(q.order_by(AcademicObjective.created_at, AcademicObjective.id)))
        if ids is not None and len(rows) != len(set(ids)):
            raise NotFound("Objective not found")
        return [(o, domain.Objective(o.id, ld.by_id[o.node_id].key, Decimal(o.target))) for o in rows]

    def _bump(self, p: AcademicProgram, event: str, entity_id: str, **payload) -> None:
        p.revision += 1
        record_event(
            self.db, user_id=p.user_id, type=event, entity_id=entity_id, revision=p.revision,
            payload={"program_id": p.id, **payload},
        )

    def _latest_rules(self, program_id: str) -> AcademicRuleSet | None:
        return self.db.scalar(
            select(AcademicRuleSet).where(AcademicRuleSet.program_id == program_id)
            .order_by(AcademicRuleSet.version.desc())
        )

    def _rules(self, ld: _Loaded, rs: AcademicRuleSet | None) -> list[validation_rules.Rule]:
        if rs is None or ld.tree is None:
            return []
        return [
            validation_rules.Rule(r["type"], r["node_key"], Decimal(r["value"]))
            for r in rs.rules
            if r["node_key"] in ld.tree.nodes  # rules on removed nodes are ignored
        ]

    def _verified_risk(self, ld: _Loaded) -> dict[str, Decimal]:
        """Per-subject risk, only from a *verified* rule set (unknown risk is omitted)."""
        rs = self._latest_rules(ld.program.id)
        if rs is None or rs.verification_status != "verified" or ld.tree is None:
            return {}
        evals = validation_rules.evaluate(ld.tree, self._marks(ld, "actual"), self._rules(ld, rs))
        out = {}
        for k in ld.tree.walk():
            if ld.tree.nodes[k].kind == "subject":
                r = validation_rules.subject_risk(ld.tree, evals, k)
                if r is not None:
                    out[k] = r
        return out

    # ---------------------------------------------------------------- programs ----
    @staticmethod
    def _program_out(p: AcademicProgram) -> ProgramOut:
        return ProgramOut(
            id=p.id, title=p.title, academic_year=p.academic_year, cohort=p.cohort,
            specialization=p.specialization, provenance=p.provenance, revision=p.revision,
        )

    def list_programs(self, user_id: str) -> list[ProgramOut]:
        rows = self.db.scalars(
            select(AcademicProgram).where(AcademicProgram.user_id == user_id).order_by(AcademicProgram.created_at)
        )
        return [self._program_out(p) for p in rows]

    def create_program(self, user_id: str, body: ProgramCreate, provenance: str = "manual") -> ProgramOut:
        p = AcademicProgram(user_id=user_id, provenance=provenance, **body.model_dump())
        self.db.add(p)
        self.db.flush()
        return self._program_out(p)

    # -------------------------------------------------------------- curriculum ----
    def _curriculum_out(self, ld: _Loaded, removed: int = 0) -> CurriculumOut:
        out: list[NodeOut] = []
        if ld.tree is not None:
            root_w = domain.leaf_weights(ld.tree, ld.tree.root)
            for n in ld.nodes:
                parent = n.parent_key
                if parent is None:
                    in_parent = None
                else:
                    siblings = ld.tree.children[parent]
                    total = sum((Decimal(ld.by_key[s].weight) for s in siblings), Decimal(0))
                    in_parent = Decimal(n.weight) / total
                in_root = sum((root_w[leaf] for leaf in ld.tree.leaves_under(n.key)), Decimal(0))
                out.append(
                    NodeOut(
                        id=n.id, key=n.key, parent_key=n.parent_key, kind=n.kind, title=n.title,  # type: ignore[arg-type]
                        weight=Decimal(n.weight), weight_mode=n.weight_mode,
                        scale_min=Decimal(n.scale_min), scale_max=Decimal(n.scale_max),
                        subject_id=n.subject_id, event_id=n.event_id, is_leaf=ld.tree.is_leaf(n.key),
                        weight_in_parent=in_parent, weight_in_root=in_root,
                    )
                )
        return CurriculumOut(program_id=ld.program.id, revision=ld.program.revision, nodes=out, removed_records=removed)

    def get_curriculum(self, user_id: str, program_id: str) -> CurriculumOut:
        return self._curriculum_out(self._load(user_id, program_id))

    def put_curriculum(self, user_id: str, program_id: str, body: CurriculumPut) -> CurriculumOut:
        ld = self._load(user_id, program_id)
        check_revision(ld.program.revision, body.expected_revision)
        specs = [
            domain.NodeSpec(
                key=n.key, parent_key=n.parent_key, kind=n.kind, title=n.title, weight=n.weight,
                weight_mode=n.weight_mode, scale_min=n.scale_min, scale_max=n.scale_max,
                subject_id=n.subject_id, event_id=n.event_id,
            )
            for n in body.nodes
        ]
        try:
            tree = domain.build_tree(specs)
        except domain.TreeError as exc:
            raise ValidationFailed("Invalid curriculum", details=exc.errors, code="invalid_curriculum") from exc

        # Marks/objectives on kept nodes must stay valid in the new tree.
        records = list(self.db.scalars(select(GradeRecord).where(GradeRecord.program_id == ld.program.id)))
        errors = []
        for r in records:
            old = ld.by_id.get(r.node_id)
            if old is None or old.key not in tree.nodes:
                continue
            if not tree.is_leaf(old.key):
                errors.append({"key": old.key, "code": "graded_node_not_leaf", "message": "graded node gained children"})
            elif domain.check_mark(tree.nodes[old.key], Decimal(r.value)):
                errors.append({"key": old.key, "code": "mark_out_of_scale", "message": "existing mark outside new scale"})
        if errors:
            raise ValidationFailed("Curriculum conflicts with recorded marks", details=errors, code="invalid_curriculum")

        removed = 0
        for old in ld.nodes:
            if old.key not in tree.nodes:
                removed += self.db.scalar(
                    select(func.count()).select_from(GradeRecord).where(GradeRecord.node_id == old.id)
                ) or 0
                for r in [r for r in records if r.node_id == old.id]:
                    self.db.delete(r)
                for o in self.db.scalars(select(AcademicObjective).where(AcademicObjective.node_id == old.id)):
                    self.db.delete(o)
                self.db.delete(old)
        self.db.flush()
        for i, n in enumerate(body.nodes):
            row = ld.by_key.get(n.key) or AcademicNode(program_id=ld.program.id, key=n.key)
            row.parent_key, row.ordinal, row.kind, row.title = n.parent_key, i, n.kind, n.title
            row.weight, row.weight_mode = str(n.weight), n.weight_mode
            row.scale_min, row.scale_max = str(n.scale_min), str(n.scale_max)
            row.subject_id, row.event_id = n.subject_id, n.event_id
            self.db.add(row)
        self._bump(ld.program, "curriculum.changed", ld.program.id, removed_records=removed)
        self.db.flush()
        return self._curriculum_out(self._load(user_id, program_id), removed)

    # ----------------------------------------------------------------- records ----
    def _record_out(self, r: GradeRecord, node: AcademicNode) -> RecordOut:
        return RecordOut(
            id=r.id, program_id=r.program_id, node_id=r.node_id, node_key=node.key, kind=r.kind,  # type: ignore[arg-type]
            value=Decimal(r.value), value_on_20=domain.to_twenty(_spec(node), Decimal(r.value)),
            provenance=r.provenance, observed_at=r.observed_at, revision=r.revision,
        )

    def _leaf_for_mark(self, user_id: str, node_id: str, value: Decimal) -> tuple[_Loaded, AcademicNode]:
        node = self.db.get(AcademicNode, node_id)
        if node is None:
            raise NotFound("Node not found")
        ld = self._load(user_id, node.program_id)
        tree = self._require_tree(ld)
        if not tree.is_leaf(node.key):
            raise ValidationFailed("Marks can only be recorded on leaf assessments", code="not_a_leaf")
        msg = domain.check_mark(tree.nodes[node.key], value)
        if msg:
            raise ValidationFailed(msg, code="mark_out_of_scale")
        return ld, node

    def list_records(self, user_id: str, program_id: str) -> list[RecordOut]:
        ld = self._load(user_id, program_id)
        rows = self.db.scalars(
            select(GradeRecord).where(GradeRecord.program_id == program_id).order_by(GradeRecord.created_at)
        )
        return [self._record_out(r, ld.by_id[r.node_id]) for r in rows]

    def create_record(self, user_id: str, body: RecordCreate, provenance: str = "manual") -> RecordOut:
        ld, node = self._leaf_for_mark(user_id, body.node_id, body.value)
        exists = self.db.scalar(
            select(GradeRecord).where(GradeRecord.node_id == node.id, GradeRecord.kind == body.kind)
        )
        if exists is not None:
            raise Conflict("A record of this kind already exists; PATCH it instead", code="record_exists",
                           details={"record_id": exists.id})
        r = GradeRecord(
            user_id=user_id, program_id=ld.program.id, node_id=node.id, kind=body.kind,
            value=str(body.value), provenance=provenance, observed_at=body.observed_at,
        )
        self.db.add(r)
        self.db.flush()
        self._bump(ld.program, "grade.recorded", r.id, node_id=node.id, kind=body.kind)
        return self._record_out(r, node)

    def _owned_record(self, user_id: str, record_id: str) -> GradeRecord:
        r = self.db.get(GradeRecord, record_id)
        if r is None or r.user_id != user_id:
            raise NotFound("Record not found")
        return r

    def patch_record(self, user_id: str, record_id: str, body: RecordPatch) -> RecordOut:
        r = self._owned_record(user_id, record_id)
        check_revision(r.revision, body.expected_revision)
        ld, node = self._leaf_for_mark(user_id, r.node_id, body.value)
        if Decimal(r.value) != body.value:
            r.value = str(body.value)
            r.revision += 1
            self._bump(ld.program, "grade.updated", r.id, node_id=node.id, kind=r.kind)
        return self._record_out(r, node)

    def delete_record(self, user_id: str, record_id: str) -> None:
        r = self._owned_record(user_id, record_id)
        p = self._program(user_id, r.program_id)
        self.db.delete(r)
        self._bump(p, "grade.deleted", r.id, node_id=r.node_id)

    # -------------------------------------------------------------- objectives ----
    def _objective_out(self, o: AcademicObjective, node: AcademicNode) -> ObjectiveOut:
        return ObjectiveOut(
            id=o.id, program_id=o.program_id, node_id=o.node_id, node_key=node.key,
            target=Decimal(o.target), active=o.active, revision=o.revision,
        )

    def list_objectives(self, user_id: str, program_id: str) -> list[ObjectiveOut]:
        ld = self._load(user_id, program_id)
        rows = self.db.scalars(
            select(AcademicObjective).where(AcademicObjective.program_id == program_id)
            .order_by(AcademicObjective.created_at, AcademicObjective.id)
        )
        return [self._objective_out(o, ld.by_id[o.node_id]) for o in rows]

    def create_objectives(self, user_id: str, body: ObjectiveCreate) -> list[ObjectiveOut]:
        if (body.node_id is None) == (body.node_kind is None):
            raise ValidationFailed("Provide exactly one of node_id or node_kind")
        ld = self._load(user_id, body.program_id)
        self._require_tree(ld)
        if body.node_id is not None:
            if body.node_id not in ld.by_id:
                raise NotFound("Node not found in this program")
            targets = [ld.by_id[body.node_id]]
        else:
            targets = [n for n in ld.nodes if n.kind == body.node_kind]
            if not targets:
                raise ValidationFailed(f"No node of kind {body.node_kind} in this program")
        out = []
        for node in targets:  # upsert: one objective per node
            o = self.db.scalar(select(AcademicObjective).where(AcademicObjective.node_id == node.id))
            if o is None:
                o = AcademicObjective(user_id=user_id, program_id=ld.program.id, node_id=node.id, target=str(body.target))
                self.db.add(o)
            else:
                o.target, o.active = str(body.target), True
                o.revision += 1
            self.db.flush()
            out.append(self._objective_out(o, node))
        self._bump(ld.program, "academic.objective_changed", ld.program.id, count=len(out))
        return out

    def _owned_objective(self, user_id: str, objective_id: str) -> AcademicObjective:
        o = self.db.get(AcademicObjective, objective_id)
        if o is None or o.user_id != user_id:
            raise NotFound("Objective not found")
        return o

    def patch_objective(self, user_id: str, objective_id: str, body: ObjectivePatch) -> ObjectiveOut:
        o = self._owned_objective(user_id, objective_id)
        check_revision(o.revision, body.expected_revision)
        if body.target is not None:
            o.target = str(body.target)
        if body.active is not None:
            o.active = body.active
        o.revision += 1
        p = self._program(user_id, o.program_id)
        self._bump(p, "academic.objective_changed", o.id)
        return self._objective_out(o, self.db.get(AcademicNode, o.node_id))  # type: ignore[arg-type]

    def delete_objective(self, user_id: str, objective_id: str) -> None:
        o = self._owned_objective(user_id, objective_id)
        p = self._program(user_id, o.program_id)
        self.db.delete(o)
        self._bump(p, "academic.objective_changed", o.id, action="deleted")

    # ----------------------------------------------------------- calculations ----
    def summary(self, user_id: str, program_id: str, node_id: str | None) -> ProgramSummaryOut:
        ld = self._load(user_id, program_id)
        tree = self._require_tree(ld)
        actual, estimated = self._marks(ld, "actual"), self._marks(ld, "estimated")
        keys = tree.walk()
        if node_id is not None:
            if node_id not in ld.by_id:
                raise NotFound("Node not found in this program")
            start = ld.by_id[node_id].key
            keys = [k for k in keys if k == start or start in tree.ancestors(k)]
        out = []
        for k in keys:
            s = domain.summarize(tree, actual, k, estimated)
            n = ld.by_key[k]
            out.append(
                SummaryOut(
                    node_id=n.id, node_key=k, kind=n.kind, title=n.title,  # type: ignore[arg-type]
                    coverage=s.coverage, provisional=s.provisional, final=s.final, forecast=s.forecast,
                    forecast_coverage=s.forecast_coverage, remaining_weight=s.remaining_weight,
                    bound_min=s.bound_min, bound_max=s.bound_max,
                )
            )
        return ProgramSummaryOut(program_id=program_id, revision=ld.program.revision, nodes=out)

    def _assumption_map(self, ld: _Loaded, tree: domain.CurriculumTree, items: list[AssumptionIn],
                        actual: dict[str, Decimal]) -> dict[str, Decimal]:
        result: dict[str, Decimal] = {}
        for a in items:
            node = ld.by_id.get(a.node_id)
            if node is None:
                raise NotFound(f"Node {a.node_id} not found in this program")
            if not tree.is_leaf(node.key):
                raise ValidationFailed("Assumptions apply to leaf assessments only", code="not_a_leaf")
            if node.key in actual:
                raise ValidationFailed(
                    "Cannot simulate over an actual mark", code="assumption_on_graded_leaf", details=[a.node_id]
                )
            msg = domain.check_mark(tree.nodes[node.key], a.value)
            if msg:
                raise ValidationFailed(msg, code="mark_out_of_scale")
            result[node.key] = a.value
        return result

    def _requirement_out(self, ld: _Loaded, r: domain.Requirement) -> RequirementOut:
        return RequirementOut(
            objective_id=r.objective_id, node_id=ld.by_key[r.node_key].id, node_key=r.node_key,
            target=r.target, required_mean=r.required_mean, feasibility=r.feasibility,  # type: ignore[arg-type]
            bound_min=r.bound_min, bound_max=r.bound_max, remaining_weight=r.remaining_weight,
            reason_codes=list(r.reason_codes),
        )

    def required(self, user_id: str, body: RequiredRequest) -> RequiredOut:
        ld = self._load(user_id, body.program_id)
        tree = self._require_tree(ld)
        actual = self._marks(ld, "actual")
        assumptions = self._assumption_map(ld, tree, body.assumptions, actual)
        reqs = [domain.required(tree, actual, obj, assumptions) for _, obj in self._objectives(ld, body.objective_ids)]
        leaves = domain.needed_per_leaf(tree, actual, reqs, assumptions)
        return RequiredOut(
            program_id=ld.program.id,
            revision=ld.program.revision,
            assumptions=body.assumptions,
            requirements=[self._requirement_out(ld, r) for r in reqs],
            leaves=[
                LeafNeedOut(node_id=ld.by_key[x.leaf_key].id, node_key=x.leaf_key, needed=x.needed,
                            objective_ids=list(x.objective_ids))
                for x in leaves
            ],
        )

    def simulate(self, user_id: str, body: SimulateRequest) -> SimulateOut:
        """What-if overlay: computed in memory only, never written (R39)."""
        ld = self._load(user_id, body.program_id)
        check_revision(ld.program.revision, body.expected_revision)
        tree = self._require_tree(ld)
        actual = self._marks(ld, "actual")
        overlay = self._assumption_map(ld, tree, body.values, actual)
        projected_marks = {**actual, **overlay}
        projections = []
        for k in tree.walk():
            if tree.is_leaf(k):
                continue
            base = domain.summarize(tree, actual, k)
            proj = domain.summarize(tree, projected_marks, k)
            delta = (
                proj.provisional - base.provisional
                if proj.provisional is not None and base.provisional is not None
                else None
            )
            projections.append(
                ProjectionOut(
                    node_id=ld.by_key[k].id, node_key=k, title=ld.by_key[k].title,
                    baseline=base.provisional, projected=proj.provisional, delta=delta,
                    baseline_coverage=base.coverage, projected_coverage=proj.coverage,
                )
            )
        reqs = [domain.required(tree, actual, obj, overlay) for _, obj in self._objectives(ld)]
        warnings = ["SIMULATION_ONLY_NOT_SAVED", "OFFICIAL_RULES_UNKNOWN"]
        if any(r.feasibility == "impossible" for r in reqs):
            warnings.append("SOME_TARGETS_IMPOSSIBLE")
        return SimulateOut(
            program_id=ld.program.id, base_revision=ld.program.revision, projections=projections,
            requirements=[self._requirement_out(ld, r) for r in reqs], warnings=warnings,
        )

    def signals(self, user_id: str, program_id: str) -> SignalsOut:
        ld = self._load(user_id, program_id)
        tree = self._require_tree(ld)
        sigs = domain.academic_signals(
            tree, self._marks(ld, "actual"), [o for _, o in self._objectives(ld)], self._verified_risk(ld)
        )
        return SignalsOut(
            program_id=program_id,
            input_revision=ld.program.revision,
            signals=[
                SignalOut(
                    node_id=ld.by_key[s.node_key].id, node_key=s.node_key, subject_id=s.subject_id,
                    effective_weight=s.effective_weight, remaining_weight=s.remaining_weight,
                    coverage=s.coverage, provisional=s.provisional, target=s.target,
                    required_mean=s.required_mean, feasibility=s.feasibility,  # type: ignore[arg-type]
                    score=s.score, reason_codes=list(s.reason_codes),
                )
                for s in sigs
            ],
        )


# ---- query interfaces for the planner / analytics (read-only) ---------------------------
@dataclass(frozen=True)
class SubjectSignal:
    subject_id: str
    score: float
    feasibility: str
    required_mean: str | None
    reason_codes: tuple[str, ...]


def academic_signals_by_subject(db: Session, user_id: str) -> dict[str, SubjectSignal]:
    """Highest academic score per linked subject across the user's programs."""
    svc = GradesService(db)
    out: dict[str, SubjectSignal] = {}
    for p in db.scalars(select(AcademicProgram).where(AcademicProgram.user_id == user_id)):
        try:
            ld = svc._load(user_id, p.id)
        except domain.TreeError:
            continue
        if ld.tree is None:
            continue
        objectives = [o for _, o in svc._objectives(ld)]
        for s in domain.academic_signals(ld.tree, svc._marks(ld, "actual"), objectives, svc._verified_risk(ld)):
            if s.subject_id is None or s.score is None:
                continue
            cur = out.get(s.subject_id)
            if cur is None or float(s.score) > cur.score:
                out[s.subject_id] = SubjectSignal(
                    s.subject_id, float(s.score), s.feasibility,
                    None if s.required_mean is None else str(domain.q(s.required_mean)), s.reason_codes,
                )
    return out


def grades_revision(db: Session, user_id: str) -> int:
    return db.scalar(
        select(func.coalesce(func.sum(AcademicProgram.revision), 0)).where(AcademicProgram.user_id == user_id)
    ) or 0
