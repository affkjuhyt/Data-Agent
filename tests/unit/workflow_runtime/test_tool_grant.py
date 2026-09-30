"""Tool-grant preset system (sandbox_schema_docs.docx §1.9)."""
from __future__ import annotations

import pathlib
import sys

import pytest

from ._paths import RUNTIME_LAYER_DIRS

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "workflow_runtime")


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (*RUNTIME_LAYER_DIRS,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


@pytest.mark.unit
def test_default_preset_grants_both_sandbox_tools_when_unconfigured():
    from nodes.sandbox.tool_executor.tool_grant import check_sandbox_tool_granted
    check_sandbox_tool_granted("execute_python", {})  # must not raise
    check_sandbox_tool_granted("execute_sql", {})


@pytest.mark.unit
def test_non_sandbox_tool_is_always_a_noop():
    from nodes.sandbox.tool_executor.tool_grant import check_sandbox_tool_granted
    # No preset on earth includes "bash" — proves this module never touches
    # tools outside SANDBOX_TOOLS, regardless of cfg.
    check_sandbox_tool_granted("bash", {"tool_grant": {"preset": "sql_readonly"}})


@pytest.mark.unit
def test_sql_readonly_preset_rejects_execute_python():
    from nodes.sandbox.tool_executor.tool_grant import (
        ToolNotGrantedError,
        check_sandbox_tool_granted,
    )
    cfg = {"tool_grant": {"preset": "sql_readonly"}}
    check_sandbox_tool_granted("execute_sql", cfg)  # granted
    with pytest.raises(ToolNotGrantedError):
        check_sandbox_tool_granted("execute_python", cfg)


@pytest.mark.unit
def test_python_analysis_preset_rejects_execute_sql():
    from nodes.sandbox.tool_executor.tool_grant import (
        ToolNotGrantedError,
        check_sandbox_tool_granted,
    )
    cfg = {"tool_grant": {"preset": "python_analysis"}}
    check_sandbox_tool_granted("execute_python", cfg)
    with pytest.raises(ToolNotGrantedError):
        check_sandbox_tool_granted("execute_sql", cfg)


@pytest.mark.unit
def test_allowlist_adds_tool_outside_preset():
    from nodes.sandbox.tool_executor.tool_grant import AgentToolConfig
    config = AgentToolConfig(preset="sql_readonly", tools=["execute_python"])
    assert config.resolve_granted_tools() == {"execute_sql", "execute_python"}


@pytest.mark.unit
def test_denylist_removes_tool_from_preset():
    from nodes.sandbox.tool_executor.tool_grant import AgentToolConfig
    config = AgentToolConfig(preset="full_data_agent", disallowed_tools=["execute_python"])
    assert config.resolve_granted_tools() == {"execute_sql"}


@pytest.mark.unit
def test_denylist_takes_precedence_over_allowlist():
    from nodes.sandbox.tool_executor.tool_grant import AgentToolConfig
    config = AgentToolConfig(preset="sql_readonly", tools=["execute_python"], disallowed_tools=["execute_python"])
    assert config.resolve_granted_tools() == {"execute_sql"}


@pytest.mark.unit
def test_unknown_preset_falls_back_to_default():
    from nodes.sandbox.tool_executor.tool_grant import DEFAULT_PRESET, PRESETS, AgentToolConfig
    config = AgentToolConfig(preset="nonexistent_preset")
    assert config.resolve_granted_tools() == set(PRESETS[DEFAULT_PRESET]["tools"])
