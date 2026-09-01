from pathlib import Path
from typing import Any

class DagBag:
    import_errors: dict[str, str]
    dags: dict[str, Any]
    def __init__(self, dag_folder: str | Path | None = ...) -> None: ...
