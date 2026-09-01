from typing import Any

from dagsentry.airflow.collector import collect_retry_failure
from dagsentry.airflow.policy import install_retry_callback


class FakeTask:
    def __init__(self, callback: Any = None) -> None:
        self.on_retry_callback = callback


def existing_callback(_: object) -> None:
    pass


def test_policy_installs_callback_when_missing() -> None:
    task = FakeTask()

    install_retry_callback(task)

    assert task.on_retry_callback is collect_retry_failure


def test_policy_preserves_existing_callback_order() -> None:
    task = FakeTask(existing_callback)

    install_retry_callback(task)

    assert task.on_retry_callback == [existing_callback, collect_retry_failure]


def test_policy_is_idempotent_for_callback_list() -> None:
    task = FakeTask([existing_callback])

    install_retry_callback(task)
    install_retry_callback(task)

    assert task.on_retry_callback == [existing_callback, collect_retry_failure]
