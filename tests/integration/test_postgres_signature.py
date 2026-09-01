import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import delete, func, select

from dagsentry.db import create_session_factory
from dagsentry.error_signature import (
    ErrorSignatureCandidate,
    ErrorSignatureInput,
    SignaturePersistResult,
    build_error_signature,
    persist_error_signature,
)
from dagsentry.models import ErrorSignatureRecord

pytestmark = pytest.mark.integration


def test_concurrent_signature_creation_persists_one_record() -> None:
    database_url = os.environ.get("DAGSENTRY_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("DAGSENTRY_TEST_DATABASE_URL is not configured")

    session_factory = create_session_factory(database_url)
    signature = build_error_signature(
        ErrorSignatureInput(
            operator_type="PythonOperator",
            exception_class="ValueError",
            vendor_error_code=None,
            normalized_message="ValueError: concurrent signature test",
            application_stack_frame='File "/opt/airflow/dags/test.py", line <LINE>, in task',
        )
    )
    barrier = Barrier(8)

    def persist(candidate: ErrorSignatureCandidate) -> SignaturePersistResult:
        with session_factory() as session:
            barrier.wait()
            return persist_error_signature(session, candidate)

    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(persist, [signature] * 8))

        assert sum(result.created for result in results) == 1
        assert len({result.signature_id for result in results}) == 1

        with session_factory() as session:
            count = session.scalar(
                select(func.count())
                .select_from(ErrorSignatureRecord)
                .where(ErrorSignatureRecord.fingerprint == signature.fingerprint)
            )
            assert count == 1
    finally:
        with session_factory.begin() as session:
            session.execute(
                delete(ErrorSignatureRecord).where(
                    ErrorSignatureRecord.fingerprint == signature.fingerprint
                )
            )
