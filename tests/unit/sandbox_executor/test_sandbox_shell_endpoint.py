"""/execute/shell and /execute/python's input staging (backend/services/sandbox_executor).

Both exist for the same reason: two workflow_runtime tools ran model-authored
work inside workflow_runtime's own process — `bash` via
subprocess.run(shell=True), `text_to_pandas` via exec() — because there was
nowhere else for a shell command to go, and no way to get a file to the place a
Python snippet could go. This service is that place.

Same pure-unit approach as test_sandbox_sql_backend_dispatch.py: main.py is
imported with docker/minio stubbed, so nothing here needs Docker.
"""
from __future__ import annotations

import base64
import pathlib
import sys
from unittest.mock import MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SVC_DIR = REPO_ROOT / "backend" / "services" / "sandbox_executor"

pytestmark = pytest.mark.unit

pytest.importorskip("fastapi", reason="fastapi not installed in this environment")
pytest.importorskip("sqlparse", reason="sqlparse not installed in this environment")


def _load_main(monkeypatch, *, shell_result: dict | None = None):
    from tests.unit._loader import load_module_from_path, stub_module

    monkeypatch.setenv("SANDBOX_SQL_BACKEND", "snowflake")

    stub_module(
        "minio_store",
        ensure_bucket=MagicMock(), put_code=MagicMock(), delete_code=MagicMock(),
    )
    stub_module(
        "docker_runner",
        run_python_sandboxed=MagicMock(),
        run_shell_sandboxed=MagicMock(return_value=shell_result or {
            "stdout": "hi\n", "stderr": "", "exit_code": 0, "timed_out": False,
        }),
        ExecutionTimeoutError=type("ExecutionTimeoutError", (Exception,), {}),
        SandboxRunError=type("SandboxRunError", (Exception,), {}),
        SandboxCircuitOpenError=type("SandboxCircuitOpenError", (Exception,), {}),
        get_pool=MagicMock(),
        shutdown_pool=MagicMock(),
    )
    for name in ("main", "sql_guard", "output", "job_queue", "security_visitor"):
        sys.modules.pop(name, None)

    return load_module_from_path("main", _SVC_DIR / "main.py", extra_syspath=[_SVC_DIR])


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


class TestInputStaging:
    """`files` is what lets text_to_pandas analyse a file it cannot hand over as
    a path — the sandbox has no view of the caller's filesystem, deliberately."""

    def test_a_valid_file_is_accepted(self, monkeypatch):
        main = _load_main(monkeypatch)

        req = main.ExecutePythonRequest(code="print(1)", files={"sales.csv": _b64(b"a,b\n1,2\n")})

        assert req.files["sales.csv"] == _b64(b"a,b\n1,2\n")

    def test_files_default_to_none_at_all(self, monkeypatch):
        """Every existing caller posts no `files` key; that has to stay valid."""
        main = _load_main(monkeypatch)

        assert main.ExecutePythonRequest(code="print(1)").files == {}

    @pytest.mark.parametrize("name", ["../etc/passwd", "sub/dir.csv", "..", ".", ""])
    def test_a_name_that_is_not_a_bare_filename_is_refused(self, monkeypatch, name):
        """The name becomes a tar entry extracted at the container's /tmp — a
        traversing name would be extracted somewhere else entirely."""
        main = _load_main(monkeypatch)

        with pytest.raises(Exception) as excinfo:
            main.ExecutePythonRequest(code="print(1)", files={name: _b64(b"x")})

        assert "bare filename" in str(excinfo.value)

    def test_content_that_is_not_base64_is_refused_at_submit_time(self, monkeypatch):
        """A 422 here says "this request is malformed", which is true and
        actionable; the same failure inside the worker is a mystery job error."""
        main = _load_main(monkeypatch)

        with pytest.raises(Exception) as excinfo:
            main.ExecutePythonRequest(code="print(1)", files={"a.csv": "not base64!!"})

        assert "not valid base64" in str(excinfo.value)

    def test_total_size_is_capped(self, monkeypatch):
        monkeypatch.setenv("SANDBOX_MAX_INPUT_BYTES", "16")
        main = _load_main(monkeypatch)

        with pytest.raises(Exception) as excinfo:
            main.ExecutePythonRequest(code="print(1)", files={"a.csv": _b64(b"x" * 64)})

        assert "over the 16-byte limit" in str(excinfo.value)

    def test_the_cap_is_on_the_total_not_on_each_file(self, monkeypatch):
        monkeypatch.setenv("SANDBOX_MAX_INPUT_BYTES", "16")
        main = _load_main(monkeypatch)

        # pydantic's ValidationError is a ValueError, so this stays narrow.
        with pytest.raises(ValueError):
            main.ExecutePythonRequest(
                code="print(1)", files={"a.csv": _b64(b"x" * 10), "b.csv": _b64(b"y" * 10)},
            )


class TestShellEndpoint:
    async def test_submitting_a_command_tags_the_job_as_shell(self, monkeypatch):
        """Kind is what routes the job to run_shell_sandboxed instead of
        run_python_sandboxed — they share one queue because they share one
        container pool."""
        main = _load_main(monkeypatch)
        queue = MagicMock()
        queue.submit.return_value = "job-1"
        monkeypatch.setattr(main, "python_queue", queue)

        result = await main.execute_shell(
            main.ExecuteShellRequest(command="echo hi", timeout_s=5, workdir="/tmp"),
        )

        assert result.status_code == 202
        payload = queue.submit.call_args[0][0]
        assert payload == {"kind": "shell", "command": "echo hi", "timeout_s": 5, "workdir": "/tmp"}

    async def test_python_jobs_are_tagged_too(self, monkeypatch):
        main = _load_main(monkeypatch)
        queue = MagicMock()
        queue.submit.return_value = "job-1"
        monkeypatch.setattr(main, "python_queue", queue)

        await main.execute_python(main.ExecutePythonRequest(code="print(1)"))

        assert queue.submit.call_args[0][0]["kind"] == "python"

    async def test_the_executor_dispatches_shell_to_the_shell_runner(self, monkeypatch):
        main = _load_main(monkeypatch)
        monkeypatch.setattr(main, "python_queue", MagicMock())

        result = await main._run_sandbox_job(
            {"kind": "shell", "command": "echo hi", "timeout_s": 5, "job_id": "j1"},
        )

        assert result["success"] is True
        assert result["exit_code"] == 0
        assert result["stdout"].startswith("hi")

    async def test_a_non_zero_exit_is_reported_not_swallowed(self, monkeypatch):
        """`success: false` with an exit code is how the caller tells "the
        command failed" from "the command could not be run"."""
        main = _load_main(monkeypatch, shell_result={
            "stdout": "", "stderr": "no such file", "exit_code": 2, "timed_out": False,
        })
        monkeypatch.setattr(main, "python_queue", MagicMock())

        result = await main._run_sandbox_job({"kind": "shell", "command": "cat x", "timeout_s": 5, "job_id": "j1"})

        assert result["success"] is False
        assert result["exit_code"] == 2
        assert result["stderr"] == "no such file"

    async def test_a_command_that_times_out_says_so(self, monkeypatch):
        main = _load_main(monkeypatch)
        # Patched on `main`, not on the stub module: main.py imported the name,
        # so rebinding it in docker_runner afterwards would change nothing.
        monkeypatch.setattr(main, "run_shell_sandboxed", MagicMock(
            side_effect=main.ExecutionTimeoutError("execution exceeded 5s timeout"),
        ))
        monkeypatch.setattr(main, "python_queue", MagicMock())

        result = await main._run_sandbox_job({"kind": "shell", "command": "sleep 99", "timeout_s": 5, "job_id": "j1"})

        assert result["timed_out"] is True
        assert result["success"] is False

    async def test_the_submitted_command_is_audited_and_then_removed(self, monkeypatch):
        """Same lifecycle as execute_python's copy of the submitted code: kept
        for the run, always deleted after it."""
        main = _load_main(monkeypatch)
        minio = sys.modules["minio_store"]
        monkeypatch.setattr(main, "python_queue", MagicMock())

        await main._run_sandbox_job({"kind": "shell", "command": "echo hi", "timeout_s": 5, "job_id": "j1"})

        assert minio.put_code.call_args[0][0] == "shell/j1.sh"
        assert minio.delete_code.call_args[0][0] == "shell/j1.sh"


class TestJobKindIsVisibleToOps:
    async def test_status_labels_a_shell_job_shell(self, monkeypatch):
        """Both kinds live in python_queue, so the dashboard's `kind` — which is
        what tells a client WHICH /execute/{kind}/ endpoint a job belongs to —
        has to come from the payload, not from which list it was found in."""
        main = _load_main(monkeypatch)
        from job_queue import JobQueue

        python_queue = JobQueue(main._run_sandbox_job, num_workers=1)
        sql_queue = JobQueue(main._run_execute_sql, num_workers=1)
        monkeypatch.setattr(main, "python_queue", python_queue)
        monkeypatch.setattr(main, "sql_queue", sql_queue)
        python_queue.submit({"kind": "shell", "command": "echo hi", "timeout_s": 5})
        python_queue.submit({"kind": "python", "code": "print(1)", "timeout_s": 5})

        status = await main.get_status()

        assert sorted(j["kind"] for j in status["jobs"]["python"]) == ["python", "shell"]
