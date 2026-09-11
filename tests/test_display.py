from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from dagsentry.config import Settings
from dagsentry.display import display_time, failure_state_label
from dagsentry.domain.failure_event import FailureState


def test_kst_date_boundary_and_configurable_timezone() -> None:
    value = datetime(2026, 9, 11, 15, tzinfo=UTC)
    assert display_time(value) == "2026-09-12 00:00:00 KST"
    assert display_time(value, "UTC") == "2026-09-11 15:00:00 UTC"
    assert failure_state_label(FailureState.UP_FOR_RETRY) == "재시도 대기 (UP_FOR_RETRY)"
    assert failure_state_label(FailureState.FAILED) == "최종 실패 (FAILED)"


def test_invalid_display_timezone_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(display_timezone="Not/A_Timezone")
