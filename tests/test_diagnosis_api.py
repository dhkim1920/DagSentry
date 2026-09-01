from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

from dagsentry.api import create_app
from dagsentry.config import Settings
from dagsentry.db import SessionFactory
from dagsentry.diagnosis import persist_diagnosis, reuse_diagnosis
from dagsentry.domain.diagnosis import (
    DiagnosisReusePolicy,
    DiagnosisSource,
    DiagnosisValidationStatus,
    DiagnosisVersions,
)
from dagsentry.models import IncidentRecord
from tests.test_incident_api import (
    TOKEN_HEADER,
    VIEWER_HEADER,
    create_incident,
    diagnosis_draft,
    request,
    store_delivery,
)


def create_diagnosis_history(
    session_factory: SessionFactory,
) -> tuple[UUID, UUID, UUID, UUID, UUID, UUID]:
    with session_factory() as session:
        incident_id, failure_ids = create_incident(session, failure_count=2)
        incident = session.get(IncidentRecord, incident_id)
        assert incident is not None and incident.error_signature_id is not None
        signature_id = incident.error_signature_id
        session.rollback()
        rejected_id = persist_diagnosis(
            session,
            diagnosis_draft(
                failure_event_id=failure_ids[0],
                error_signature_id=signature_id,
                source=DiagnosisSource.AI,
                validation_status=DiagnosisValidationStatus.REJECTED,
                root_cause="Unverified AI result",
            ),
        )
        effective_id = persist_diagnosis(
            session,
            diagnosis_draft(
                failure_event_id=failure_ids[0],
                error_signature_id=signature_id,
                source=DiagnosisSource.RULE,
                validation_status=DiagnosisValidationStatus.PASSED,
                root_cause="Upstream connection timed out",
            ),
        )
        reused = reuse_diagnosis(
            session,
            failure_event_id=failure_ids[1],
            error_signature_id=signature_id,
            policy=DiagnosisReusePolicy(
                fingerprint_version=1,
                versions=DiagnosisVersions(1, None, 1),
                max_age=timedelta(days=30),
            ),
        )
        assert reused is not None
        store_delivery(
            session,
            diagnosis_id=effective_id,
            airflow_log_url="https://airflow.example/first",
        )
        store_delivery(
            session,
            diagnosis_id=reused.diagnosis_id,
            airflow_log_url="https://airflow.example/second",
        )
    return (
        incident_id,
        failure_ids[0],
        signature_id,
        rejected_id,
        effective_id,
        reused.diagnosis_id,
    )


def test_diagnosis_list_filters_and_resolves_effective_provenance(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    _, _, _, rejected_id, effective_id, reused_id = create_diagnosis_history(session_factory)
    app = create_app(settings, session_factory)

    listing = request(
        app,
        "GET",
        "/api/v1/diagnoses?sort=created_at&order=asc&limit=10",
        headers=TOKEN_HEADER,
    )
    rejected_only = request(
        app,
        "GET",
        "/api/v1/diagnoses?source=AI&validation_status=REJECTED&classification=NETWORK",
        headers=TOKEN_HEADER,
    )
    history = request(app, "GET", "/api/v1/diagnoses/history?limit=10", headers=TOKEN_HEADER)

    assert listing.status_code == 200
    assert listing.json()["total"] == 3
    items = {item["id"]: item for item in listing.json()["items"]}
    assert items[str(rejected_id)]["effective"] is False
    assert items[str(rejected_id)]["effective_diagnosis_id"] == str(effective_id)
    assert items[str(effective_id)]["effective"] is True
    assert items[str(reused_id)]["effective"] is True
    assert items[str(reused_id)]["source"] == "REUSED"
    assert items[str(reused_id)]["content_diagnosis_id"] == str(effective_id)
    assert items[str(reused_id)]["classification"] == "NETWORK"
    assert items[str(reused_id)]["root_cause"] == "Upstream connection timed out"
    assert rejected_only.status_code == 200
    assert rejected_only.json()["total"] == 1
    assert rejected_only.json()["items"][0]["id"] == str(rejected_id)
    assert history.status_code == 200
    history_items = {item["id"]: item for item in history.json()["items"]}
    assert history_items[str(reused_id)]["source_type"] == "RULE"
    assert history_items[str(reused_id)]["reused_from_diagnosis_id"] == str(effective_id)


def test_diagnosis_detail_exposes_evidence_context_and_reuse_origin(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    incident_id, failure_id, signature_id, rejected_id, effective_id, reused_id = (
        create_diagnosis_history(session_factory)
    )
    app = create_app(settings, session_factory)

    rejected = request(
        app,
        "GET",
        f"/api/v1/diagnoses/{rejected_id}",
        headers=TOKEN_HEADER,
    )
    reused = request(
        app,
        "GET",
        f"/api/v1/diagnoses/{reused_id}",
        headers=TOKEN_HEADER,
    )

    assert rejected.status_code == 200
    assert rejected.json()["failure"]["id"] == str(failure_id)
    assert rejected.json()["incident_id"] == str(incident_id)
    assert rejected.json()["error_signature"]["id"] == str(signature_id)
    assert rejected.json()["effective"] is False
    assert rejected.json()["effective_diagnosis_id"] == str(effective_id)
    assert rejected.json()["validation_errors"] == ["EVIDENCE_TEXT_MISMATCH:7"]
    assert rejected.json()["evidence"] == [{"line_id": 7, "text": "Connection timed out"}]
    assert rejected.json()["airflow_log_url"] == "https://airflow.example/first"

    assert reused.status_code == 200
    assert reused.json()["effective"] is True
    assert reused.json()["content_diagnosis_id"] == str(effective_id)
    assert reused.json()["reused_from_diagnosis_id"] == str(effective_id)
    assert reused.json()["root_cause"] == "Upstream connection timed out"
    assert reused.json()["evidence"] == [{"line_id": 7, "text": "Connection timed out"}]
    assert reused.json()["airflow_log_url"] == "https://airflow.example/second"


def test_diagnosis_api_validates_auth_identity_sort_and_dates(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)

    unauthorized = request(app, "GET", "/api/v1/diagnoses")
    viewer = request(app, "GET", "/api/v1/diagnoses", headers=VIEWER_HEADER)
    missing = request(
        app,
        "GET",
        f"/api/v1/diagnoses/{uuid4()}",
        headers=TOKEN_HEADER,
    )
    invalid_sort = request(
        app,
        "GET",
        "/api/v1/diagnoses?sort=unknown",
        headers=TOKEN_HEADER,
    )
    invalid_dates = request(
        app,
        "GET",
        "/api/v1/diagnoses?date_from=2026-08-12&date_to=2026-08-11",
        headers=TOKEN_HEADER,
    )

    assert unauthorized.status_code == 401
    assert viewer.status_code == 200
    assert missing.status_code == 404
    assert invalid_sort.status_code == 422
    assert invalid_dates.status_code == 422
