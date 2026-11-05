"""Azure Container Apps sandbox adapters and command agent."""

from .client import AcaSandboxAdapter, FakeSandboxAdapter
from .command_agent import SandboxCommandAgent
from .lifecycle_benchmark import (
    ModeComparisonMetrics, SandboxLifecycleComparator, SnapshotModeOracle,
    WorkerObservation, aggregate_metrics, evaluate_mode_observation,
    metrics_to_csv, metrics_to_json, metrics_to_markdown,
)

__all__ = [
    "AcaSandboxAdapter", "FakeSandboxAdapter", "SandboxCommandAgent", "ModeComparisonMetrics",
    "SandboxLifecycleComparator", "SnapshotModeOracle", "WorkerObservation", "aggregate_metrics",
    "evaluate_mode_observation", "metrics_to_csv", "metrics_to_json", "metrics_to_markdown",
]
