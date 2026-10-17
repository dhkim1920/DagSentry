import subprocess
import sys

from dagsentry.extensions import PROVIDER_API_VERSION, LLMProvider, NotificationProvider
from dagsentry.llm import LLMProvider as CoreLLMProvider
from dagsentry.providers.notification_adapter import NotificationProviderAdapter


def test_public_protocols_are_the_runtime_protocols() -> None:
    assert PROVIDER_API_VERSION == 1
    assert LLMProvider is CoreLLMProvider
    assert NotificationProvider is NotificationProviderAdapter


def test_external_examples_pass_installed_contract_suites() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "examples/providers/test_gateway.py", "-q"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
