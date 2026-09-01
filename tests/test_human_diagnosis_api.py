from __future__ import annotations

from uuid import UUID

from dagsentry.api import create_app
from dagsentry.config import Settings
from dagsentry.db import SessionFactory
from dagsentry.diagnosis import persist_diagnosis
from dagsentry.domain.diagnosis import DiagnosisSource, DiagnosisValidationStatus
from dagsentry.models import IncidentHumanDiagnosisRecord, IncidentRecord
from tests.test_incident_api import (
    TOKEN_HEADER,
    VIEWER_HEADER,
    create_incident,
    diagnosis_draft,
    request,
)


def _incident_with_diagnosis(session_factory: SessionFactory) -> tuple[UUID, UUID]:
    with session_factory() as session:
        incident_id, failure_ids = create_incident(session)
        incident = session.get(IncidentRecord, incident_id)
        assert incident is not None
        error_signature_id = incident.error_signature_id
        assert error_signature_id is not None
        session.commit()
        diagnosis_id = persist_diagnosis(
            session,
            diagnosis_draft(
                failure_event_id=failure_ids[0],
                error_signature_id=error_signature_id,
                source=DiagnosisSource.RULE,
                validation_status=DiagnosisValidationStatus.PASSED,
                root_cause="Automatic timeout diagnosis",
            ),
        )
    return incident_id, diagnosis_id


def _body(diagnosis_id: UUID, **changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "expected_revision": 0,
        "basis_diagnosis_id": str(diagnosis_id),
        "classification": "NETWORK",
        "root_cause": "Upstream service was unavailable",
        "recommended_actions": ["Check the upstream service"],
        "retry_decision": "RETRYABLE",
        "evidence": [{"source_diagnosis_id": str(diagnosis_id), "line_id": 7}],
        "operator_notes": "Confirmed by on-call",
    }
    body.update(changes)
    return body


def test_operator_publishes_revision_and_viewer_reads_history(
    settings: Settings, session_factory: SessionFactory
) -> None:
    incident_id, diagnosis_id = _incident_with_diagnosis(session_factory)
    app = create_app(settings, session_factory)

    created = request(
        app,
        "POST",
        f"/api/v1/incidents/{incident_id}/human-diagnoses",
        headers=TOKEN_HEADER,
        json=_body(diagnosis_id),
    )
    history = request(
        app,
        "GET",
        f"/api/v1/incidents/{incident_id}/human-diagnoses",
        headers=VIEWER_HEADER,
    )
    revision = request(
        app,
        "GET",
        f"/api/v1/incidents/{incident_id}/human-diagnoses/{created.json()['id']}",
        headers=VIEWER_HEADER,
    )
    detail = request(app, "GET", f"/api/v1/incidents/{incident_id}", headers=VIEWER_HEADER)

    assert created.status_code == 201
    assert created.json()["revision"] == 1
    assert created.json()["actor_identity"] == "oncall@example.com"
    assert created.json()["evidence"] == [
        {
            "source_diagnosis_id": str(diagnosis_id),
            "failure_event_id": history.json()["items"][0]["evidence"][0]["failure_event_id"],
            "line_id": 7,
            "text": "Connection timed out",
            "position": 0,
        }
    ]
    assert history.status_code == 200
    assert history.json()["current_human_diagnosis_id"] == created.json()["id"]
    assert revision.status_code == 200
    assert revision.json()["id"] == created.json()["id"]
    assert (
        detail.json()["current_human_diagnosis"]["root_cause"] == "Upstream service was unavailable"
    )


def test_human_diagnosis_revisions_conflict_and_withdraw(
    settings: Settings, session_factory: SessionFactory
) -> None:
    incident_id, diagnosis_id = _incident_with_diagnosis(session_factory)
    app = create_app(settings, session_factory)
    first = request(
        app,
        "POST",
        f"/api/v1/incidents/{incident_id}/human-diagnoses",
        headers=TOKEN_HEADER,
        json=_body(diagnosis_id),
    )
    stale = request(
        app,
        "POST",
        f"/api/v1/incidents/{incident_id}/human-diagnoses",
        headers=TOKEN_HEADER,
        json=_body(diagnosis_id),
    )
    correction = request(
        app,
        "POST",
        f"/api/v1/incidents/{incident_id}/human-diagnoses",
        headers=TOKEN_HEADER,
        json=_body(
            diagnosis_id,
            expected_revision=1,
            root_cause="Corrected upstream diagnosis",
            change_reason="DBA confirmation",
        ),
    )
    withdrawn = request(
        app,
        "POST",
        f"/api/v1/incidents/{incident_id}/human-diagnoses/withdraw",
        headers=TOKEN_HEADER,
        json={"expected_revision": 2, "reason": "Incident was incorrectly grouped"},
    )

    assert first.status_code == 201
    assert stale.status_code == 409
    assert correction.status_code == 201
    assert correction.json()["revision"] == 2
    assert withdrawn.status_code == 201
    assert withdrawn.json()["action"] == "WITHDRAW"
    history = request(
        app, "GET", "/api/v1/diagnoses/history?source_type=OPERATOR", headers=VIEWER_HEADER
    )
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["revision"] == 3
    assert history.json()["items"][0]["status"] == "WITHDRAWN"
    detail = request(app, "GET", f"/api/v1/incidents/{incident_id}", headers=VIEWER_HEADER)
    assert detail.json()["current_human_diagnosis"] is None
    with session_factory() as session:
        assert session.query(IncidentHumanDiagnosisRecord).count() == 3


def test_human_diagnosis_rejects_viewer_secret_and_unknown_evidence(
    settings: Settings, session_factory: SessionFactory
) -> None:
    incident_id, diagnosis_id = _incident_with_diagnosis(session_factory)
    app = create_app(settings, session_factory)
    viewer = request(
        app,
        "POST",
        f"/api/v1/incidents/{incident_id}/human-diagnoses",
        headers=VIEWER_HEADER,
        json=_body(diagnosis_id),
    )
    secret = request(
        app,
        "POST",
        f"/api/v1/incidents/{incident_id}/human-diagnoses",
        headers=TOKEN_HEADER,
        json=_body(diagnosis_id, root_cause="password=do-not-store"),
    )
    evidence = request(
        app,
        "POST",
        f"/api/v1/incidents/{incident_id}/human-diagnoses",
        headers=TOKEN_HEADER,
        json=_body(
            diagnosis_id, evidence=[{"source_diagnosis_id": str(diagnosis_id), "line_id": 99}]
        ),
    )

    assert viewer.status_code == 403
    assert secret.status_code == 422
    assert evidence.status_code == 422


def test_human_diagnosis_is_searchable_in_history_and_signature_context(
    settings: Settings, session_factory: SessionFactory
) -> None:
    incident_id, diagnosis_id = _incident_with_diagnosis(session_factory)
    with session_factory() as session:
        incident = session.get(IncidentRecord, incident_id)
        assert incident is not None and incident.error_signature_id is not None
        signature_id = incident.error_signature_id
    app = create_app(settings, session_factory)
    created = request(
        app,
        "POST",
        f"/api/v1/incidents/{incident_id}/human-diagnoses",
        headers=TOKEN_HEADER,
        json=_body(diagnosis_id),
    )
    history = request(
        app, "GET", "/api/v1/diagnoses/history?source_type=OPERATOR", headers=VIEWER_HEADER
    )
    signature = request(
        app,
        "GET",
        f"/api/v1/error-signatures/{signature_id}",
        headers=VIEWER_HEADER,
    )

    assert created.status_code == 201
    assert history.status_code == 200
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["source_type"] == "OPERATOR"
    assert history.json()["items"][0]["status"] == "CONFIRMED"
    assert history.json()["items"][0]["incident_id"] == str(incident_id)
    assert signature.status_code == 200
    assert signature.json()["operator_diagnoses"] == [
        {
            "id": created.json()["id"],
            "incident_id": str(incident_id),
            "revision": 1,
            "classification": "NETWORK",
            "retry_decision": "RETRYABLE",
            "root_cause": "Upstream service was unavailable",
            "recommended_actions": ["Check the upstream service"],
            "actor_identity": "oncall@example.com",
            "created_at": created.json()["created_at"],
        }
    ]
