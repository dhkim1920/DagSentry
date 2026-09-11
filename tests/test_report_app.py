import sys
from unittest.mock import patch

import pytest

from dagsentry.report_app import main


def test_invalid_cli_timezone_precedes_engine_and_provider_creation() -> None:
    with (
        patch.object(sys, "argv", ["dagsentry-daily-report", "--timezone", "Not/A_Timezone"]),
        patch("dagsentry.report_app.create_session_factory") as factory,
        patch("dagsentry.report_app.create_report_summary_provider") as provider,
    ):
        with pytest.raises(SystemExit) as error:
            main()
    assert error.value.code == 2
    factory.assert_not_called()
    provider.assert_not_called()
