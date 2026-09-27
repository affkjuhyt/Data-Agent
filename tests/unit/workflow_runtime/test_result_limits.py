"""Row / byte / cell / time bounds on a SQL result.

`MAX_ROWS = 10_000` was the only bound, and a row count does not bound memory.
Ten thousand rows of a wide join is hundreds of megabytes, and every stage
downstream keeps its own copy — the driver, the backend's dicts, the job store's
600s TTL, the response, agent state. The row cap bounded the SHAPE of the
answer and never its size.

What these hold to:

  * **Bytes are bounded, not just rows.** The dimension that was missing.
  * **A cut is never silent.** Every path that stops early sets a reason, and
    the reason reaches the answer. A SUM over "as many rows as fitted" is a
    wrong number that looks exactly like the right one — strictly worse than an
    error, which at least announces itself.
  * **One row always comes back.** Returning zero rows and "byte_limit" tells
    the caller nothing about what they asked for.
  * **Time is bounded too.** A query costing an hour and returning three rows
    passes every result-size check there is; only the engine's own timeout sees
    it.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "sandbox_executor")

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


def _wide_row(size_chars: int, key: str = "blob") -> dict:
    return {key: "x" * size_chars}


class TestRowBound:
    def test_rows_past_the_cap_are_dropped_and_reported(self):
        from result_limits import ROW_LIMIT, cap_rows

        result = cap_rows(({"n": i} for i in range(50)), max_rows=10)

        assert result.row_count == 10
        assert result.is_truncated is True
        assert result.reason == ROW_LIMIT

    def test_a_result_inside_every_bound_is_not_marked_truncated(self):
        from result_limits import cap_rows

        result = cap_rows([{"n": i} for i in range(5)], max_rows=10)

        assert result.row_count == 5
        assert result.is_truncated is False
        assert result.reason == ""


class TestByteBound:
    def test_a_wide_result_is_cut_on_bytes_while_under_the_row_cap(self):
        """The gap this module exists to close: 10 rows is nowhere near
        MAX_ROWS, and is still 10MB."""
        from result_limits import BYTE_LIMIT, cap_rows

        rows = [_wide_row(1000) for _ in range(100)]
        result = cap_rows(rows, max_rows=10_000, max_bytes=5_000, max_cell_chars=100_000)

        assert result.row_count < 100
        assert result.is_truncated is True
        assert result.reason == BYTE_LIMIT
        assert result.result_bytes <= 5_000 + 1100

    def test_the_first_row_is_kept_however_large(self):
        """Zero rows plus "byte_limit" tells the caller nothing about what they
        asked for; one over-budget row plus the flag does."""
        from result_limits import cap_rows

        result = cap_rows([_wide_row(50_000)], max_bytes=100, max_cell_chars=100_000)

        assert result.row_count == 1
        assert result.is_truncated is False  # nothing was dropped — there was nothing after it

    def test_a_huge_first_row_still_stops_the_second(self):
        from result_limits import BYTE_LIMIT, cap_rows

        result = cap_rows([_wide_row(50_000), _wide_row(10)], max_bytes=100, max_cell_chars=100_000)

        assert result.row_count == 1
        assert result.reason == BYTE_LIMIT


class TestCellBound:
    def test_an_oversized_value_is_shortened_and_counted(self):
        """A row cap and a byte cap together still let one 200MB document
        through as "a single small row"."""
        from result_limits import cap_rows

        result = cap_rows([_wide_row(10_000)], max_cell_chars=100)

        assert len(result.rows[0]["blob"]) < 200
        assert result.rows[0]["blob"].endswith("[truncated]")
        assert result.cells_truncated == 1

    def test_values_within_the_cap_are_untouched(self):
        from result_limits import cap_rows

        result = cap_rows([{"a": "short", "n": 42, "z": None}], max_cell_chars=100)

        assert result.rows[0] == {"a": "short", "n": 42, "z": None}
        assert result.cells_truncated == 0

    def test_bytes_values_are_capped_too(self):
        from result_limits import cap_rows

        result = cap_rows([{"b": b"y" * 5000}], max_cell_chars=10)

        assert len(result.rows[0]["b"]) == 10
        assert result.cells_truncated == 1


class TestAccumulatorContract:
    def test_false_is_returned_only_when_a_row_is_rejected(self):
        """The invariant that keeps "we stopped early" and "the result says it
        is truncated" from coming apart. An earlier version returned False after
        ADMITTING the row that reached the budget, which stopped the read with
        `reason` still empty and reported a silently-cut result as complete."""
        from result_limits import RowAccumulator

        accumulator = RowAccumulator(max_rows=100, max_bytes=200, max_cell_chars=1000)

        accepted = 0
        rejected_at = None
        for i in range(100):
            if accumulator.add({"n": i, "pad": "x" * 20}):
                accepted += 1
            else:
                rejected_at = i
                break

        assert rejected_at is not None, "budget never ran out — test is not exercising the bound"
        result = accumulator.finish()
        assert result.row_count == accepted
        # The only thing that matters: stopping and reporting agree.
        assert result.is_truncated is True
        assert result.reason != ""

    def test_add_keeps_returning_false_once_full(self):
        from result_limits import RowAccumulator

        accumulator = RowAccumulator(max_rows=1)
        assert accumulator.add({"n": 1}) is True
        assert accumulator.add({"n": 2}) is False
        assert accumulator.add({"n": 3}) is False
        assert accumulator.finish().row_count == 1

    def test_an_unserialisable_value_does_not_fail_the_query(self):
        """Accounting for size must never be the thing that loses an answer
        that already ran."""
        from result_limits import cap_rows

        class Opaque:
            def __repr__(self) -> str:
                return "<opaque>"

        result = cap_rows([{"v": Opaque()}])
        assert result.row_count == 1


class TestReportedShape:
    def test_as_dict_carries_the_reason_not_just_the_flag(self):
        """"the first 10,000 rows" and "as much as fitted in 8MB" support
        different answers; a caller that cannot tell them apart cannot explain
        either one."""
        from result_limits import cap_rows

        payload = cap_rows([{"n": i} for i in range(20)], max_rows=5).as_dict()

        assert payload["row_count"] == 5
        assert payload["is_truncated"] is True
        assert payload["truncation_reason"] == "row_limit"
        assert set(payload) == {
            "rows", "row_count", "is_truncated", "truncation_reason",
            "cells_truncated", "result_bytes",
        }

    def test_a_complete_result_reports_an_empty_reason(self):
        from result_limits import cap_rows

        payload = cap_rows([{"n": 1}]).as_dict()
        assert payload["is_truncated"] is False
        assert payload["truncation_reason"] == ""


class TestConfiguration:
    @pytest.mark.parametrize("bad", ["", "abc", "0", "-5"])
    def test_an_unusable_env_override_falls_back_to_the_default(self, bad, monkeypatch):
        """A cap misconfigured to 0 or nonsense must not become "no cap" — that
        turns a typo into the unbounded behaviour this replaces."""
        monkeypatch.setenv("SQL_MAX_ROWS", bad)
        import importlib

        import result_limits
        reloaded = importlib.reload(result_limits)
        try:
            assert reloaded.MAX_ROWS == 10_000
        finally:
            monkeypatch.delenv("SQL_MAX_ROWS", raising=False)
            importlib.reload(result_limits)

    def test_the_defaults_are_bounded_in_every_dimension(self):
        import result_limits

        assert 0 < result_limits.MAX_ROWS <= 100_000
        assert 0 < result_limits.MAX_RESULT_BYTES <= 64 * 1024 * 1024
        assert 0 < result_limits.MAX_CELL_CHARS <= 1024 * 1024
        assert 0 < result_limits.QUERY_TIMEOUT_S <= 300


class TestEveryBackendIsBounded:
    """The finding was that only one backend had any of this. A backend that
    quietly drops a bound is the version hardest to notice, so each is checked
    by reading its source rather than by trusting the constant it exports."""

    def _tree(self, filename):
        import ast
        return ast.parse((pathlib.Path(_SVC_DIR) / filename).read_text(encoding="utf-8"))

    @pytest.mark.parametrize("filename", [
        "postgres_readonly.py", "clickhouse_readonly.py", "snowflake_readonly.py",
    ])
    def test_each_backend_applies_the_shared_bounds(self, filename):
        import ast

        tree = self._tree(filename)
        names = {
            node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name) and node.value.id == "result_limits"
        }
        # Either it streams through the accumulator or it caps the finished
        # result; both routes end at the same bounds.
        assert names & {"RowAccumulator", "cap_rows"}, f"{filename} applies no result bound"

    @pytest.mark.parametrize("filename,marker", [
        ("postgres_readonly.py", "statement_timeout"),
        ("clickhouse_readonly.py", "max_execution_time"),
        ("snowflake_readonly.py", "STATEMENT_TIMEOUT_IN_SECONDS"),
    ])
    def test_each_backend_sets_a_server_side_time_limit(self, filename, marker):
        """No result-size bound sees a query that costs an hour and returns
        three rows. Snowflake bills for it by the second."""
        source = (pathlib.Path(_SVC_DIR) / filename).read_text(encoding="utf-8")
        assert marker in source

    def test_postgres_streams_rather_than_materialising(self):
        """conn.fetch() allocates the whole result before any cap can apply, so
        the cap would bound the payload but not the peak. This is the only
        backend where a cursor can bound peak memory — the other two are handed
        a finished result by their driver."""
        import ast

        tree = self._tree("postgres_readonly.py")
        calls = {
            node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert "cursor" in calls
        assert "fetch" in calls

    def test_clickhouse_asks_the_server_for_a_memory_ceiling(self):
        """The only place a runaway join can be stopped before it is computed
        rather than after."""
        source = (pathlib.Path(_SVC_DIR) / "clickhouse_readonly.py").read_text(encoding="utf-8")
        assert "max_memory_usage" in source
        assert "max_result_bytes" in source


class TestTruncationReachesTheAnswer:
    def test_the_dispatcher_returns_the_reason(self):
        import ast

        tree = ast.parse((pathlib.Path(_SVC_DIR) / "main.py").read_text(encoding="utf-8"))
        keys = {
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert {"truncation_reason", "cells_truncated", "result_bytes"} <= keys


class TestResponderDisclosesTheCut:
    """A cap that reaches the model unlabelled is worse than the unbounded
    behaviour it replaces: the answer is wrong and nothing about it looks wrong.
    """

    def _responder(self, monkeypatch):
        runtime = str(REPO_ROOT / "backend" / "services" / "workflow_runtime")
        monkeypatch.syspath_prepend(runtime)
        for name in ("observability", "state"):
            module = sys.modules.get(name)
            if module is not None and getattr(module, "__file__", None) is None:
                monkeypatch.delitem(sys.modules, name, raising=False)
        import nodes.model_harness.responder as responder
        return responder

    def test_a_row_capped_result_is_labelled_with_its_row_count(self, monkeypatch):
        responder = self._responder(monkeypatch)
        note = responder._truncation_note(
            {"reason": "row_limit", "rows_returned": 10_000}
        )
        assert "first 10000 row(s)" in note
        assert note.startswith("result-truncated:")

    def test_a_byte_capped_result_says_so_differently(self, monkeypatch):
        """The two reasons support different follow-ups — narrow the rows vs
        narrow the columns — so they must not render identically."""
        responder = self._responder(monkeypatch)
        row = responder._truncation_note({"reason": "row_limit", "rows_returned": 5})
        byte = responder._truncation_note({"reason": "byte_limit", "rows_returned": 5})
        assert row != byte
        assert "size cap" in byte

    def test_shortened_cells_are_mentioned_alongside_a_row_cut(self, monkeypatch):
        responder = self._responder(monkeypatch)
        note = responder._truncation_note(
            {"reason": "row_limit", "rows_returned": 10, "cells_truncated": 3}
        )
        assert "3 oversized value(s)" in note

    @pytest.mark.parametrize("value", [None, {}, "row_limit", 7, {"reason": ""}])
    def test_a_complete_result_renders_nothing(self, monkeypatch, value):
        responder = self._responder(monkeypatch)
        assert responder._truncation_note(value) == ""

    def test_the_rule_forbids_aggregating_over_a_partial_slice(self, monkeypatch):
        """The specific failure mode: SUM over the rows that fitted is a number
        that looks exactly like the real total."""
        responder = self._responder(monkeypatch)
        rule = responder._TRUNCATION_RULE
        assert "partial slice" in rule
        assert "totals" in rule
        assert rule.startswith("\n\n")
