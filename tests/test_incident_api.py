from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dagsentry.api import create_app
from dagsentry.config import Settings
from dagsentry.db import SessionFactory
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
from dagsentry.domain.failure_event import CollectionSource, FailureEventCreate, FailureState
from dagsentry.domain.incident import IncidentStatus, IncidentTransitionInitiator
from dagsentry.domain.notification import NotificationDeliveryStatus
from dagsentry.error_signature import (
    ErrorSignatureInput,
    build_error_signature,
    persist_error_signature,
)
from dagsentry.incident import correlate_failure, transition_incident
from dagsentry.ingestion import ingest_failure_event
from dagsentry.models import (
    IncidentRecord,
    IncidentStateTransitionRecord,
    NotificationDeliveryRecord,
)

NOW = datetime(2026, 8, 11, 12, tzinfo=UTC)
TOKEN_HEADER = {"X-DagSentry-Operator-Token": "test-operator-token"}
VIEWER_HEADER = {"X-DagSentry-Viewer-Token": "test-viewer-token"}


def create_incident(
    session: Session,
    *,
    environment: str = "production",
    dag_id: str = "orders",
    task_id: str = "load",
    dag_run_id: str = "scheduled__2026-08-11",
    failure_count: int = 1,
    observed_at: datetime = NOW,
) -> tuple[UUID, list[UUID]]:
    signature = persist_error_signature(
        session,
        build_error_signature(
            ErrorSignatureInput(
                operator_type="PythonOperator",
                exception_class="ValueError",
                vendor_error_code=None,
                normalized_message=f"ValueError: {environment}:{dag_id}:{task_id}",
                application_stack_frame=None,
            )
        ),
    )
    assert signature.signature_id is not None
    failure_ids: list[UUID] = []
    incident_id: UUID | None = None
    for try_number in range(1, failure_count + 1):
        failure = ingest_failure_event(
            session,
            FailureEventCreate(
                environment=environment,
                dag_id=dag_id,
                dag_run_id=dag_run_id,
                task_id=task_id,
                map_index=-1,
                try_number=try_number,
                source=CollectionSource.LISTENER,
                state=FailureState.FAILED,
                observed_at=observed_at + timedelta(minutes=try_number - 1),
            ),
        )
        failure_ids.append(failure.failure_event_id)
        correlated = correlate_failure(
            session,
            failure_event_id=failure.failure_event_id,
            error_signature_id=signature.signature_id,
        )
        incident_id = correlated.incident_id
    assert incident_id is not None
    return incident_id, failure_ids


def request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    json: dict[str, object] | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, headers=headers, json=json)

    return asyncio.run(send())


def diagnosis_draft(
    *,
    failure_event_id: UUID,
    error_signature_id: UUID,
    source: DiagnosisSource,
    validation_status: DiagnosisValidationStatus,
    root_cause: str,
) -> DiagnosisDraft:
    return DiagnosisDraft(
        failure_event_id=failure_event_id,
        error_signature_id=error_signature_id,
        source=source,
        validation_status=validation_status,
        versions=DiagnosisVersions(
            schema_version=1,
            prompt_version="incident-detail-v1" if source == DiagnosisSource.AI else None,
            rule_version=1,
        ),
        content=DiagnosisContent(
            classification=ErrorClassification.NETWORK,
            root_cause=root_cause,
            confidence=0.8,
            confidence_reason="Connection timeout matched",
            matched_rule="connection-timeout",
            evidence=(DiagnosisEvidence(line_id=7, text="Connection timed out"),),
            recommended_actions=("Check the upstream service",),
            retry_decision=RetryDecision.RETRYABLE,
        ),
        operator_review_required=True,
        validation_errors=(
            ("EVIDENCE_TEXT_MISMATCH:7",)
            if validation_status == DiagnosisValidationStatus.REJECTED
            else ()
        ),
    )


def store_delivery(
    session: Session,
    *,
    diagnosis_id: UUID,
    airflow_log_url: str,
) -> None:
    with session.begin():
        session.add(
            NotificationDeliveryRecord(
                diagnosis_id=diagnosis_id,
                delivery_key=diagnosis_id.hex,
                delivery_key_version=1,
                provider="slack",
                status=NotificationDeliveryStatus.DELIVERED,
                attempt_count=1,
                payload={"airflow_log_url": airflow_log_url},
                delivered_at=NOW,
            )
        )


def test_incident_api_requires_separate_operator_token(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)

    missing = request(app, "GET", "/api/v1/incidents")
    invalid = request(
        app,
        "GET",
        "/api/v1/incidents",
        headers={"X-DagSentry-Operator-Token": "wrong"},
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401


def test_incident_api_is_unavailable_without_operator_token(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    settings.operator_api_token = None
    settings.viewer_api_token = None

    response = request(create_app(settings, session_factory), "GET", "/api/v1/incidents")

    assert response.status_code == 503
    assert response.json() == {"detail": "Query APIs are not configured"}


def test_viewer_can_read_incidents_but_cannot_change_state(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    with session_factory() as session:
        incident_id, _ = create_incident(session)
    app = create_app(settings, session_factory)

    listing = request(app, "GET", "/api/v1/incidents", headers=VIEWER_HEADER)
    forbidden = request(
        app,
        "PATCH",
        f"/api/v1/incidents/{incident_id}/status",
        headers=VIEWER_HEADER,
        json={"status": "ACKNOWLEDGED", "expected_status": "OPEN"},
    )

    assert listing.status_code == 200
    assert forbidden.status_code == 403
    assert forbidden.json() == {"detail": "Operator role required"}


def test_operator_identity_must_be_configured_server_side(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    settings.operator_api_identity = None

    response = request(
        create_app(settings, session_factory),
        "GET",
        "/api/v1/incidents",
        headers=TOKEN_HEADER,
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Operator identity is not configured"}


def test_list_filters_and_detail_include_failures(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    with session_factory() as session:
        production_id, failure_ids = create_incident(session, failure_count=2)
        create_incident(session, environment="staging")
    app = create_app(settings, session_factory)

    listing = request(
        app,
        "GET",
        "/api/v1/incidents?environment=production&status=OPEN&limit=1&offset=0",
        headers=TOKEN_HEADER,
    )
    detail = request(
        app,
        "GET",
        f"/api/v1/incidents/{production_id}",
        headers=TOKEN_HEADER,
    )

    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == str(production_id)
    assert listing.json()["items"][0]["failure_count"] == 2
    assert listing.json()["items"][0]["first_failure_at"] == NOW.isoformat().replace("+00:00", "Z")
    assert listing.json()["items"][0]["last_failure_at"] == (
        NOW + timedelta(minutes=1)
    ).isoformat().replace("+00:00", "Z")
    assert detail.status_code == 200
    assert detail.json()["incident"]["id"] == str(production_id)
    assert (
        detail.json()["incident"]["first_failure_at"]
        == listing.json()["items"][0]["first_failure_at"]
    )
    assert (
        detail.json()["incident"]["last_failure_at"]
        == listing.json()["items"][0]["last_failure_at"]
    )
    assert {item["id"] for item in detail.json()["failures"]} == {
        str(failure_id) for failure_id in failure_ids
    }
    assert all(item["diagnoses"] == [] for item in detail.json()["failures"])
    assert all(item["error_signature"] is not None for item in detail.json()["failures"])
    assert all(item["airflow_log_url"] is None for item in detail.json()["failures"])
    assert detail.json()["transitions"] == []


def test_detail_exposes_diagnosis_history_reuse_and_delivery_log_url(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    with session_factory() as session:
        incident_id, failure_ids = create_incident(session, failure_count=2)
        incident = session.get(IncidentRecord, incident_id)
        assert incident is not None
        assert incident.error_signature_id is not None
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
                versions=DiagnosisVersions(
                    schema_version=1,
                    prompt_version=None,
                    rule_version=1,
                ),
                max_age=timedelta(days=30),
            ),
        )
        assert reused is not None
        first_log_url = "https://airflow.example/dags/orders/first"
        second_log_url = "https://airflow.example/dags/orders/second"
        store_delivery(session, diagnosis_id=effective_id, airflow_log_url=first_log_url)
        store_delivery(session, diagnosis_id=reused.diagnosis_id, airflow_log_url=second_log_url)

    response = request(
        create_app(settings, session_factory),
        "GET",
        f"/api/v1/incidents/{incident_id}",
        headers=TOKEN_HEADER,
    )

    assert response.status_code == 200
    failures = response.json()["failures"]
    first_diagnoses = {item["id"]: item for item in failures[0]["diagnoses"]}
    assert first_diagnoses[str(rejected_id)]["effective"] is False
    assert first_diagnoses[str(rejected_id)]["validation_status"] == "REJECTED"
    assert first_diagnoses[str(rejected_id)]["validation_errors"] == ["EVIDENCE_TEXT_MISMATCH:7"]
    assert first_diagnoses[str(effective_id)]["effective"] is True
    assert first_diagnoses[str(effective_id)]["root_cause"] == "Upstream connection timed out"
    assert first_diagnoses[str(effective_id)]["evidence"] == [
        {"line_id": 7, "text": "Connection timed out"}
    ]
    assert failures[0]["airflow_log_url"] == first_log_url
    assert failures[0]["error_signature"]["id"] == str(signature_id)
    assert failures[0]["error_signature"]["normalized_message"].startswith("ValueError:")

    reused_diagnosis = failures[1]["diagnoses"][0]
    assert reused_diagnosis["effective"] is True
    assert reused_diagnosis["source"] == "REUSED"
    assert reused_diagnosis["content_diagnosis_id"] == str(effective_id)
    assert reused_diagnosis["reused_from_diagnosis_id"] == str(effective_id)
    assert reused_diagnosis["root_cause"] == "Upstream connection timed out"
    assert reused_diagnosis["evidence"] == [{"line_id": 7, "text": "Connection timed out"}]
    assert failures[1]["airflow_log_url"] == second_log_url


def test_list_supports_stable_server_side_sorting(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    with session_factory() as session:
        repeated_id, _ = create_incident(
            session,
            dag_id="repeated",
            failure_count=3,
            observed_at=NOW - timedelta(hours=2),
        )
        recent_id, _ = create_incident(
            session,
            dag_id="recent",
            observed_at=NOW - timedelta(hours=1),
        )
    app = create_app(settings, session_factory)

    latest = request(
        app,
        "GET",
        "/api/v1/incidents?sort=last_failure_at&order=desc&limit=1",
        headers=TOKEN_HEADER,
    )
    most_repeated = request(
        app,
        "GET",
        "/api/v1/incidents?sort=failure_count&order=desc&limit=1",
        headers=TOKEN_HEADER,
    )
    oldest = request(
        app,
        "GET",
        "/api/v1/incidents?sort=last_failure_at&order=asc&limit=1",
        headers=TOKEN_HEADER,
    )

    assert latest.status_code == 200
    assert latest.json()["items"][0]["id"] == str(recent_id)
    assert most_repeated.json()["items"][0]["id"] == str(repeated_id)
    assert oldest.json()["items"][0]["id"] == str(repeated_id)


def test_list_sort_is_stable_across_offset_pages(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    with session_factory() as session:
        create_incident(session, dag_id="alpha")
        create_incident(session, dag_id="beta")
        create_incident(session, dag_id="gamma")
    app = create_app(settings, session_factory)

    complete = request(
        app,
        "GET",
        "/api/v1/incidents?sort=last_failure_at&order=desc&limit=3",
        headers=TOKEN_HEADER,
    ).json()["items"]
    first_page = request(
        app,
        "GET",
        "/api/v1/incidents?sort=last_failure_at&order=desc&limit=2&offset=0",
        headers=TOKEN_HEADER,
    ).json()["items"]
    second_page = request(
        app,
        "GET",
        "/api/v1/incidents?sort=last_failure_at&order=desc&limit=2&offset=2",
        headers=TOKEN_HEADER,
    ).json()["items"]

    assert [item["id"] for item in first_page + second_page] == [item["id"] for item in complete]


@pytest.mark.parametrize(
    "query",
    ["sort=unknown", "order=sideways"],
)
def test_list_rejects_unsupported_sorting(
    query: str,
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    response = request(
        create_app(settings, session_factory),
        "GET",
        f"/api/v1/incidents?{query}",
        headers=TOKEN_HEADER,
    )

    assert response.status_code == 422


def test_operator_transition_is_audited_and_stale_update_is_rejected(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    with session_factory() as session:
        incident_id, _ = create_incident(session)
    app = create_app(settings, session_factory)
    payload: dict[str, object] = {
        "status": "ACKNOWLEDGED",
        "expected_status": "OPEN",
        "reason": "Investigating the task failure",
    }

    changed = request(
        app,
        "PATCH",
        f"/api/v1/incidents/{incident_id}/status",
        headers=TOKEN_HEADER,
        json=payload,
    )
    stale = request(
        app,
        "PATCH",
        f"/api/v1/incidents/{incident_id}/status",
        headers=TOKEN_HEADER,
        json={**payload, "status": "IGNORED"},
    )
    detail = request(
        app,
        "GET",
        f"/api/v1/incidents/{incident_id}",
        headers=TOKEN_HEADER,
    )

    assert changed.status_code == 200
    assert changed.json()["previous_status"] == "OPEN"
    assert changed.json()["status"] == "ACKNOWLEDGED"
    assert changed.json()["transition_id"] is not None
    assert stale.status_code == 409
    assert detail.json()["incident"]["status"] == "ACKNOWLEDGED"
    assert detail.json()["transitions"] == [
        {
            "id": changed.json()["transition_id"],
            "previous_status": "OPEN",
            "status": "ACKNOWLEDGED",
            "initiator": "OPERATOR",
            "actor": "oncall@example.com",
            "reason": "Investigating the task failure",
            "created_at": detail.json()["transitions"][0]["created_at"],
        }
    ]


def test_operator_api_rejects_recovered_and_accepts_owner_terminal_override(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    with session_factory() as session:
        incident_id, _ = create_incident(session)
    app = create_app(settings, session_factory)

    recovered = request(
        app,
        "PATCH",
        f"/api/v1/incidents/{incident_id}/status",
        headers=TOKEN_HEADER,
        json={
            "status": "RECOVERED",
            "expected_status": "OPEN",
        },
    )
    resolved = request(
        app,
        "PATCH",
        f"/api/v1/incidents/{incident_id}/status",
        headers=TOKEN_HEADER,
        json={
            "status": "RESOLVED",
            "expected_status": "OPEN",
        },
    )
    reopened = request(
        app,
        "PATCH",
        f"/api/v1/incidents/{incident_id}/status",
        headers=TOKEN_HEADER,
        json={
            "status": "ACKNOWLEDGED",
            "expected_status": "RESOLVED",
        },
    )

    assert recovered.status_code == 422
    assert resolved.status_code == 200
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "ACKNOWLEDGED"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(IncidentStateTransitionRecord)) == 2


def test_terminal_incident_can_be_changed_only_by_its_last_actor(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    with session_factory() as session:
        owned_incident_id, _ = create_incident(session, task_id="owned")
        other_incident_id, _ = create_incident(session, task_id="other")
        transition_incident(
            session,
            incident_id=owned_incident_id,
            target=IncidentStatus.RESOLVED,
            initiator=IncidentTransitionInitiator.OPERATOR,
            actor="oncall@example.com",
        )
        transition_incident(
            session,
            incident_id=other_incident_id,
            target=IncidentStatus.IGNORED,
            initiator=IncidentTransitionInitiator.OPERATOR,
            actor="other@example.com",
        )
    app = create_app(settings, session_factory)

    reopened = request(
        app,
        "PATCH",
        f"/api/v1/incidents/{owned_incident_id}/status",
        headers=TOKEN_HEADER,
        json={"status": "OPEN", "expected_status": "RESOLVED"},
    )
    denied = request(
        app,
        "PATCH",
        f"/api/v1/incidents/{other_incident_id}/status",
        headers=TOKEN_HEADER,
        json={"status": "OPEN", "expected_status": "IGNORED"},
    )

    assert reopened.status_code == 200
    assert reopened.json()["previous_status"] == "RESOLVED"
    assert reopened.json()["status"] == "OPEN"
    assert denied.status_code == 403
    assert denied.json() == {
        "detail": "Only the operator who made the terminal change or an Admin may change it"
    }


def test_terminal_incident_cannot_reopen_over_an_existing_active_incident(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    with session_factory() as session:
        terminal_incident_id, _ = create_incident(session)
        transition_incident(
            session,
            incident_id=terminal_incident_id,
            target=IncidentStatus.RESOLVED,
            initiator=IncidentTransitionInitiator.OPERATOR,
            actor="oncall@example.com",
        )
        active_incident_id, _ = create_incident(
            session,
            dag_run_id="scheduled__2026-08-12",
            observed_at=NOW + timedelta(days=1),
        )
    assert active_incident_id != terminal_incident_id

    response = request(
        create_app(settings, session_factory),
        "PATCH",
        f"/api/v1/incidents/{terminal_incident_id}/status",
        headers=TOKEN_HEADER,
        json={"status": "ACKNOWLEDGED", "expected_status": "RESOLVED"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Another active Incident already exists for this failure group"
    }


def test_missing_incident_returns_not_found(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    missing_id = "00000000-0000-0000-0000-000000000001"
    app = create_app(settings, session_factory)

    detail = request(
        app,
        "GET",
        f"/api/v1/incidents/{missing_id}",
        headers=TOKEN_HEADER,
    )
    update = request(
        app,
        "PATCH",
        f"/api/v1/incidents/{missing_id}/status",
        headers=TOKEN_HEADER,
        json={
            "status": "ACKNOWLEDGED",
            "expected_status": "OPEN",
        },
    )

    assert detail.status_code == 404
    assert update.status_code == 404


def test_transition_rejects_browser_supplied_actor(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    with session_factory() as session:
        incident_id, _ = create_incident(session)

    response = request(
        create_app(settings, session_factory),
        "PATCH",
        f"/api/v1/incidents/{incident_id}/status",
        headers=TOKEN_HEADER,
        json={
            "status": "ACKNOWLEDGED",
            "expected_status": "OPEN",
            "actor": "spoofed@example.com",
        },
    )

    assert response.status_code == 422


def test_audit_insert_failure_rolls_back_incident_status(
    session: Session,
) -> None:
    incident_id, _ = create_incident(session)

    def reject_audit(*_: object) -> None:
        raise RuntimeError("simulated audit failure")

    sqlalchemy_event.listen(IncidentStateTransitionRecord, "before_insert", reject_audit)
    try:
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            transition_incident(
                session,
                incident_id=incident_id,
                target=IncidentStatus.ACKNOWLEDGED,
                initiator=IncidentTransitionInitiator.OPERATOR,
                actor="oncall@example.com",
                expected_status=IncidentStatus.OPEN,
            )
    finally:
        sqlalchemy_event.remove(IncidentStateTransitionRecord, "before_insert", reject_audit)

    incident = session.get(IncidentRecord, incident_id)
    assert incident is not None
    assert incident.status == IncidentStatus.OPEN
    assert session.scalar(select(func.count()).select_from(IncidentStateTransitionRecord)) == 0


def test_operator_token_is_not_accepted_as_ingest_token(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)

    response = request(
        app,
        "GET",
        "/api/v1/incidents",
        headers={"X-DagSentry-Operator-Token": "test-ingest-token"},
    )

    assert response.status_code == 401
