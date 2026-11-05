import pytest

from aca_agent_sample.types import ToolExecutionResult


@pytest.mark.parametrize("kwargs", [
    {"status": "unexpected"},
    {"status": "failed", "error_kind": "unexpected"},
    {"status": "succeeded", "error_kind": "outcome_unknown"},
    {"status": "succeeded", "stdout": None},
    {"status": "succeeded", "stderr": []},
    {"status": "succeeded", "exit_code": True},
    {"status": "succeeded", "exit_code": "0"},
    {"status": "succeeded", "metadata": []},
])
def test_execution_result_rejects_malformed_contract(kwargs):
    with pytest.raises(ValueError):
        ToolExecutionResult(**kwargs)
