"""Deterministic masking and relevant excerpt generation for Task Try logs."""

from __future__ import annotations

import re
from dataclasses import dataclass

from dagsentry.config import Settings
from dagsentry.task_logs import LogCollectionStatus, TaskLogResult

MASK = "[REDACTED]"

_AUTHORIZATION = re.compile(r"(?im)(\bauthorization\s*:\s*)[^\r\n]+")
_CONNECTION_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^@/\s]+)@")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:password|passwd|pwd|access[_-]?token|refresh[_-]?token|token|"
    r"api[_-]?key|apikey|client[_-]?secret|secret)[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;]+)"
)

_TIMESTAMP = re.compile(
    r"(?<!\d)\d{4}-\d{2}-\d{2}(?:T|,\s*|\s+)\d{2}:\d{2}:\d{2}"
    r"(?:[.,]\d+)?(?:Z|\s*(?:UTC|[+-]\d{2}:?\d{2}))?"
)
_UUID = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}\b"
)
_TEMP_PATH = re.compile(
    r"(?i)(?:/(?:private/)?tmp|/var/folders)/[a-z0-9_./-]+"
    r"|[a-z]:\\[^\r\n\"']*\\(?:temp|tmp)\\[^\s\"']+"
)
_HOME_PATH = re.compile(
    r"(?i)(?:/Users|/home)/[^/\s\"']+"
    r"|[a-z]:\\+Users\\+[^\\\s\"']+"
)
_STACK_LINE_NUMBER = re.compile(r'(\bFile\s+"[^"\r\n]+",\s+line\s+)\d+')
_LABELED_ID = re.compile(
    r"(?i)(\b(?:request|job|execution|session|trace|span|record|row)[_-]?id\s*[:=]\s*)\d+\b"
)
_LONG_NUMBER = re.compile(r"(?<![\w-])\d{8,}(?![\w-])")
_HEX_ADDRESS = re.compile(r"(?i)\b0x[0-9a-f]{6,}\b")

_EXCEPTION_CLASS = re.compile(r"\b([A-Za-z_][\w.]*(?:Error|Exception))\s*:")
_VENDOR_CODE = re.compile(
    r"(?i)\b(?:ORA-\d{5}|SQLSTATE(?:\[[0-9A-Z]{5}\]|\s*[:=]\s*[0-9A-Z]{5}))(?![0-9A-Z])"
)
_STACK_FRAME = re.compile(r'^\s*File\s+"([^"\r\n]+)",\s+line\s+\d+,\s+in\s+([\w<>]+)')
_RELEVANT = re.compile(
    r"(?i)traceback \(most recent call last\)|during handling of the above exception|"
    r"the above exception was the direct cause|\b(?:error|exception|critical|fatal|failed|failure)\b|"
    r"\bORA-\d{5}\b|\bSQLSTATE\b|\bOOMKilled\b|out of memory|\bHTTP\s+[45]\d{2}\b"
)
_AIRFLOW_NOISE = (
    re.compile(r"(?i)dependencies all met"),
    re.compile(r"(?i)starting attempt \d+ of \d+"),
    re.compile(r"(?i)executing <Task\("),
    re.compile(r"(?i)exporting env vars:"),
    re.compile(r"(?i)marking task as (?:success|up_for_retry|failed)"),
)

_SOURCE_GROUP_START = "::group::Log message source details"
_SOURCE_GROUP_END = "::endgroup::"
_MAX_EXTRACTED_VALUES = 20
_FALLBACK_LINE_COUNT = 10


class LogInputTooLargeError(ValueError):
    """Raised before processing a log larger than the configured input limit."""


@dataclass(frozen=True)
class LogProcessingConfig:
    """Deterministic limits and deployment-specific masking patterns."""

    max_input_chars: int = 1_048_576
    max_excerpt_lines: int = 80
    max_excerpt_chars: int = 16_000
    context_lines: int = 2
    custom_secret_patterns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.max_input_chars < 1:
            raise ValueError("max_input_chars must be positive")
        if self.max_excerpt_lines < 1:
            raise ValueError("max_excerpt_lines must be positive")
        if self.max_excerpt_chars < 1:
            raise ValueError("max_excerpt_chars must be positive")
        if self.context_lines < 0:
            raise ValueError("context_lines must not be negative")

    @classmethod
    def from_settings(cls, settings: Settings) -> LogProcessingConfig:
        """Build log-processing limits from Worker process settings."""
        return cls(
            max_input_chars=settings.log_max_input_chars,
            max_excerpt_lines=settings.log_excerpt_max_lines,
            max_excerpt_chars=settings.log_excerpt_max_chars,
            context_lines=settings.log_excerpt_context_lines,
            custom_secret_patterns=tuple(settings.log_secret_patterns),
        )


@dataclass(frozen=True)
class ExcerptLine:
    """One sanitized excerpt line tied to its original one-based position."""

    line_id: int
    text: str


@dataclass(frozen=True)
class RelevantLogExcerpt:
    """Bounded evidence and structured values extracted without AI."""

    lines: tuple[ExcerptLine, ...]
    exception_classes: tuple[str, ...]
    vendor_error_codes: tuple[str, ...]
    application_stack_frames: tuple[str, ...]
    input_line_count: int


@dataclass(frozen=True)
class _SourceLine:
    line_id: int
    text: str


class LogProcessor:
    """Mask secrets before selecting and normalizing relevant log evidence."""

    def __init__(self, config: LogProcessingConfig | None = None) -> None:
        self.config = config or LogProcessingConfig()
        try:
            self.custom_secret_patterns = tuple(
                re.compile(pattern) for pattern in self.config.custom_secret_patterns
            )
        except re.error as error:
            raise ValueError(f"Invalid custom Secret pattern: {error}") from error

    def process_result(self, result: TaskLogResult) -> RelevantLogExcerpt | None:
        """Process available content and preserve LOG_UNAVAILABLE as a normal path."""
        if result.status == LogCollectionStatus.LOG_UNAVAILABLE:
            return None
        assert result.content is not None
        return self.process(result.content)

    def process(self, raw_log: str) -> RelevantLogExcerpt:
        """Create a deterministic, bounded excerpt from one raw Task Try log."""
        if len(raw_log) > self.config.max_input_chars:
            raise LogInputTooLargeError(
                f"Task log exceeds {self.config.max_input_chars} character limit"
            )

        masked = self.mask_secrets(raw_log)
        source_lines = [
            _SourceLine(line_id=index, text=line)
            for index, line in enumerate(masked.splitlines(), start=1)
        ]
        cleaned = _remove_noise(source_lines)
        exception_classes = _unique_matches(_EXCEPTION_CLASS, cleaned)
        vendor_error_codes = tuple(
            value.upper() for value in _unique_matches(_VENDOR_CODE, cleaned)
        )
        application_frames = _application_frames(cleaned)
        selected = _select_relevant(cleaned, self.config.context_lines)
        normalized = [
            ExcerptLine(line_id=line.line_id, text=_normalize_dynamic_values(line.text))
            for line in selected
        ]
        bounded = _bound_excerpt(
            normalized,
            max_lines=self.config.max_excerpt_lines,
            max_chars=self.config.max_excerpt_chars,
        )
        return RelevantLogExcerpt(
            lines=tuple(bounded),
            exception_classes=exception_classes,
            vendor_error_codes=vendor_error_codes,
            application_stack_frames=application_frames,
            input_line_count=len(source_lines),
        )

    def mask_secrets(self, value: str) -> str:
        """Mask Secret patterns in arbitrary text without excerpt selection or normalization."""
        masked = _AUTHORIZATION.sub(rf"\1{MASK}", value)
        masked = _CONNECTION_CREDENTIALS.sub(rf"\1{MASK}:{MASK}@", masked)
        masked = _SECRET_ASSIGNMENT.sub(rf"\1{MASK}", masked)
        for pattern in self.custom_secret_patterns:
            masked = pattern.sub(MASK, masked)
        return masked


def _remove_noise(lines: list[_SourceLine]) -> list[_SourceLine]:
    cleaned: list[_SourceLine] = []
    inside_source_group = False
    previous_text: str | None = None
    for line in lines:
        stripped = line.text.strip()
        if stripped == _SOURCE_GROUP_START:
            inside_source_group = True
            continue
        if stripped == _SOURCE_GROUP_END and inside_source_group:
            inside_source_group = False
            continue
        if inside_source_group or not stripped:
            continue
        if any(pattern.search(line.text) for pattern in _AIRFLOW_NOISE):
            continue
        if line.text == previous_text:
            continue
        cleaned.append(line)
        previous_text = line.text
    return cleaned


def _unique_matches(pattern: re.Pattern[str], lines: list[_SourceLine]) -> tuple[str, ...]:
    values: list[str] = []
    for line in lines:
        values.extend(
            match.group(1) if match.lastindex else match.group(0)
            for match in pattern.finditer(line.text)
        )
    return tuple(dict.fromkeys(values))[:_MAX_EXTRACTED_VALUES]


def _application_frames(lines: list[_SourceLine]) -> tuple[str, ...]:
    frames: list[str] = []
    for line in lines:
        match = _STACK_FRAME.search(line.text)
        if match is None:
            continue
        path = match.group(1).replace("\\", "/")
        if "/site-packages/" in path or re.search(r"/lib/python\d", path):
            continue
        frames.append(_normalize_dynamic_values(line.text.strip()))
    return tuple(dict.fromkeys(frames))[:_MAX_EXTRACTED_VALUES]


def _select_relevant(lines: list[_SourceLine], context_lines: int) -> list[_SourceLine]:
    if not lines:
        return []
    anchors = {
        index
        for index, line in enumerate(lines)
        if _RELEVANT.search(line.text) or _STACK_FRAME.search(line.text)
    }
    if not anchors:
        return lines[-_FALLBACK_LINE_COUNT:]

    selected_indexes: set[int] = set()
    for index in anchors:
        start = max(0, index - context_lines)
        end = min(len(lines), index + context_lines + 1)
        selected_indexes.update(range(start, end))
    return [line for index, line in enumerate(lines) if index in selected_indexes]


def _normalize_dynamic_values(value: str) -> str:
    normalized = _TIMESTAMP.sub("<TIMESTAMP>", value)
    normalized = _UUID.sub("<UUID>", normalized)
    normalized = _HOME_PATH.sub("<HOME_PATH>", normalized)
    normalized = _TEMP_PATH.sub("<TEMP_PATH>", normalized)
    normalized = _STACK_LINE_NUMBER.sub(r"\1<LINE>", normalized)
    normalized = _LABELED_ID.sub(r"\1<ID>", normalized)
    normalized = _HEX_ADDRESS.sub("<HEX>", normalized)
    return _LONG_NUMBER.sub("<NUMBER>", normalized)


def _bound_excerpt(
    lines: list[ExcerptLine], *, max_lines: int, max_chars: int
) -> list[ExcerptLine]:
    limited_lines = lines[-max_lines:]
    remaining = max_chars
    bounded_reversed: list[ExcerptLine] = []
    for line in reversed(limited_lines):
        if remaining == 0:
            break
        text = line.text
        if len(text) > remaining:
            if bounded_reversed:
                break
            text = text[-remaining:]
        bounded_reversed.append(ExcerptLine(line.line_id, text))
        remaining -= len(text)
    return list(reversed(bounded_reversed))
