"""Catalog port: the single place where Person-3 modules resolve subject / concept references.

The catalog (subjects, chapters, concepts, concept_edges) belongs to C1 and does not exist
yet. Until it does, ``InterimCatalog`` accepts any well-formed identifier and serves
prerequisites from the interim ``concept_prerequisites`` table. When C1 delivers the catalog,
implement ``CatalogPort`` over the catalog query interface and return it from
``get_catalog`` — planner, learning, analytics and grades code does not change.
"""

from __future__ import annotations

import re
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailed

ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,36}$")


class CatalogPort(Protocol):
    def check_subject(self, user_id: str, subject_id: str | None) -> None: ...
    def check_concepts(self, user_id: str, concept_ids: list[str]) -> None: ...
    def prerequisites(self, user_id: str) -> set[tuple[str, str]]: ...
    def is_authoritative(self) -> bool: ...


class InterimCatalog:
    """Format-only validation; references are not checked for existence (no catalog yet)."""

    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _check(kind: str, values: list[str]) -> None:
        bad = [v for v in values if not ID_PATTERN.match(v)]
        if bad:
            raise ValidationFailed(f"Malformed {kind} identifier(s)", details=bad, code=f"invalid_{kind}_id")

    def check_subject(self, user_id: str, subject_id: str | None) -> None:
        if subject_id is not None:
            self._check("subject", [subject_id])

    def check_concepts(self, user_id: str, concept_ids: list[str]) -> None:
        self._check("concept", list(concept_ids))

    def prerequisites(self, user_id: str) -> set[tuple[str, str]]:
        from app.modules.learning.models import ConceptEdge

        return {
            (e.prerequisite_id, e.dependent_id)
            for e in self.db.scalars(
                select(ConceptEdge).where(ConceptEdge.user_id == user_id, ConceptEdge.confirmed.is_(True))
            )
        }

    def is_authoritative(self) -> bool:
        return False


def get_catalog(db: Session) -> CatalogPort:
    """Composition point: swap for the C1 catalog adapter once it exists."""
    return InterimCatalog(db)
