from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete

from dagsentry.db import create_session_factory
from dagsentry.diagnosis import DiagnosisDraft, persist_diagnosis, reuse_diagnosis
from dagsentry.domain.diagnosis import (
    DiagnosisContent,
    DiagnosisEvidence,
    DiagnosisReusePolicy,
    DiagnosisSource,
    DiagnosisValidationStatus,
    DiagnosisVersions,
    ErrorClassification,
    RetryDecision,
)
from dagsentry.domain.failure_event import CollectionSource, FailureState
from dagsentry.error_signature import (
    FINGERPRINT_VERSION,
    ErrorSignatureInput,
    build_error_signature,
    persist_error_signature,
)
from dagsentry.models import DiagnosisRecord, ErrorSignatureRecord, FailureEventRecord

pytestmark = pytest.mark.integration


def test_postgres_reuses_compatible_diagnosis_by_reference() -> None:
    database_url = os.environ.get("DAGSENTRY_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("DAGSENTRY_TEST_DATABASE_URL is not configured")

    session_factory = create_session_factory(database_url)
    marker = uuid4().hex
    now = datetime.now(UTC)
    failure_ids: list[UUID] = []
    diagnosis_ids: list[UUID] = []
    signature_id: UUID | None = None

    try:
        with session_factory() as session:
            for try_number in (1, 2):
                failure = FailureEventRecord(
                    event_key=hashlib.sha256(f"{marker}:{try_number}".encode()).hexdigest(),
                    event_key_version=1,
                    environment="integration",
                    dag_id="diagnosis_test",
                    dag_run_id=marker,
                    task_id="load",
                    map_index=-1,
                    try_number=try_number,
                    source=CollectionSource.LISTENER,
                    state=FailureState.FAILED,
                    observed_at=now,
                )
                session.add(failure)
                session.commit()
                failure_ids.append(failure.id)

            signature = persist_error_signature(
                session,
                build_error_signature(
                    ErrorSignatureInput(
                        operator_type="PythonOperator",
                        exception_class="ValueError",
                        vendor_error_code=None,
                        normalized_message=f"ValueError: diagnosis integration {marker}",
                        application_stack_frame=None,
                    )
                ),
            )
            assert signature.signature_id is not None
            signature_id = signature.signature_id
            original_id = persist_diagnosis(
                session,
                DiagnosisDraft(
                    failure_event_id=failure_ids[0],
                    error_signature_id=signature_id,
                    source=DiagnosisSource.AI,
                    validation_status=DiagnosisValidationStatus.PASSED,
                    versions=DiagnosisVersions(1, "ai-v1", 1),
                    content=DiagnosisContent(
                        classification=ErrorClassification.DAG_CODE,
                        root_cause="Invalid integration input.",
                        confidence=0.9,
                        evidence=(DiagnosisEvidence(1, "ValueError"),),
                        recommended_actions=("Validate the input.",),
                        retry_decision=RetryDecision.NOT_RETRYABLE,
                    ),
                ),
            )
            diagnosis_ids.append(original_id)

            reused = reuse_diagnosis(
                session,
                failure_event_id=failure_ids[1],
                error_signature_id=signature_id,
                policy=DiagnosisReusePolicy(
                    fingerprint_version=FINGERPRINT_VERSION,
                    versions=DiagnosisVersions(1, "ai-v1", 1),
                    max_age=timedelta(days=30),
                ),
                now=now,
            )
            assert reused is not None
            diagnosis_ids.append(reused.diagnosis_id)

            reused_record = session.get(DiagnosisRecord, reused.diagnosis_id)
            assert reused_record is not None
            assert reused_record.reused_from_diagnosis_id == original_id
            assert reused_record.evidence is None
    finally:
        with session_factory.begin() as session:
            if diagnosis_ids:
                session.execute(
                    delete(DiagnosisRecord).where(DiagnosisRecord.id.in_(diagnosis_ids))
                )
            if failure_ids:
                session.execute(
                    delete(FailureEventRecord).where(FailureEventRecord.id.in_(failure_ids))
                )
            if signature_id is not None:
                session.execute(
                    delete(ErrorSignatureRecord).where(ErrorSignatureRecord.id == signature_id)
                )
