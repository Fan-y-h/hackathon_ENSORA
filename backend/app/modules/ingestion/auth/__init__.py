"""Authentication adapters and setup automation for Ensora+ P1 sources."""

from .ensai import (
    BrowserProfileLock,
    EnsaiAuthAdapter,
    EnsaiSessionProbe,
    PlaywrightEnvironmentStatus,
    detect_playwright_environment,
)
from .google_cloud import (
    GOOGLE_CALENDAR_API,
    GOOGLE_DRIVE_API,
    GoogleCloudProvisioningReport,
    GoogleCloudSetupHelper,
    validate_google_credentials_file,
)
from .google_oauth import GoogleOAuthAdapter
from .onboarding import OnboardingOrchestrator, OnboardingReport

__all__ = [
    "BrowserProfileLock",
    "EnsaiAuthAdapter",
    "EnsaiSessionProbe",
    "PlaywrightEnvironmentStatus",
    "detect_playwright_environment",
    "GOOGLE_CALENDAR_API",
    "GOOGLE_DRIVE_API",
    "GoogleCloudProvisioningReport",
    "GoogleCloudSetupHelper",
    "validate_google_credentials_file",
    "GoogleOAuthAdapter",
    "OnboardingOrchestrator",
    "OnboardingReport",
]
