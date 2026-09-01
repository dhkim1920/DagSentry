from __future__ import annotations

import os
from uuid import uuid4

import pytest

from dagsentry.config import Settings
from dagsentry.db import SessionFactory
from dagsentry.domain.connection import ConnectionProvider, ConnectionPurpose
from dagsentry.domain.diagnosis import DiagnosisValidationStatus
from dagsentry.evidence_validation import validate_ai_evidence
from dagsentry.log_processing import LogProcessor
from dagsentry.models import ManagedConnectionRecord
from dagsentry.runtime_connections import ConfiguredLLMConnectionResolver
from tests.contracts.llm import diagnosis_request

pytestmark = pytest.mark.integration


def test_database_ollama_connection_returns_valid_ai_diagnosis(
    session_factory: SessionFactory,
) -> None:
    api_base_url = os.environ.get("DAGSENTRY_TEST_OLLAMA_API_BASE_URL")
    model = os.environ.get("DAGSENTRY_TEST_OLLAMA_MODEL")
    if api_base_url is None or model is None:
        pytest.skip("DAGSENTRY_TEST_OLLAMA_API_BASE_URL and MODEL are not configured")

    connection_id = uuid4()
    with session_factory() as session, session.begin():
        session.add(
            ManagedConnectionRecord(
                id=connection_id,
                environment="ollama-live",
                purpose=ConnectionPurpose.LLM,
                provider=ConnectionProvider.OLLAMA,
                display_name="Ollama live test",
                non_secret_config={
                    "api_base_url": api_base_url,
                    "model": model,
                    "max_output_tokens": 1_024,
                    "timeout_seconds": 120,
                    "max_attempts": 1,
                },
                enabled=True,
                version=1,
            )
        )

    snapshot = ConfiguredLLMConnectionResolver(
        Settings(llm_config_source="database"),
        session_factory,
    ).resolve("ollama-live")
    assert snapshot.connection_id == connection_id
    assert snapshot.provider is not None

    request = diagnosis_request()
    result = snapshot.provider.diagnose(request)
    validation = validate_ai_evidence(
        result.diagnosis,
        request.excerpt,
        log_processor=LogProcessor(),
    )

    assert result.metadata.model
    assert validation.status == DiagnosisValidationStatus.PASSED
