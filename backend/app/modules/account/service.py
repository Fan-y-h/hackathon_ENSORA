"""Learning profile use cases: availability, preferences and goals."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.common import check_revision
from app.core.db import new_id
from app.core.errors import NotFound, ValidationFailed
from app.core.events import record_event
from app.core.identity import User
from app.modules.account.domain import (
    PlanningPreferences,
    WeeklyWindow,
    check_preferences,
    is_onboarding_complete,
)
from app.modules.account.models import Preferences, Profile
from app.modules.account.schemas import ProfileOut, ProfilePatch

# Fields of the profile row that a PATCH may set directly.
_PROFILE_FIELDS = ("cohort", "track", "academic_year", "locale", "timezone", "self_assessment")
_NON_NULLABLE = {"locale", "timezone", "display_name"}


def _windows(raw: list[dict]) -> list[WeeklyWindow]:
    return [WeeklyWindow.from_dict(w) for w in raw]


class AccountService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # -- loading --------------------------------------------------------------------
    def _load(self, user_id: str) -> tuple[User, Profile, Preferences]:
        user = self.db.get(User, user_id)
        if user is None:
            raise NotFound("User not found")
        profile = self.db.scalar(select(Profile).where(Profile.user_id == user_id))
        prefs = self.db.scalar(select(Preferences).where(Preferences.user_id == user_id))
        if profile is None:
            profile = Profile(user_id=user_id, goals=[])
            self.db.add(profile)
        if prefs is None:
            prefs = Preferences(
                user_id=user_id,
                work_windows=[],
                rest_windows=[],
                preferred_formats=[],
                provider_policy={"external_allowed": False, "preferred_mode": "mock"},
            )
            self.db.add(prefs)
        self.db.flush()
        return user, profile, prefs

    def get(self, user_id: str) -> ProfileOut:
        return self._to_out(*self._load(user_id))

    # -- mutation -------------------------------------------------------------------
    def patch(self, user_id: str, patch: ProfilePatch) -> ProfileOut:
        user, profile, prefs = self._load(user_id)
        check_revision(profile.revision, patch.expected_revision)
        fields = patch.model_fields_set - {"expected_revision"}

        for name in fields & _NON_NULLABLE:
            if getattr(patch, name) is None:
                raise ValidationFailed(f"{name} cannot be null", details=[{"field": name}])

        profile_changed = False
        if "display_name" in fields and patch.display_name != user.display_name:
            user.display_name = patch.display_name  # type: ignore[assignment]
            profile_changed = True
        for name in fields & set(_PROFILE_FIELDS):
            value = getattr(patch, name)
            if getattr(profile, name) != value:
                setattr(profile, name, value)
                profile_changed = True
        if "goals" in fields:
            new_goals = self._merge_goals(profile.goals, patch.goals or [])
            if new_goals != profile.goals:
                profile.goals = new_goals
                profile_changed = True

        prefs_changed = False
        if "preferences" in fields and patch.preferences is not None:
            prefs_changed = self._apply_preferences(prefs, patch)

        timezone_changed = "timezone" in fields and profile_changed
        if profile_changed or prefs_changed:
            profile.revision += 1
            record_event(
                self.db,
                user_id=user_id,
                type="ProfileChanged",
                entity_id=profile.id,
                revision=profile.revision,
            )
        if prefs_changed or timezone_changed:
            # Availability is a planner input: a change marks the plan dirty.
            prefs.revision += 1
            record_event(
                self.db,
                user_id=user_id,
                type="PreferencesChanged",
                entity_id=prefs.id,
                revision=prefs.revision,
            )
        self.db.flush()
        return self._to_out(user, profile, prefs)

    def _apply_preferences(self, prefs: Preferences, patch: ProfilePatch) -> bool:
        p = patch.preferences
        assert p is not None
        updates: dict = {}
        for name in p.model_fields_set:
            value = getattr(p, name)
            if value is None:
                raise ValidationFailed(
                    f"preferences.{name} cannot be null", details=[{"field": name}]
                )
            if name in ("work_windows", "rest_windows"):
                value = sorted(
                    (w.model_dump() for w in value), key=lambda w: (w["weekday"], w["start"])
                )
            elif name == "provider_policy":
                value = value.model_dump()
            elif name == "notification_settings":
                patch_values = {k: v for k, v in value.model_dump(exclude_unset=True).items()}
                value = {**(prefs.notification_settings or {}), **patch_values}
                value = {k: v for k, v in value.items() if v is not None}
            elif name == "preferred_formats":
                value = list(dict.fromkeys(value))  # dedupe, keep order
            updates[name] = value

        merged = {
            c: updates.get(c, getattr(prefs, c))
            for c in (
                "work_windows",
                "rest_windows",
                "daily_max_min",
                "weekly_max_min",
                "min_session_min",
                "max_session_min",
            )
        }
        errors = check_preferences(
            work_windows=_windows(merged["work_windows"]),
            rest_windows=_windows(merged["rest_windows"]),
            daily_max_min=merged["daily_max_min"],
            weekly_max_min=merged["weekly_max_min"],
            min_session_min=merged["min_session_min"],
            max_session_min=merged["max_session_min"],
        )
        if errors:
            raise ValidationFailed("Inconsistent preferences", details=errors)

        changed = False
        for name, value in updates.items():
            if getattr(prefs, name) != value:
                setattr(prefs, name, value)
                changed = True
        return changed

    @staticmethod
    def _merge_goals(existing: list[dict], incoming: list) -> list[dict]:
        """The list replaces the previous one; known IDs are kept, new goals get an ID."""
        known = {g["id"] for g in existing}
        result: list[dict] = []
        seen: set[str] = set()
        for goal in incoming:
            data = goal.model_dump(mode="json")
            if data["id"] is None or data["id"] not in known:
                data["id"] = new_id()
            if data["id"] in seen:
                raise ValidationFailed("Duplicate goal id", details=[{"id": data["id"]}])
            seen.add(data["id"])
            result.append(data)
        return result

    @staticmethod
    def _to_out(user: User, profile: Profile, prefs: Preferences) -> ProfileOut:
        return ProfileOut.model_validate(
            {
                "user_id": user.id,
                "email": user.email,
                "display_name": user.display_name,
                "cohort": profile.cohort,
                "track": profile.track,
                "academic_year": profile.academic_year,
                "locale": profile.locale,
                "timezone": profile.timezone,
                "goals": profile.goals,
                "self_assessment": profile.self_assessment or {},
                "preferences": {
                    "work_windows": prefs.work_windows,
                    "rest_windows": prefs.rest_windows,
                    "daily_max_min": prefs.daily_max_min,
                    "weekly_max_min": prefs.weekly_max_min,
                    "min_session_min": prefs.min_session_min,
                    "max_session_min": prefs.max_session_min,
                    "preferred_formats": prefs.preferred_formats,
                    "provider_policy": prefs.provider_policy,
                    "repetition_algorithm": prefs.repetition_algorithm,
                    "desired_retention": prefs.desired_retention,
                    "planner_algorithm": prefs.planner_algorithm,
                    "notification_settings": prefs.notification_settings or {},
                    "revision": prefs.revision,
                },
                "onboarding_complete": is_onboarding_complete(profile.cohort, prefs.work_windows),
                "revision": profile.revision,
                "updated_at": max(profile.updated_at, prefs.updated_at),
            }
        )


def get_planning_preferences(db: Session, user_id: str) -> PlanningPreferences:
    """Query interface used by the planner module (no cross-module ORM writes)."""
    _, profile, prefs = AccountService(db)._load(user_id)
    return PlanningPreferences(
        timezone=profile.timezone,
        work_windows=tuple(_windows(prefs.work_windows)),
        rest_windows=tuple(_windows(prefs.rest_windows)),
        daily_max_min=prefs.daily_max_min,
        weekly_max_min=prefs.weekly_max_min,
        min_session_min=prefs.min_session_min,
        max_session_min=prefs.max_session_min,
        revision=prefs.revision,
        planner_algorithm=prefs.planner_algorithm,
        repetition_algorithm=prefs.repetition_algorithm,
        desired_retention=prefs.desired_retention,
        notification_settings=dict(prefs.notification_settings or {}),
    )


def get_self_assessment(db: Session, user_id: str) -> dict[str, int]:
    _, profile, _ = AccountService(db)._load(user_id)
    return dict(profile.self_assessment or {})
