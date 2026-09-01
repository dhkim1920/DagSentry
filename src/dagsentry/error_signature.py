"""Versioned, deterministic Error Signature generation and persistence."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from dagsentry.log_processing import RelevantLogExcerpt
from dagsentry.models import ErrorSignatureRecord

FINGERPRINT_VERSION = 1

_SIGNATURE_MESSAGE = re.compile(
    r"(?i)\b(?:error|exception|critical|fatal|failed|failure|OOMKilled)\b|"
    r"out of memory|\bHTTP\s+[45]\d{2}\b"
)


class SignatureStatus(StrEnum):
    """Whether stable identifiers are sufficient to group an error."""

    SIGNABLE = "SIGNABLE"
    UNSIGNABLE = "UNSIGNABLE"


@dataclass(frozen=True)
class ErrorSignatureInput:
    """The only normalized fields allowed to influence a fingerprint."""

    operator_type: str | None
    exception_class: str | None
    vendor_error_code: str | None
    normalized_message: str | None
    application_stack_frame: str | None


@dataclass(frozen=True)
class CanonicalError:
    """Canonical fingerprint payload with explicit null fields."""

    operator_type: str | None
    exception_class: str | None
    vendor_error_code: str | None
    normalized_message: str | None
    application_stack_frame: str | None


@dataclass(frozen=True)
class ErrorSignatureCandidate:
    """A signable fingerprint or an explicit UNSIGNABLE result."""

    status: SignatureStatus
    fingerprint_version: int
    canonical_error: CanonicalError | None
    canonical_json: str | None
    fingerprint: str | None

    def __post_init__(self) -> None:
        if self.fingerprint_version < 1:
            raise ValueError("fingerprint_version must be positive")
        values = (self.canonical_error, self.canonical_json, self.fingerprint)
        if self.status == SignatureStatus.SIGNABLE and any(value is None for value in values):
            raise ValueError("SIGNABLE candidate requires canonical data and fingerprint")
        if self.status == SignatureStatus.UNSIGNABLE and any(value is not None for value in values):
            raise ValueError("UNSIGNABLE candidate must not contain canonical data")


@dataclass(frozen=True)
class SignaturePersistResult:
    """Outcome of idempotently storing one candidate."""

    status: SignatureStatus
    signature_id: UUID | None
    fingerprint: str | None
    created: bool


def signature_input_from_excerpt(
    operator_type: str | None, excerpt: RelevantLogExcerpt | None
) -> ErrorSignatureInput:
    """Select stable signature fields from sanitized evidence only."""
    if excerpt is None:
        return ErrorSignatureInput(operator_type, None, None, None, None)

    exception_class = excerpt.exception_classes[-1] if excerpt.exception_classes else None
    vendor_error_code = excerpt.vendor_error_codes[-1] if excerpt.vendor_error_codes else None
    application_frame = (
        excerpt.application_stack_frames[-1] if excerpt.application_stack_frames else None
    )
    normalized_message = _select_normalized_message(
        excerpt,
        exception_class=exception_class,
        vendor_error_code=vendor_error_code,
    )
    return ErrorSignatureInput(
        operator_type=operator_type,
        exception_class=exception_class,
        vendor_error_code=vendor_error_code,
        normalized_message=normalized_message,
        application_stack_frame=application_frame,
    )


def build_error_signature(signature_input: ErrorSignatureInput) -> ErrorSignatureCandidate:
    """Build a versioned SHA-256 fingerprint from normalized Canonical JSON."""
    canonical_error = CanonicalError(
        operator_type=_normalize_text(signature_input.operator_type),
        exception_class=_normalize_text(signature_input.exception_class),
        vendor_error_code=_normalize_vendor_code(signature_input.vendor_error_code),
        normalized_message=_normalize_text(signature_input.normalized_message),
        application_stack_frame=_normalize_text(signature_input.application_stack_frame),
    )
    if not any(
        (
            canonical_error.exception_class,
            canonical_error.vendor_error_code,
            canonical_error.normalized_message,
        )
    ):
        return ErrorSignatureCandidate(
            status=SignatureStatus.UNSIGNABLE,
            fingerprint_version=FINGERPRINT_VERSION,
            canonical_error=None,
            canonical_json=None,
            fingerprint=None,
        )

    canonical_json = json.dumps(
        asdict(canonical_error),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    fingerprint = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    return ErrorSignatureCandidate(
        status=SignatureStatus.SIGNABLE,
        fingerprint_version=FINGERPRINT_VERSION,
        canonical_error=canonical_error,
        canonical_json=canonical_json,
        fingerprint=fingerprint,
    )


def persist_error_signature(
    session: Session, candidate: ErrorSignatureCandidate
) -> SignaturePersistResult:
    """Idempotently store one signable candidate using its database uniqueness contract."""
    if candidate.status == SignatureStatus.UNSIGNABLE:
        return SignaturePersistResult(SignatureStatus.UNSIGNABLE, None, None, created=False)

    canonical_error = candidate.canonical_error
    fingerprint = candidate.fingerprint
    assert canonical_error is not None
    assert fingerprint is not None
    signature_id = uuid4()
    values = {
        "id": signature_id,
        "fingerprint_version": candidate.fingerprint_version,
        "fingerprint": fingerprint,
        **asdict(canonical_error),
    }

    with session.begin():
        dialect_name = session.get_bind().dialect.name
        if dialect_name == "postgresql":
            statement = (
                postgresql_insert(ErrorSignatureRecord)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["fingerprint_version", "fingerprint"])
                .returning(ErrorSignatureRecord.id)
            )
        elif dialect_name == "sqlite":
            statement = (
                sqlite_insert(ErrorSignatureRecord)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["fingerprint_version", "fingerprint"])
                .returning(ErrorSignatureRecord.id)
            )
        else:  # pragma: no cover - only configured dialects are supported
            raise RuntimeError(f"Unsupported database dialect: {dialect_name}")

        inserted_id = session.scalar(statement)
        if inserted_id is not None:
            return SignaturePersistResult(
                SignatureStatus.SIGNABLE, inserted_id, fingerprint, created=True
            )
        existing_id = session.scalar(
            select(ErrorSignatureRecord.id).where(
                ErrorSignatureRecord.fingerprint_version == candidate.fingerprint_version,
                ErrorSignatureRecord.fingerprint == fingerprint,
            )
        )
        if existing_id is None:  # pragma: no cover - database invariant
            raise RuntimeError("Conflicting Error Signature was not found")
        return SignaturePersistResult(
            SignatureStatus.SIGNABLE, existing_id, fingerprint, created=False
        )


def _select_normalized_message(
    excerpt: RelevantLogExcerpt,
    *,
    exception_class: str | None,
    vendor_error_code: str | None,
) -> str | None:
    for line in reversed(excerpt.lines):
        if exception_class is not None and exception_class in line.text:
            return line.text
        if vendor_error_code is not None and vendor_error_code in line.text.upper():
            return line.text
    return next(
        (line.text for line in reversed(excerpt.lines) if _SIGNATURE_MESSAGE.search(line.text)),
        None,
    )


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _normalize_vendor_code(value: str | None) -> str | None:
    normalized = _normalize_text(value)
    return normalized.upper() if normalized is not None else None
