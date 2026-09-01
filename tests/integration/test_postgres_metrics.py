from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import delete

from dagsentry.db import create_session_factory
from dagsentry.metrics import increment_counter
from dagsentry.models import OperationalMetricCounterRecord

pytestmark = pytest.mark.integration


def test_concurrent_metric_increments_are_not_lost() -> None:
    database_url = os.environ.get("DAGSENTRY_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("DAGSENTRY_TEST_DATABASE_URL is not configured")

    session_factory = create_session_factory(database_url)
    identity = ("dagsentry_recovery_checker_runs_total", "degraded")
    with session_factory.begin() as session:
        session.execute(
            delete(OperationalMetricCounterRecord).where(
                OperationalMetricCounterRecord.metric_name == identity[0],
                OperationalMetricCounterRecord.label_value == identity[1],
            )
        )

    def increment_many(_: int) -> None:
        for _ in range(25):
            with session_factory.begin() as session:
                increment_counter(session, *identity)

    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(increment_many, range(8)))

        with session_factory() as session:
            record = session.get(OperationalMetricCounterRecord, identity)
            assert record is not None
            assert record.value == 200
    finally:
        with session_factory.begin() as session:
            session.execute(
                delete(OperationalMetricCounterRecord).where(
                    OperationalMetricCounterRecord.metric_name == identity[0],
                    OperationalMetricCounterRecord.label_value == identity[1],
                )
            )
