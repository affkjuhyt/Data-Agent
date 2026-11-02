"""Python the model writes over the turn's datasets, run apart from the worker (after DataAgent's
PythonExecutor): a static check of the code, then a child interpreter with no environment, a
temporary directory, CPU and memory limits and a wall-clock timeout.

The code sees each dataset as a pandas DataFrame named by its id (`d1`, `d2`, …) and must leave
its answer in `result` (a DataFrame, a Series, a dict or a number). What it prints comes back too.
The numbers it computes are deterministic computations over figures already fetched, so they are
traced like any other dataset cell (verify.py); the model still never states one itself.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from typing import Any

ALLOWED_IMPORTS = {
    "pandas",
    "numpy",
    "math",
    "statistics",
    "datetime",
    "json",
    "re",
    "collections",
    "itertools",
    "functools",
    "decimal",
}
FORBIDDEN_NAMES = {
    "open",
    "exec",
    "eval",
    "compile",
    "__import__",
    "globals",
    "locals",
    "vars",
    "input",
    "breakpoint",
    "getattr",
    "setattr",
    "delattr",
    "memoryview",
    "exit",
    "quit",
    "help",
}
# Attributes that read or write files, reach the network or evaluate strings.
FORBIDDEN_ATTR_PREFIXES = (
    "read_",
    "to_csv",
    "to_excel",
    "to_json",
    "to_parquet",
    "to_pickle",
    "to_sql",
    "to_hdf",
    "to_feather",
    "to_html",
    "to_latex",
    "to_clipboard",
    "to_markdown",
    "to_stata",
    "to_orc",
    "to_xml",
    "to_gbq",
)
# pandas and numpy are imported whole (`import pandas as pd`), never a submodule or a name from
# them, so a reader of files or the network cannot be reached by a bare name.
WHOLE_ONLY = {"pandas", "numpy"}
FORBIDDEN_ATTRS = {
    "loadtxt",
    "genfromtxt",
    "fromregex",
    "DataSource",
    "ExcelFile",
    "ExcelWriter",
    "HDFStore",
    "f2py",
    "compat",
    "core",
    "api",
    "util",
    "plotting",
    "style",
    "memmap",
    "eval",
    "query",
    "io",
    "system",
    "popen",
    "load",
    "save",
    "fromfile",
    "tofile",
    "ctypeslib",
    "lib",
    "testing",
}

TIMEOUT_S = 20
CPU_S = 15
MEMORY_BYTES = 1024 * 1024 * 1024
MAX_RESULT_ROWS = 2000
MAX_STDOUT = 4000


class CodeRejected(ValueError):
    pass


def _forbidden_name(name: str) -> bool:
    return (
        "__" in name
        or name in FORBIDDEN_NAMES
        or name in FORBIDDEN_ATTRS
        or name.startswith(FORBIDDEN_ATTR_PREFIXES)
    )


def check(code: str) -> None:
    """CodeRejected when the code imports, calls or reaches what the sandbox does not allow."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise CodeRejected(f"lỗi cú pháp dòng {exc.lineno}: {exc.msg}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in ALLOWED_IMPORTS or (top in WHOLE_ONLY and "." in alias.name):
                    raise CodeRejected(f"không được import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            top = module.split(".")[0]
            if top not in ALLOWED_IMPORTS or top in WHOLE_ONLY or node.level:
                raise CodeRejected(f"không được import từ {module}")
            for alias in node.names:
                if _forbidden_name(alias.name) or _forbidden_name(alias.asname or ""):
                    raise CodeRejected(f"không được import {alias.name}")
        elif isinstance(node, ast.Name) and _forbidden_name(node.id):
            raise CodeRejected(f"không được dùng {node.id}")
        elif isinstance(node, ast.Attribute):
            if (
                node.attr.startswith("__")
                or node.attr in FORBIDDEN_ATTRS
                or node.attr.startswith(FORBIDDEN_ATTR_PREFIXES)
            ):
                raise CodeRejected(f"không được dùng .{node.attr}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and "__" in node.value:
            raise CodeRejected("không được dùng chuỗi có '__'")


# Runs in the child: builds the DataFrames, runs the code with a narrowed builtins and import,
# and prints one JSON line with the result.
RUNNER = r"""
import builtins, io, json, sys, contextlib
import pandas as pd, numpy as np
ALLOWED = set(json.loads(sys.argv[1]))
payload = json.loads(sys.stdin.read())
real_import = builtins.__import__
def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    # Only the analysis code imports through this: a library's own imports use its module's
    # builtins, not these, so nothing outside the list is ever let through here.
    if level or name.split(".")[0] not in ALLOWED:
        raise ImportError("import not allowed: " + name)
    return real_import(name, globals, locals, fromlist, level)
safe = {k: getattr(builtins, k) for k in dir(builtins) if k not in json.loads(sys.argv[2])}
safe["__import__"] = guarded_import
env = {"__builtins__": safe, "pd": pd, "np": np}
for name, ds in payload["datasets"].items():
    frame = pd.DataFrame(ds["rows"], columns=ds["columns"])
    for col in frame.columns:
        converted = pd.to_numeric(frame[col], errors="coerce")
        if converted.notna().sum() == frame[col].notna().sum():
            frame[col] = converted
    env[name] = frame
out = io.StringIO()
error = None
try:
    with contextlib.redirect_stdout(out):
        exec(compile(payload["code"], "<analysis>", "exec"), env)
except Exception as exc:
    error = type(exc).__name__ + ": " + str(exc)[:500]
result = env.get("result")
def plain(v):
    if isinstance(v, (np.integer,)): return int(v)
    if isinstance(v, (np.floating,)): return None if np.isnan(v) else float(v)
    if isinstance(v, float) and v != v: return None
    if isinstance(v, (pd.Timestamp,)): return v.isoformat()
    if isinstance(v, (str, int, float, bool)) or v is None: return v
    return str(v)
shape = None
if error is None and result is not None:
    if isinstance(result, pd.Series):
        result = result.reset_index()
    if isinstance(result, pd.DataFrame):
        if not isinstance(result.index, pd.RangeIndex):
            result = result.reset_index()
        cols = [str(c) for c in result.columns]
        rows = [[plain(v) for v in r] for r in result.itertuples(index=False, name=None)]
        shape = {"columns": cols, "rows": rows[:MAXROWS], "truncated": len(rows) > MAXROWS}
    elif isinstance(result, dict):
        shape = {"columns": ["chỉ tiêu", "giá trị"], "rows": [[str(k), plain(v)] for k, v in result.items()], "truncated": False}
    else:
        shape = {"columns": ["giá trị"], "rows": [[plain(result)]], "truncated": False}
print(json.dumps({"error": error, "result": shape, "stdout": out.getvalue()[-STDOUT:]}, ensure_ascii=False))
""".replace("MAXROWS", str(MAX_RESULT_ROWS)).replace("STDOUT", str(MAX_STDOUT))


@dataclass
class Execution:
    ok: bool
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    stdout: str
    error: str | None


def _limits() -> None:  # pragma: no cover - runs in the child
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (CPU_S, CPU_S))
    # macOS refuses RLIMIT_AS; the pod's memory limit still applies there.
    with contextlib.suppress(ValueError, OSError):
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    os.setsid()


_NO_NETWORK: list[str] | None = None


async def _no_network() -> list[str]:
    """`unshare -rn` before the interpreter when this host lets an unprivileged process open a
    network namespace with no interface; else nothing (the static check still applies). Probed
    once per worker."""
    global _NO_NETWORK
    if _NO_NETWORK is None:
        _NO_NETWORK = []
        unshare = shutil.which("unshare")
        if unshare:
            probe = await asyncio.create_subprocess_exec(
                unshare,
                "-rn",
                "true",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            if await probe.wait() == 0:
                _NO_NETWORK = [unshare, "-rn"]
    return _NO_NETWORK


async def run(code: str, datasets: dict[str, dict[str, Any]]) -> Execution:
    check(code)
    payload = json.dumps({"code": code, "datasets": datasets}, ensure_ascii=False, default=str)
    with tempfile.TemporaryDirectory(prefix="analysis-") as cwd:
        proc = await asyncio.create_subprocess_exec(
            *(await _no_network()),
            sys.executable,
            "-I",
            "-c",
            RUNNER,
            json.dumps(sorted(ALLOWED_IMPORTS)),
            json.dumps(sorted(FORBIDDEN_NAMES - {"__import__"})),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env={"PATH": "/usr/bin:/bin", "HOME": cwd, "OPENBLAS_NUM_THREADS": "1"},
            preexec_fn=_limits,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(payload.encode()), TIMEOUT_S)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return Execution(False, [], [], False, "", f"quá {TIMEOUT_S} giây")
    if proc.returncode != 0:
        tail = err.decode(errors="replace").strip().splitlines()[-1:] or ["lỗi không rõ"]
        return Execution(False, [], [], False, "", tail[0][:300])
    try:
        answer = json.loads(out.decode().strip().splitlines()[-1])
    except (ValueError, IndexError):
        return Execution(False, [], [], False, "", "không đọc được kết quả")
    shape = answer.get("result") or {}
    return Execution(
        ok=answer.get("error") is None and bool(shape),
        columns=shape.get("columns", []),
        rows=shape.get("rows", []),
        truncated=bool(shape.get("truncated")),
        stdout=answer.get("stdout", ""),
        error=answer.get("error") or (None if shape else "code không gán biến result"),
    )
