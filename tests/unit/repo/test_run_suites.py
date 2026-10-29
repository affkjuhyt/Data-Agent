"""`make test-related` (tests/run_suites.py): a change runs the test files it touches, not its
suites whole, and test paths given in PYTEST_ARGS replace the suite's directory."""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

pytestmark = [pytest.mark.unit]

_spec = importlib.util.spec_from_file_location(
    "run_suites", pathlib.Path(__file__).parents[2] / "run_suites.py"
)
assert _spec is not None and _spec.loader is not None
rs = importlib.util.module_from_spec(_spec)
# @dataclass looks its module up in sys.modules while the class is made.
sys.modules["run_suites"] = rs
_spec.loader.exec_module(rs)

WORKER = rs.Suite("integration", "worker_agent", "worker-agent")
API = rs.Suite("integration", "api", "api")
FILES = {
    "tests/integration/worker_agent/test_sql.py": (
        "from services.worker_agent import questions\nfrom .test_answers import build\n"
    ),
    "tests/integration/worker_agent/test_answers.py": "from services.worker_agent import questions\n",
    "tests/integration/worker_agent/test_verified.py": (
        "from services.worker_agent.sqlgen.verified import match\n"
    ),
    "tests/integration/worker_agent/test_pilot.py": 'SEED = "v3-pilot-seed.sql"\n',
    "tests/integration/worker_agent/test_runs.py": "from services.worker_agent import runs\n",
    "tests/integration/api/test_qa_answers.py": "async def test_x(): ...\n",
    "tests/integration/api/test_qa_query.py": "async def test_y(): ...\n",
    "tests/integration/api/test_agent.py": "async def test_z(): ...\n",
}


def related(paths: list[str], suite: object) -> tuple[list[str], bool]:
    return rs.related_tests(paths, suite, FILES)


def test_a_changed_module_runs_the_tests_that_import_it() -> None:
    picked, whole = related(["backend/services/worker_agent/sqlgen/verified.py"], WORKER)
    assert picked == ["tests/integration/worker_agent/test_verified.py"] and not whole
    picked, _ = related(["backend/services/worker_agent/questions.py"], WORKER)
    assert picked == [
        "tests/integration/worker_agent/test_answers.py",
        "tests/integration/worker_agent/test_sql.py",
    ]


def test_an_api_module_runs_the_test_files_named_after_it() -> None:
    picked, whole = related(["backend/services/api/modules/qa/routes.py"], API)
    assert (
        picked
        == [
            "tests/integration/api/test_qa_answers.py",
            "tests/integration/api/test_qa_query.py",
        ]
        and not whole
    )
    assert related(["backend/services/api/modules/qa/routes.py"], WORKER) == ([], False)


def test_a_changed_test_runs_with_the_tests_that_borrow_from_it() -> None:
    picked, _ = related(["tests/integration/worker_agent/test_answers.py"], WORKER)
    assert picked == [
        "tests/integration/worker_agent/test_answers.py",
        "tests/integration/worker_agent/test_sql.py",
    ]


def test_a_file_a_test_reads_runs_that_test_in_any_suite() -> None:
    picked, whole = related(["deploy/docker/sql/v3-pilot-seed.sql"], WORKER)
    assert picked == ["tests/integration/worker_agent/test_pilot.py"] and not whole


def test_what_no_import_reaches_runs_the_suite_whole() -> None:
    assert related(["backend/alembic/sql/dataagent.sql"], WORKER)[1] is True
    assert related(["tests/integration/worker_agent/conftest.py"], WORKER)[1] is True
    assert related(["tests/integration/_db.py"], API)[1] is True
    assert related(["docs/v3/04-backlog.md"], WORKER) == ([], False)


def test_test_paths_in_pytest_args_go_to_their_own_suite() -> None:
    paths, flags = rs.split_targets(
        "-x tests/integration/api/test_qa_answers.py tests/integration/worker_agent/test_sql.py"
    )
    assert flags == ["-x"]
    assert rs.own_targets(API, paths) == ["tests/integration/api/test_qa_answers.py"]
    assert rs.own_targets(WORKER, paths) == ["tests/integration/worker_agent/test_sql.py"]
    assert rs.own_targets(rs.Suite("unit", "api", "api"), paths) == []
