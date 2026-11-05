from dataclasses import dataclass, field
from typing import Any, Literal, get_args

ErrorKind = Literal[
    "auth", "forbidden", "timeout", "schema_mismatch", "execution_failed",
    "outcome_unknown", "state_lost",
]


@dataclass(frozen=True)
class ToolExecutionResult:
    status: Literal["succeeded", "failed"]
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    error_kind: ErrorKind | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in {"succeeded", "failed"}:
            raise ValueError("Invalid execution status")
        if not isinstance(self.stdout, str) or not isinstance(self.stderr, str):
            raise ValueError("Execution output must be text")
        if self.exit_code is not None and type(self.exit_code) is not int:
            raise ValueError("Execution exit code must be an integer or None")
        if self.error_kind is not None and self.error_kind not in get_args(ErrorKind):
            raise ValueError("Invalid execution error kind")
        if self.status == "succeeded" and self.error_kind is not None:
            raise ValueError("Successful execution cannot have an error kind")
        if not isinstance(self.metadata, dict):
            raise ValueError("Execution metadata must be a dictionary")
