"""Consent-gated adapter orchestration."""
from datetime import UTC, datetime
import logging
from threading import RLock

from sqlalchemy import insert, literal_column, select, update

from ..contracts.authentication import (
    AuthenticationResult, ConnectionStatus, ProfileBusy, ReauthenticationRequired, ServiceAccessDenied,
)
from ..contracts.sources import SourceKind
from .database import connections, consents, new_id


logger = logging.getLogger(__name__)
INSTITUTIONAL_SOURCES = frozenset({SourceKind.MOODLE, SourceKind.PAMPLEMOUSSE})


def now():
    return datetime.now(UTC).isoformat()


def enable_auth_diagnostics():
    """Enable only status, operation and evidence tracing for the local operator."""
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("[ensora-auth-state] %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False


class ConsentManager:
    def __init__(self, engine, connection_ids):
        self.engine = engine
        self.connection_ids = connection_ids

    def record(self, source, purpose, granted):
        with self.engine.begin() as db:
            db.execute(insert(consents).values(id=new_id(), connection_id=self.connection_ids[source],
                                               purpose=purpose, granted=granted, recorded_at=now()))

    def allowed(self, source, purpose):
        with self.engine.connect() as db:
            return bool(db.scalar(select(consents.c.granted).where(
                consents.c.connection_id == self.connection_ids[source],
                consents.c.purpose == purpose,
            ).order_by(literal_column("consents.rowid").desc()).limit(1)))


class AuthenticationManager:
    def __init__(self, engine, connection_ids, consent, adapters, *, shared_ensai_session=True,
                 require_institutional_evidence=False):
        self.engine = engine
        self.connection_ids = connection_ids
        self.consent = consent
        self.adapters = adapters
        self.require_institutional_evidence = require_institutional_evidence
        self.locks = {source: RLock() for source in SourceKind}
        if shared_ensai_session:
            shared_lock = RLock()
            self.locks.update(dict.fromkeys(INSTITUTIONAL_SOURCES, shared_lock))

    def authorize(self, source, inputs=None):
        return self._run(source, "authorize", inputs or {})

    def reauthorize(self, source, inputs=None):
        return self._run(source, "reauthorize", inputs or {})

    def validate(self, source):
        return self._run(source, "validate")

    def _run(self, source, operation, *args):
        with self.locks[source]:
            previous = self.status(source)
            adapter = self.adapters.get(source)
            if not self.consent.allowed(source, "read"):
                result = AuthenticationResult(status=ConnectionStatus.ACCESS_DENIED, reason="consent_required")
            elif source in (SourceKind.LOCAL, SourceKind.ACADEMIC_IMPORT):
                result = AuthenticationResult(status=ConnectionStatus.CONNECTED)
            elif adapter is None:
                result = AuthenticationResult(status=ConnectionStatus.UNAVAILABLE, reason="adapter_missing")
            else:
                stage = "adapter_result"
                try:
                    action = getattr(adapter, operation, None)
                    if action is None and operation == "reauthorize":
                        action = adapter.authorize
                    result = AuthenticationResult.model_validate(action(*args))
                    self._trace(source, operation, "adapter_result", result)
                    if self._needs_post_auth_probe(source, operation, result):
                        self._trace(source, operation, "fallback_probe", result)
                        stage = "fallback_result"
                        result = AuthenticationResult.model_validate(adapter.validate())
                        self._trace(source, operation, stage, result)
                except (ReauthenticationRequired, ServiceAccessDenied) as exc:
                    result = self.failure_result(exc)
                    self._trace(source, operation, stage, result)
                except ProfileBusy:
                    result = AuthenticationResult(status=ConnectionStatus.UNAVAILABLE, reason="profile_busy")
                    self._trace(source, operation, stage, result)
                except Exception:
                    result = AuthenticationResult(status=ConnectionStatus.ERROR, reason="adapter_error")
                    self._trace(source, operation, stage, result)
            if source in INSTITUTIONAL_SOURCES:
                result = self._institutional_result(result, previous, operation)
                if previous == ConnectionStatus.REAUTH_REQUIRED and result.status in (
                    ConnectionStatus.UNAVAILABLE, ConnectionStatus.ERROR,
                ):
                    # Keep the acquisition challenge through transient probe failure.
                    self._trace(source, operation, "retained_reauth", result)
                    return result
            self.set_status(source, result.status)
            self._trace(source, operation, "persisted", result)
            return result

    @staticmethod
    def _needs_post_auth_probe(source, operation, result):
        if operation == "validate" or result.status != ConnectionStatus.CONNECTED:
            return False
        if source not in INSTITUTIONAL_SOURCES:
            return True
        # The adapter already checked its protected endpoint in the interactive context.
        # A separate validate/acquisition checks persisted session reuse independently.
        return result.service_access == "unknown" and result.session_state != "reauth_required"

    @staticmethod
    def _trace(source, operation, stage, result):
        if source in INSTITUTIONAL_SOURCES:
            logger.debug("source=%s operation=%s stage=%s status=%s reason=%s session=%s service=%s",
                         source.value, operation, stage, result.status.value, result.reason or "none",
                         result.session_state, result.service_access)

    def _institutional_result(self, result, previous, operation):
        if result.service_access == "denied":
            return result.model_copy(update={"status": ConnectionStatus.ACCESS_DENIED,
                                             "reason": "service_access_denied"})
        if result.session_state == "reauth_required":
            return result.model_copy(update={"status": ConnectionStatus.REAUTH_REQUIRED,
                                             "reason": "cas_login_required"})
        if (result.status == ConnectionStatus.CONNECTED and result.service_access != "verified"
                and (self.require_institutional_evidence or result.session_state == "active")):
            return AuthenticationResult(status=ConnectionStatus.REAUTH_REQUIRED,
                                        reason="service_verification_required", session_state=result.session_state)
        if (operation == "validate" and previous == ConnectionStatus.REAUTH_REQUIRED
                and result.status == ConnectionStatus.CONNECTED and result.service_access != "verified"):
            # An old landing-page probe cannot clear a recorded acquisition challenge.
            return AuthenticationResult(status=ConnectionStatus.REAUTH_REQUIRED,
                                        reason="service_verification_required",
                                        session_state=result.session_state)
        return result

    def guard_acquisition(self, source, operation, *args):
        with self.locks[source]:
            try:
                return operation(*args)
            except (ReauthenticationRequired, ServiceAccessDenied) as error:
                self.set_status(source, self.failure_result(error).status)
                raise

    @staticmethod
    def failure_result(error):
        if isinstance(error, ServiceAccessDenied):
            result = AuthenticationResult(status=ConnectionStatus.ACCESS_DENIED, reason="service_access_denied",
                                          service_access="denied")
        else:
            reason = error.reason
            cas = reason == "cas_login_required"
            result = AuthenticationResult(status=ConnectionStatus.REAUTH_REQUIRED, reason=reason,
                                          session_state="reauth_required" if cas else "unknown")
        return result

    def status(self, source):
        with self.engine.connect() as db:
            state = db.scalar(select(connections.c.status).where(connections.c.id == self.connection_ids[source]))
        return ConnectionStatus(state)

    def set_status(self, source, status):
        with self.engine.begin() as db:
            db.execute(update(connections).where(connections.c.id == self.connection_ids[source]).values(status=status))

    def disconnect(self, source):
        with self.locks[source]:
            for purpose in ("automatic_sync", "read"):
                self.consent.record(source, purpose, False)
            self.set_status(source, ConnectionStatus.NOT_CONFIGURED)
            adapter = self.adapters.get(source)
            if adapter is not None:
                try:
                    adapter.disconnect()
                except Exception as exc:
                    logger.warning("Source cleanup deferred for %s (%s)", source.value, type(exc).__name__)
                    return False
            return True
