from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from dagsentry.api import create_app
from dagsentry.config import Settings
from dagsentry.db import SessionFactory
from dagsentry.diagnosis import persist_diagnosis
from dagsentry.domain.diagnosis import DiagnosisSource, DiagnosisValidationStatus
from dagsentry.models import IncidentRecord
from tests.test_incident_api import (
    NOW,
    TOKEN_HEADER,
    VIEWER_HEADER,
    create_incident,
    diagnosis_draft,
    request,
)


def test_signature_list_is_filterable_paginated_and_stably_sorted(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    with session_factory() as session:
        repeated_incident_id, _ = create_incident(
            session,
            dag_id="repeated",
            failure_count=3,
            observed_at=NOW - timedelta(days=2),
        )
        recent_incident_id, _ = create_incident(
            session,
            dag_id="recent",
            observed_at=NOW - timedelta(days=1),
        )
        repeated = session.get(IncidentRecord, repeated_incident_id)
        recent = session.get(IncidentRecord, recent_incident_id)
        assert repeated is not None and repeated.error_signature_id is not None
        assert recent is not None and recent.error_signature_id is not None
        repeated_signature_id = repeated.error_signature_id
        recent_signature_id = recent.error_signature_id

    app = create_app(settings, session_factory)
    response = request(
        app,
        "GET",
        "/api/v1/error-signatures?environment=production&sort=failure_count&order=desc",
        headers=TOKEN_HEADER,
    )
    latest = request(
        app,
        "GET",
        "/api/v1/error-signatures?sort=last_seen_at&order=desc&limit=1",
        headers=TOKEN_HEADER,
    )

    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert response.json()["items"][0]["id"] == str(repeated_signature_id)
    assert response.json()["items"][0]["failure_count"] == 3
    assert response.json()["items"][0]["incident_count"] == 1
    assert latest.json()["items"][0]["id"] == str(recent_signature_id)


def test_signature_detail_occurrences_and_daily_trend(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    with session_factory() as session:
        incident_id, first_failure_ids = create_incident(
            session,
            failure_count=2,
            observed_at=NOW - timedelta(days=1),
        )
        same_incident_id, second_failure_ids = create_incident(
            session,
            dag_run_id="scheduled__2026-08-12",
            observed_at=NOW,
        )
        assert same_incident_id == incident_id
        incident = session.get(IncidentRecord, incident_id)
        assert incident is not None and incident.error_signature_id is not None
        signature_id = incident.error_signature_id
        session.rollback()
        diagnosis_id = persist_diagnosis(
            session,
            diagnosis_draft(
                failure_event_id=second_failure_ids[0],
                error_signature_id=signature_id,
                source=DiagnosisSource.RULE,
                validation_status=DiagnosisValidationStatus.PASSED,
                root_cause="Upstream connection timed out",
            ),
        )

    app = create_app(settings, session_factory)
    detail = request(
        app,
        "GET",
        f"/api/v1/error-signatures/{signature_id}",
        headers=TOKEN_HEADER,
    )
    occurrences = request(
        app,
        "GET",
        f"/api/v1/error-signatures/{signature_id}/occurrences?limit=2",
        headers=TOKEN_HEADER,
    )
    trend = request(
        app,
        "GET",
        (f"/api/v1/error-signatures/{signature_id}/trend?date_from=2026-08-10&date_to=2026-08-12"),
        headers=TOKEN_HEADER,
    )
    filtered = request(
        app,
        "GET",
        (
            "/api/v1/error-signatures?dag_id=orders&classification=NETWORK"
            "&q=ValueError&date_from=2026-08-10&date_to=2026-08-11"
        ),
        headers=TOKEN_HEADER,
    )

    assert detail.status_code == 200
    assert detail.json()["failure_count"] == 3
    assert detail.json()["incident_count"] == 1
    assert detail.json()["latest_validated_diagnosis"]["id"] == str(diagnosis_id)
    assert occurrences.status_code == 200
    assert occurrences.json()["total"] == 3
    assert len(occurrences.json()["items"]) == 2
    assert occurrences.json()["items"][0]["failure_event_id"] == str(second_failure_ids[0])
    assert occurrences.json()["items"][0]["incident_id"] == str(incident_id)
    assert {item["failure_event_id"] for item in occurrences.json()["items"]}.isdisjoint(
        {str(failure_id) for failure_id in first_failure_ids[:-1]}
    )
    assert trend.status_code == 200
    assert trend.json()["items"] == [
        {"date": "2026-08-10", "failure_count": 2},
        {"date": "2026-08-11", "failure_count": 1},
        {"date": "2026-08-12", "failure_count": 0},
    ]
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["id"] == str(signature_id)


def test_signature_api_validates_identity_sort_and_trend_range(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)

    unauthorized = request(app, "GET", "/api/v1/error-signatures")
    viewer = request(app, "GET", "/api/v1/error-signatures", headers=VIEWER_HEADER)
    missing = request(
        app,
        "GET",
        f"/api/v1/error-signatures/{uuid4()}",
        headers=TOKEN_HEADER,
    )
    invalid_sort = request(
        app,
        "GET",
        "/api/v1/error-signatures?sort=unknown",
        headers=TOKEN_HEADER,
    )
    invalid_range = request(
        app,
        "GET",
        (f"/api/v1/error-signatures/{uuid4()}/trend?date_from=2025-01-01&date_to=2026-08-12"),
        headers=TOKEN_HEADER,
    )

    assert unauthorized.status_code == 401
    assert viewer.status_code == 200
    assert missing.status_code == 404
    assert invalid_sort.status_code == 422
    assert invalid_range.status_code == 422
