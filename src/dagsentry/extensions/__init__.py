"""Public Provider API v1. Import extension contracts from this module."""

from dagsentry.domain.diagnosis import (
    DiagnosisSource,
    ErrorClassification,
    RetryDecision,
)
from dagsentry.domain.failure_event import (
    FailureState,
)
from dagsentry.domain.incident import (
    IncidentStatus,
)
from dagsentry.domain.notification import (
    NotificationErrorCategory,
    NotificationEvidence,
    NotificationPayload,
    NotificationProviderError,
)
from dagsentry.domain.recovery import (
    RecoveryNotificationPayload,
)
from dagsentry.domain.reporting import (
    DailyReportNotificationPayload,
)
from dagsentry.llm import (
    AIDiagnosisRequest,
    AIDiagnosisResponse,
    AIEvidence,
    ErrorSignatureContext,
    ExcerptLineContext,
    FailureMetadata,
    LLMCallMetadata,
    LLMProvider,
    LLMProviderError,
    LLMResult,
    RuleDiagnosisContext,
    ValidatedPriorDiagnosis,
)
from dagsentry.prompts import (
    AI_DIAGNOSIS_INSTRUCTIONS,
)
from dagsentry.providers.notification_adapter import (
    NotificationMessage,
)
from dagsentry.providers.notification_adapter import (
    NotificationProviderAdapter as NotificationProvider,
)

PROVIDER_API_VERSION = 1

__all__ = [
    "PROVIDER_API_VERSION",
    "DiagnosisSource",
    "ErrorClassification",
    "RetryDecision",
    "FailureState",
    "IncidentStatus",
    "NotificationErrorCategory",
    "NotificationEvidence",
    "NotificationPayload",
    "NotificationProviderError",
    "RecoveryNotificationPayload",
    "DailyReportNotificationPayload",
    "AIDiagnosisRequest",
    "AIDiagnosisResponse",
    "AIEvidence",
    "ErrorSignatureContext",
    "ExcerptLineContext",
    "FailureMetadata",
    "LLMCallMetadata",
    "LLMProvider",
    "LLMProviderError",
    "LLMResult",
    "RuleDiagnosisContext",
    "ValidatedPriorDiagnosis",
    "AI_DIAGNOSIS_INSTRUCTIONS",
    "NotificationMessage",
    "NotificationProvider",
]
