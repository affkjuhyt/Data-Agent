"""Every module a migration imports is in the migrate image.

The image copies only what the migrations need (backend/alembic/Dockerfile). A
migration importing libs.shared.tenant_rls (a2c4e6f8b0d1) passed every test,
which run alembic in a service image that has all of libs, and then failed the
migrate Job on the cluster with ModuleNotFoundError.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
BACKEND = REPO_ROOT / "backend"
DOCKERFILE = BACKEND / "alembic" / "Dockerfile"


def _copied_sources() -> list[str]:
    """backend/... paths the Dockerfile COPYs (files or directories)."""
    sources: list[str] = []
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("COPY "):
            sources += [p for p in line.split()[1:-1] if p.startswith("backend/")]
    return sources


def _imported_lib_modules() -> set[str]:
    modules: set[str] = set()
    for path in (BACKEND / "alembic").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("libs."):
                modules.add(node.module)
            elif isinstance(node, ast.Import):
                modules |= {a.name for a in node.names if a.name.startswith("libs.")}
    return modules


def _module_file(module: str) -> str:
    rel = "backend/" + module.replace(".", "/")
    return rel + "/__init__.py" if (REPO_ROOT / rel).is_dir() else rel + ".py"


def test_every_libs_module_a_migration_imports_is_copied_into_the_image() -> None:
    copied = _copied_sources()
    missing = sorted(
        module
        for module in _imported_lib_modules()
        if not any(
            _module_file(module) == src or _module_file(module).startswith(src.rstrip("/") + "/")
            for src in copied
        )
    )
    assert not missing, f"migrations import modules the migrate image does not copy: {missing}"


def test_the_helper_dependencies_are_copied_too() -> None:
    """tenant_rls imports sql_identifiers; a copied module's own libs imports count."""
    copied = _copied_sources()
    for src in [s for s in copied if s.endswith(".py")]:
        tree = ast.parse((REPO_ROOT / src).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and re.match(r"libs\.", node.module)
            ):
                assert _module_file(node.module) in copied or any(
                    _module_file(node.module).startswith(s.rstrip("/") + "/") for s in copied
                ), f"{src} imports {node.module}, which the image does not copy"
