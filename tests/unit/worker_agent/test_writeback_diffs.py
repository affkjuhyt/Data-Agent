"""A writeback's preview diff (UC-06-04 step 2): rows matched by the target's key, counted as
inserts, updates and deletes, with a sample that never holds a personal-data column (BR-113)."""

from __future__ import annotations

import pytest

from services.worker_agent.writeback_diffs import SAMPLE_ROWS, PreviewFailed, compare

pytestmark = [pytest.mark.unit]

COLUMNS = ["id", "region", "balance", "phone"]


def _row(id_: object, region: str, balance: object, phone: str = "0900") -> dict[str, object]:
    return {"id": id_, "region": region, "balance": balance, "phone": phone}


def test_counts_inserts_updates_deletes_by_key() -> None:
    old = [_row(1, "HN", "100.00"), _row(2, "SG", "200.00"), _row(3, "DN", "300.00")]
    # 1 unchanged (1500.00 against 1500 is the same number), 2 updated, 3 gone, 4 new.
    new = [_row("1", "HN", "100"), _row(2, "SG", "250.00", "0911"), _row(4, "HP", "50")]
    diff = compare(["id"], COLUMNS, new, old, numeric={"id", "balance"}, hidden={"phone"})
    assert (diff.inserts, diff.updates, diff.deletes, diff.unchanged) == (1, 1, 1, 1)
    assert diff.sample["inserts"] == [
        {"key": {"id": 4}, "after": {"id": 4, "region": "HP", "balance": "50"}}
    ]
    # Only the columns that changed, and never the hidden one even though it changed too.
    assert diff.sample["updates"] == [
        {"key": {"id": 2}, "before": {"balance": "200.00"}, "after": {"balance": "250.00"}}
    ]
    assert diff.sample["deletes"] == [
        {"key": {"id": 3}, "before": {"id": 3, "region": "DN", "balance": "300.00"}}
    ]


def test_text_keys_are_not_read_as_numbers() -> None:
    diff = compare(
        ["code"], ["code"], [{"code": "001"}], [{"code": "1"}], numeric=set(), hidden=set()
    )
    assert (diff.inserts, diff.deletes) == (1, 1)


def test_a_hidden_key_is_left_out_of_the_sample() -> None:
    diff = compare(
        ["phone"],
        ["phone", "region"],
        [{"phone": "0900", "region": "HN"}],
        [],
        numeric=set(),
        hidden={"phone"},
    )
    assert diff.sample["inserts"] == [{"key": {}, "after": {"region": "HN"}}]


def test_the_sample_is_bounded() -> None:
    new = [_row(i, "HN", i) for i in range(SAMPLE_ROWS * 3)]
    diff = compare(["id"], COLUMNS, new, [], numeric={"id"}, hidden=set())
    assert diff.inserts == SAMPLE_ROWS * 3 and len(diff.sample["inserts"]) == SAMPLE_ROWS


@pytest.mark.parametrize(
    ("new", "old", "says"),
    [
        ([_row(1, "HN", 1), _row("1", "SG", 2)], [], "dẫn xuất bị trùng"),
        ([], [_row(1, "HN", 1), _row(1, "SG", 2)], "đích bị trùng"),
        ([_row(None, "HN", 1)], [], "thiếu giá trị khoá"),
    ],
)
def test_a_key_that_does_not_name_one_row_fails(
    new: list[dict[str, object]], old: list[dict[str, object]], says: str
) -> None:
    with pytest.raises(PreviewFailed, match=says):
        compare(["id"], COLUMNS, new, old, numeric={"id"}, hidden=set())
