"""Seed the local database with the SYNTHETIC agenda/profile fixture.

Usage (from the repository root, venv activated):
    python scripts/seed_demo.py [--fixture data/demo/agenda_fixture.json]

Idempotent: imported events are keyed by external_id (second run → 0 created).
Personal events and tasks are only created when the agenda has no manual items yet.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import app  # noqa: E402,F401  (sets SQLAlchemy env workaround)
from sqlalchemy import select  # noqa: E402

from app.core.common import SystemClock  # noqa: E402
from app.core.config import load_settings  # noqa: E402
from app.core.db import Database  # noqa: E402
from app.core.identity import LOCAL_USER_EMAIL, ensure_user  # noqa: E402
from app.modules.account.schemas import ProfilePatch  # noqa: E402
from app.modules.account.service import AccountService  # noqa: E402
from app.modules.grades.models import AcademicProgram  # noqa: E402
from app.modules.grades.schemas import CurriculumPut, ObjectiveCreate, ProgramCreate, RecordCreate  # noqa: E402
from app.modules.grades.service import GradesService  # noqa: E402
from app.modules.learning.models import Deck  # noqa: E402
from app.modules.learning.schemas import CardCreate, DeckCreate, QuizCreate  # noqa: E402
from app.modules.learning.service import LearningService  # noqa: E402
from app.modules.planner.models import Assessment, Event, Task  # noqa: E402
from app.modules.planner.schemas import AssessmentPatch, EventCreate, EventImportItem, TaskCreate  # noqa: E402
from app.modules.planner.service import AgendaService  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", default=str(ROOT / "data" / "demo" / "agenda_fixture.json"))
    args = parser.parse_args()
    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))

    db = Database(load_settings().database_url)
    db.create_schema()
    with db.session_factory() as session:
        user = ensure_user(session, LOCAL_USER_EMAIL, "Étudiant·e local·e")
        AccountService(session).patch(user.id, ProfilePatch.model_validate(fixture["profile"]))
        agenda = AgendaService(session, SystemClock())
        result = agenda.import_events(
            user.id,
            fixture["source"],
            [EventImportItem.model_validate(e) for e in fixture["events"]],
        )
        has_manual = session.scalar(
            select(Event.id).where(Event.user_id == user.id, Event.source == "manual")
        ) or session.scalar(select(Task.id).where(Task.user_id == user.id))
        if not has_manual:
            for e in fixture.get("personal_events", []):
                agenda.create_event(user.id, EventCreate.model_validate(e))
            for t in fixture.get("tasks", []):
                agenda.create_task(user.id, TaskCreate.model_validate(t))
        scope = fixture.get("exam_scope")
        if scope:
            ev = session.scalar(select(Event).where(
                Event.user_id == user.id, Event.source == fixture["source"], Event.external_id == scope["external_id"]))
            a = session.scalar(select(Assessment).where(Assessment.event_id == ev.id)) if ev else None
            if a is not None and a.scope_status != "confirmed":
                agenda.patch_assessment(user.id, a.id, AssessmentPatch(
                    scope_status="confirmed", concept_ids=scope["concept_ids"],
                    difficulty=scope.get("difficulty"), format=scope.get("format")))
        extras = seed_academic_and_learning(session, user.id, fixture)
        session.commit()
    print(
        f"[SYNTHETIC FIXTURE] events created={result.created} updated={result.updated} "
        f"unchanged={result.unchanged}; manual items {'kept' if has_manual else 'seeded'}; {extras}"
    )


def seed_academic_and_learning(session, user_id: str, fixture: dict) -> str:
    done = []
    clock = SystemClock()
    acad = fixture.get("academic")
    if acad and not session.scalar(select(AcademicProgram.id).where(AcademicProgram.user_id == user_id)):
        grades = GradesService(session, clock)
        p = grades.create_program(user_id, ProgramCreate.model_validate(acad["program"]), provenance="fixture")
        cur = grades.put_curriculum(user_id, p.id, CurriculumPut.model_validate({"nodes": acad["nodes"]}))
        ids = {n.key: n.id for n in cur.nodes}
        for key, value in acad.get("marks", {}).items():
            grades.create_record(user_id, RecordCreate(node_id=ids[key], value=value), provenance="fixture")
        if acad.get("objective_per_unit"):
            grades.create_objectives(user_id, ObjectiveCreate(
                program_id=p.id, node_kind="unit", target=acad["objective_per_unit"]))
        done.append("academic program seeded")
    learn = fixture.get("learning")
    if learn and not session.scalar(select(Deck.id).where(Deck.user_id == user_id)):
        svc = LearningService(session, clock)
        deck = svc.create_deck(user_id, DeckCreate(title=learn["deck"]["title"], subject_id=learn["deck"]["subject_id"]))
        for c in learn["deck"]["cards"]:
            svc.create_card(user_id, deck.id, CardCreate.model_validate(c))
        svc.create_quiz(user_id, QuizCreate.model_validate(learn["quiz"]))
        done.append("deck + quiz seeded")
    return ", ".join(done) or "academic/learning kept"


if __name__ == "__main__":
    main()
