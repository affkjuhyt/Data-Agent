"""Nothing a model writes runs inside workflow_runtime's own process.

Two tools used to break that, both a long way inside the boundary the platform
describes:

  * `text_to_pandas` — `exec(compile(code, "<pandas_tool>", "exec"), ns)` on
    LLM-generated Python, in the process holding every tenant's database
    credentials, the LiteLLM key, and the event loop serving every other
    conversation.
  * `bash` — `subprocess.run(command, shell=True)` on an LLM-written string, in
    the same process. `tool_config.bash.enabled` gated it; a config flag is not
    a boundary.

Both now go to sandbox_executor, which runs them in the hardened pool container
(no network, read-only rootfs, tmpfs /tmp, pids/memory/cpu caps, killed and
wiped afterwards) that execute_python already used.

The first class is the durable part: an AST scan, so a future handler cannot
reintroduce either shape without failing here. It is AST rather than text
because the words `exec` and `shell=True` appear in the docstrings and prompts
that explain why they are gone, and a grep-shaped test would have to be
loosened until it stopped meaning anything.
"""
from __future__ import annotations

import ast
import base64
import pathlib
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "workflow_runtime")
_TOOL_EXECUTOR = REPO_ROOT / "backend" / "services" / "workflow_runtime" / "nodes" / "sandbox" / "tool_executor"

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _sys_path(monkeypatch):
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added

    try:
        import litellm  # noqa: F401
    except ModuleNotFoundError:
        # Importing ANY handler pulls in handlers/data.py, which imports litellm
        # at module scope. These tests replace the model call itself, so a
        # stand-in is enough — and it keeps the suite runnable where the real
        # (very large) package is not installed. Only registered when it is
        # genuinely absent, so a real installation is never shadowed.
        stub = types.ModuleType("litellm")
        stub.acompletion = AsyncMock()
        stub.completion = MagicMock()
        monkeypatch.setitem(sys.modules, "litellm", stub)

    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


# ── the regression guard ───────────────────────────────────────────────────

def _tool_executor_sources() -> list[pathlib.Path]:
    return sorted(p for p in _TOOL_EXECUTOR.rglob("*.py") if "__pycache__" not in p.parts)


def _called_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""


@pytest.mark.parametrize("path", _tool_executor_sources(), ids=lambda p: p.name)
def test_no_tool_handler_evaluates_code_in_process(path: pathlib.Path):
    """exec/eval anywhere in the tool-executor package is the escape itself."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = [
        f"{path.name}:{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _called_name(node) in ("exec", "eval")
    ]
    assert not offenders, (
        f"{offenders} runs model-authored code in workflow_runtime's process. "
        "It belongs in sandbox_executor (/execute/python)."
    )


@pytest.mark.parametrize("path", _tool_executor_sources(), ids=lambda p: p.name)
def test_no_tool_handler_spawns_a_shell(path: pathlib.Path):
    """shell=True hands a model-authored string to a shell. The `rg` invocation
    in filesystem.py is deliberately still allowed: a fixed binary with an argv
    list and `--` before the pattern is not a shell, and cannot become one."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = [
        f"{path.name}:{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for kw in node.keywords
        if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True
    ]
    assert not offenders, (
        f"{offenders} runs a shell in workflow_runtime's process. "
        "It belongs in sandbox_executor (/execute/shell)."
    )


# ── bash ───────────────────────────────────────────────────────────────────

def _bash_cfg(enabled: bool = True, **extra) -> dict:
    return {"tool_config": {"bash": {"enabled": enabled, **extra}}}


def _sandbox_reply(**overrides) -> dict:
    reply = {"success": True, "stdout": "hi\n", "stderr": "", "exit_code": 0,
             "timed_out": False, "is_truncated": False, "_http_status": 200}
    reply.update(overrides)
    return reply


def _capture(module, monkeypatch, reply: dict) -> dict:
    """Replace the module's submit_and_poll with a recorder. That function is the
    single seam where anything leaves this process, so a test that needs to stub
    subprocess or exec instead would mean the escape had come back."""
    seen: dict = {}

    async def _fake(http, path, body, overall_timeout_s):
        seen["path"] = path
        seen["body"] = body
        seen["overall_timeout_s"] = overall_timeout_s
        return reply

    monkeypatch.setattr(module, "submit_and_poll", _fake)
    return seen


async def test_bash_command_goes_to_the_sandbox_shell_endpoint(monkeypatch):
    import nodes.sandbox.tool_executor.handlers.shell as shell

    seen = _capture(shell, monkeypatch, _sandbox_reply())

    result, error = await shell._bash_handler({"arguments": {"command": "echo hi"}}, _bash_cfg())

    assert error is None
    assert seen["path"] == "/execute/shell"
    assert seen["body"]["command"] == "echo hi"
    assert result["content"].startswith("hi")
    assert result["metadata"]["executed_via"] == "sandbox_executor"


async def test_bash_cwd_becomes_a_directory_inside_the_sandbox(monkeypatch):
    """`cwd` used to name a path on workflow_runtime's filesystem. It is sent as
    the container's workdir now — the same word, a different machine."""
    import nodes.sandbox.tool_executor.handlers.shell as shell

    seen = _capture(shell, monkeypatch, _sandbox_reply())

    await shell._bash_handler({"arguments": {"command": "ls"}}, _bash_cfg(cwd="/tmp/work"))

    assert seen["body"]["workdir"] == "/tmp/work"


async def test_bash_omits_workdir_when_unset(monkeypatch):
    """An empty workdir is not a workdir: docker rejects a literal empty
    WorkingDir, so the key has to be absent rather than blank."""
    import nodes.sandbox.tool_executor.handlers.shell as shell

    seen = _capture(shell, monkeypatch, _sandbox_reply())

    await shell._bash_handler({"arguments": {"command": "ls"}}, _bash_cfg(cwd=""))

    assert "workdir" not in seen["body"]


async def test_bash_reports_a_non_zero_exit_as_content_not_an_error(monkeypatch):
    """`grep` matching nothing exits 1. That is an answer, and reporting it as a
    tool failure would make the agent retry a question already answered."""
    import nodes.sandbox.tool_executor.handlers.shell as shell

    _capture(shell, monkeypatch, _sandbox_reply(success=False, stdout="", exit_code=1))

    result, error = await shell._bash_handler({"arguments": {"command": "grep x f"}}, _bash_cfg())

    assert error is None
    assert "(exit code: 1)" in result["content"]
    assert result["metadata"]["exit_code"] == 1


async def test_bash_surfaces_a_sandbox_timeout(monkeypatch):
    import nodes.sandbox.tool_executor.handlers.shell as shell

    _capture(shell, monkeypatch, {"success": False, "error": "execution exceeded 5s timeout",
                                  "timed_out": True, "_http_status": 200})

    result, error = await shell._bash_handler(
        {"arguments": {"command": "sleep 99", "timeout": 5}}, _bash_cfg(),
    )

    assert result is None
    assert "timeout after 5s" in error["error"]


async def test_bash_stays_gated_by_the_enabled_flag(monkeypatch):
    """The gate is unchanged — it just no longer stands alone."""
    import nodes.sandbox.tool_executor.handlers.shell as shell

    seen = _capture(shell, monkeypatch, _sandbox_reply())

    result, error = await shell._bash_handler({"arguments": {"command": "echo hi"}}, _bash_cfg(enabled=False))

    assert result is None
    assert "not enabled" in error["error"]
    assert seen == {}, "a disabled tool must not reach sandbox_executor either"


# ── text_to_pandas ─────────────────────────────────────────────────────────

def _llm_code(code: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=code))]
    return resp


def _pandas_cfg(allowed_dir) -> dict:
    return {"tool_config": {"text_to_pandas": {"allowed_dir": str(allowed_dir)}}}


def _stub_llm(data, monkeypatch, code: str = "import pandas as pd\nresult = pd.read_csv('/tmp/sales.csv')") -> AsyncMock:
    generate = AsyncMock(return_value=_llm_code(code))
    monkeypatch.setattr(data.litellm, "acompletion", generate)
    return generate


async def test_pandas_sends_the_file_and_the_code_to_the_sandbox(monkeypatch, tmp_path):
    """The sandbox cannot see this service's disk — that is the isolation, so the
    bytes travel with the code rather than the path."""
    import nodes.sandbox.tool_executor.handlers.data as data

    csv = tmp_path / "sales.csv"
    csv.write_bytes(b"region,amount\nnorth,10\n")
    _stub_llm(data, monkeypatch)
    seen = _capture(data, monkeypatch, {"success": True, "stdout": "| region |", "_http_status": 200})

    result, error = await data._text_to_pandas_handler(
        {"arguments": {"file_path": str(csv)}, "query": "tong doanh thu"}, _pandas_cfg(tmp_path),
    )

    assert error is None
    assert seen["path"] == "/execute/python"
    assert base64.b64decode(seen["body"]["files"]["sales.csv"]) == csv.read_bytes()
    assert seen["body"]["code"].endswith(data._PANDAS_RENDER_EPILOGUE)
    assert result["metadata"]["executed_via"] == "sandbox_executor"


async def test_pandas_tells_the_model_the_in_sandbox_path(monkeypatch, tmp_path):
    """The generated code has to read the staged copy. A prompt still naming the
    host path would produce code that cannot find its own input."""
    import nodes.sandbox.tool_executor.handlers.data as data

    csv = tmp_path / "sales.csv"
    csv.write_bytes(b"a\n1\n")
    generate = _stub_llm(data, monkeypatch)
    _capture(data, monkeypatch, {"success": True, "stdout": "x", "_http_status": 200})

    await data._text_to_pandas_handler(
        {"arguments": {"file_path": str(csv)}, "query": "q"}, _pandas_cfg(tmp_path),
    )

    prompt = generate.await_args.kwargs["messages"][0]["content"]
    assert "/tmp/sales.csv" in prompt
    assert str(csv) not in prompt


async def test_pandas_reports_code_that_never_set_result(monkeypatch, tmp_path):
    """The epilogue prints a sentinel instead of raising, so "you forgot to
    assign result" stays distinguishable from "your code crashed"."""
    import nodes.sandbox.tool_executor.handlers.data as data

    csv = tmp_path / "sales.csv"
    csv.write_bytes(b"a\n1\n")
    _stub_llm(data, monkeypatch)
    _capture(data, monkeypatch, {"success": True, "stdout": data._NO_RESULT_SENTINEL, "_http_status": 200})

    result, error = await data._text_to_pandas_handler(
        {"arguments": {"file_path": str(csv)}, "query": "q"}, _pandas_cfg(tmp_path),
    )

    assert result is None
    assert error["error"] == "code did not set 'result'"


async def test_pandas_surfaces_a_security_rejection_without_retrying(monkeypatch, tmp_path):
    """Code outside the sandbox's import allowlist is refused there. That is a
    policy answer, so it comes back with the code that caused it."""
    import nodes.sandbox.tool_executor.handlers.data as data

    csv = tmp_path / "sales.csv"
    csv.write_bytes(b"a\n1\n")
    _stub_llm(data, monkeypatch, code="import os\nresult = os.listdir('/')")
    _capture(data, monkeypatch, {
        "success": False, "error": "security validation failed: Import blocked: os", "_http_status": 200,
    })

    result, error = await data._text_to_pandas_handler(
        {"arguments": {"file_path": str(csv)}, "query": "q"}, _pandas_cfg(tmp_path),
    )

    assert result is None
    assert "Import blocked: os" in error["error"]
    assert "import os" in error["code"]


async def test_pandas_refuses_a_file_too_large_to_stage_before_calling_the_model(monkeypatch, tmp_path):
    """Checked before generation: an LLM call spent on a file that can never be
    sent is pure waste, and the failure is knowable without it."""
    import nodes.sandbox.tool_executor.handlers.data as data

    csv = tmp_path / "big.csv"
    csv.write_bytes(b"x" * 2048)
    monkeypatch.setattr(data, "_MAX_PANDAS_INPUT_BYTES", 1024)
    generate = _stub_llm(data, monkeypatch)
    seen = _capture(data, monkeypatch, {"success": True, "stdout": "", "_http_status": 200})

    result, error = await data._text_to_pandas_handler(
        {"arguments": {"file_path": str(csv)}, "query": "q"}, _pandas_cfg(tmp_path),
    )

    assert result is None
    assert "over the 1024-byte sandbox input limit" in error["error"]
    generate.assert_not_awaited()
    assert seen == {}


async def test_pandas_still_refuses_a_path_outside_allowed_dir(monkeypatch, tmp_path):
    """Unchanged, and now load-bearing in a second way: allowed_dir is what
    decides which bytes this service is willing to hand to the sandbox."""
    import nodes.sandbox.tool_executor.handlers.data as data

    outside = tmp_path / "outside.csv"
    outside.write_bytes(b"a\n1\n")
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    seen = _capture(data, monkeypatch, {"success": True, "stdout": "", "_http_status": 200})

    result, error = await data._text_to_pandas_handler(
        {"arguments": {"file_path": str(outside)}, "query": "q"}, _pandas_cfg(allowed),
    )

    assert result is None
    assert "outside allowed_dir" in error["error"]
    assert seen == {}
