"""Deterministic rule-based diagnosis over sanitized log evidence."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from dagsentry.domain.diagnosis import ErrorClassification as ErrorClassification
from dagsentry.domain.diagnosis import RetryDecision
from dagsentry.log_processing import RelevantLogExcerpt

RULESET_VERSION = 2


@dataclass(frozen=True)
class ExtractedValue:
    """One reproducible value supporting a rule match."""

    name: str
    value: str


@dataclass(frozen=True)
class RuleDiagnosisInput:
    """Metadata and sanitized evidence available to deterministic rules."""

    operator_type: str | None
    excerpt: RelevantLogExcerpt | None


@dataclass(frozen=True)
class RuleDiagnosisResult:
    """Structured and reproducible result selected by the Rule Engine."""

    classification: ErrorClassification
    matched_rule: str
    ruleset_version: int
    confidence: float
    confidence_reason: str
    extracted_values: tuple[ExtractedValue, ...]
    evidence_line_ids: tuple[int, ...]
    recommended_actions: tuple[str, ...] = ()
    retry_decision: RetryDecision = RetryDecision.UNKNOWN

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if self.ruleset_version < 1:
            raise ValueError("ruleset_version must be positive")


@dataclass(frozen=True)
class RuleMatch:
    """A candidate diagnosis produced by one rule."""

    classification: ErrorClassification
    rule_id: str
    confidence: float
    confidence_reason: str
    extracted_values: tuple[ExtractedValue, ...]
    evidence_line_ids: tuple[int, ...]
    recommended_actions: tuple[str, ...] = ()
    retry_decision: RetryDecision = RetryDecision.UNKNOWN


class DiagnosisRule(Protocol):
    """Minimal extension boundary for deterministic diagnosis rules."""

    @property
    def rule_id(self) -> str:
        """Stable versioned rule identifier."""

    @property
    def priority(self) -> int:
        """Specificity priority used for conflict resolution."""

    def evaluate(self, diagnosis_input: RuleDiagnosisInput) -> RuleMatch | None:
        """Return a match only when the input satisfies this rule."""


@dataclass(frozen=True)
class PatternRule:
    """A rule backed by one explicit case-insensitive log pattern."""

    rule_id: str
    priority: int
    classification: ErrorClassification
    pattern: re.Pattern[str]
    extracted_name: str
    extracted_value: str
    confidence: float
    confidence_reason: str
    recommended_actions: tuple[str, ...] = ()
    retry_decision: RetryDecision = RetryDecision.UNKNOWN

    def evaluate(self, diagnosis_input: RuleDiagnosisInput) -> RuleMatch | None:
        excerpt = diagnosis_input.excerpt
        if excerpt is None:
            return None

        latest_line_id: int | None = None
        for line in excerpt.lines:
            match = self.pattern.search(line.text)
            if match is None:
                continue
            latest_line_id = line.line_id
        if latest_line_id is None:
            return None
        return RuleMatch(
            classification=self.classification,
            rule_id=self.rule_id,
            confidence=self.confidence,
            confidence_reason=self.confidence_reason,
            extracted_values=(ExtractedValue(self.extracted_name, self.extracted_value),),
            evidence_line_ids=(latest_line_id,),
            recommended_actions=self.recommended_actions,
            retry_decision=self.retry_decision,
        )


@dataclass(frozen=True)
class PythonDagExceptionRule:
    """Classify an explicit Python exception only when application code is in the stack."""

    rule_id: str = "python.application_exception.v1"
    priority: int = 50

    def evaluate(self, diagnosis_input: RuleDiagnosisInput) -> RuleMatch | None:
        excerpt = diagnosis_input.excerpt
        if excerpt is None or not excerpt.exception_classes or not excerpt.application_stack_frames:
            return None

        exception_class = excerpt.exception_classes[-1]
        application_frame = excerpt.application_stack_frames[-1]
        evidence_line_id = next(
            (line.line_id for line in reversed(excerpt.lines) if exception_class in line.text),
            excerpt.lines[-1].line_id,
        )
        return RuleMatch(
            classification=ErrorClassification.DAG_CODE,
            rule_id=self.rule_id,
            confidence=0.85,
            confidence_reason="Python exception is backed by an application stack frame",
            extracted_values=(
                ExtractedValue("exception_class", exception_class),
                ExtractedValue("application_stack_frame", application_frame),
            ),
            evidence_line_ids=(evidence_line_id,),
            recommended_actions=(
                "트레이스백의 DAG 코드 위치와 입력값을 확인하고 예외 원인을 수정하세요.",
            ),
            retry_decision=RetryDecision.NOT_RETRYABLE,
        )


class RuleEngine:
    """Evaluate all rules and choose one match with deterministic conflict resolution."""

    def __init__(self, rules: Iterable[DiagnosisRule] | None = None) -> None:
        configured_rules = tuple(rules) if rules is not None else default_rules()
        rule_ids = [rule.rule_id for rule in configured_rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("Rule IDs must be unique")
        self.rules = configured_rules

    def diagnose(self, diagnosis_input: RuleDiagnosisInput) -> RuleDiagnosisResult:
        """Return the highest-priority match or a structured UNKNOWN fallback."""
        candidates = [
            (rule, match)
            for rule in self.rules
            if (match := rule.evaluate(diagnosis_input)) is not None
        ]
        if not candidates:
            return RuleDiagnosisResult(
                classification=ErrorClassification.UNKNOWN,
                matched_rule="unknown.v1",
                ruleset_version=RULESET_VERSION,
                confidence=0.0,
                confidence_reason="No supported deterministic rule matched",
                extracted_values=(),
                evidence_line_ids=(),
                recommended_actions=(
                    "Airflow Task 로그와 실행 환경을 확인하여 실패 원인을 점검하세요.",
                ),
            )

        _, selected = min(
            candidates,
            key=lambda candidate: (
                -candidate[0].priority,
                -max(candidate[1].evidence_line_ids),
                candidate[0].rule_id,
            ),
        )
        return RuleDiagnosisResult(
            classification=selected.classification,
            matched_rule=selected.rule_id,
            ruleset_version=RULESET_VERSION,
            confidence=selected.confidence,
            confidence_reason=selected.confidence_reason,
            extracted_values=selected.extracted_values,
            evidence_line_ids=selected.evidence_line_ids,
            recommended_actions=selected.recommended_actions,
            retry_decision=selected.retry_decision,
        )


def default_rules() -> tuple[DiagnosisRule, ...]:
    """Return the v2 ruleset with Korean advisory actions."""
    rules: tuple[DiagnosisRule, ...] = (
        PatternRule(
            rule_id="http.authentication_401.v1",
            priority=100,
            classification=ErrorClassification.AUTHENTICATION,
            pattern=re.compile(
                r"(?i)\b(?:(?:HTTP(?:\s+(?:response|status))?\s*[:=]?\s*|"
                r"status(?:_code|\s+code)?\s*[:=]\s*)401|401\s+(?:Client\s+Error|Unauthorized))\b"
            ),
            extracted_name="http_status",
            extracted_value="401",
            confidence=0.99,
            confidence_reason="Explicit HTTP 401 status was found in sanitized log evidence",
            recommended_actions=(
                "인증 정보의 만료와 요청 대상의 인증 설정을 확인하고 갱신하세요.",
            ),
            retry_decision=RetryDecision.NOT_RETRYABLE,
        ),
        PatternRule(
            rule_id="http.authorization_403.v1",
            priority=100,
            classification=ErrorClassification.AUTHORIZATION,
            pattern=re.compile(
                r"(?i)\b(?:(?:HTTP(?:\s+(?:response|status))?\s*[:=]?\s*|"
                r"status(?:_code|\s+code)?\s*[:=]\s*)403|403\s+(?:Client\s+Error|Forbidden))\b"
            ),
            extracted_name="http_status",
            extracted_value="403",
            confidence=0.99,
            confidence_reason="Explicit HTTP 403 status was found in sanitized log evidence",
            recommended_actions=("실행 계정의 대상 리소스 접근 권한과 권한 설정을 확인하세요.",),
            retry_decision=RetryDecision.NOT_RETRYABLE,
        ),
        PatternRule(
            rule_id="resource.out_of_memory.v1",
            priority=90,
            classification=ErrorClassification.RESOURCE,
            pattern=re.compile(r"(?i)\b(?:OOMKilled|out of memory|MemoryError)\b"),
            extracted_name="resource_signal",
            extracted_value="OUT_OF_MEMORY",
            confidence=0.98,
            confidence_reason="Explicit out-of-memory signal was found in sanitized log evidence",
            recommended_actions=(
                "메모리 사용량과 제한을 확인하고 처리 데이터 크기나 메모리 할당을 조정하세요.",
            ),
            retry_decision=RetryDecision.NOT_RETRYABLE,
        ),
        PatternRule(
            rule_id="oracle.no_listener.v1",
            priority=80,
            classification=ErrorClassification.SOURCE_DATABASE,
            pattern=re.compile(r"(?i)\bORA-12541\b"),
            extracted_name="vendor_error_code",
            extracted_value="ORA-12541",
            confidence=0.99,
            confidence_reason="Oracle ORA-12541 was found in sanitized log evidence",
            recommended_actions=(
                "Oracle Listener 상태와 접속 호스트·포트 및 네트워크 연결을 확인하세요.",
            ),
            retry_decision=RetryDecision.UNKNOWN,
        ),
        PythonDagExceptionRule(),
    )
    return rules
