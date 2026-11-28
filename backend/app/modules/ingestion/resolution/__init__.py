"""Academic entity resolution and reconciliation subsystem."""

from .entity_resolver import (
    AUTO_MATCH_THRESHOLD,
    REVIEW_MATCH_THRESHOLD,
    AcademicEntityResolver,
    CanonicalSubject,
    ManualCorrectionStore,
    normalize_text_key,
    significant_tokens,
)

__all__ = [
    "AUTO_MATCH_THRESHOLD",
    "REVIEW_MATCH_THRESHOLD",
    "AcademicEntityResolver",
    "CanonicalSubject",
    "ManualCorrectionStore",
    "normalize_text_key",
    "significant_tokens",
]
