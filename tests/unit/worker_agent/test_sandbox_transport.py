"""AL-04: datasets go to the python sandbox, and its result comes back, as Arrow IPC in a frame
(libs/snapshot/ipc.py, libs/code_sandbox/wire.py); the JSON body stays behind a flag."""

from __future__ import annotations

import datetime
import decimal
import json
import random
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from typing import Any

import pytest

from libs.code_sandbox import client, wire
from libs.snapshot import ipc
from services.python_sandbox import main as sandbox

pytestmark = [pytest.mark.unit, pytest.mark.req("AL-04")]


def _sales(n: int) -> tuple[list[str], list[list[Any]]]:
    rng = random.Random(1)
    regions = ["Bắc", "Trung", "Nam", "Tây Nguyên"]
    products = [f"SP{i:03d}" for i in range(200)]
    rows = [
        [
            datetime.date(2013, 1, 1) + datetime.timedelta(days=i % 365),
            rng.choice(regions),
            rng.choice(products),
            decimal.Decimal(f"{rng.uniform(10, 99999):.4f}"),
            rng.randint(1, 500),
        ]
        for i in range(n)
    ]
    return ["Ngày", "Vùng", "Sản phẩm", "Doanh thu", "Số đơn"], rows


def test_the_arrow_payload_is_at_most_a_fifth_of_the_json(monkeypatch: pytest.MonkeyPatch) -> None:
    columns, rows = _sales(10_000)
    datasets = {"d1": {"columns": columns, "rows": rows}}
    arrow, kind = client.request_body("result = d1", datasets)
    assert kind == wire.CONTENT_TYPE
    monkeypatch.setenv("AGENT_SANDBOX_TRANSPORT", "json")
    as_json, kind = client.request_body("result = d1", datasets)
    assert kind == "application/json"
    assert len(arrow) <= 0.2 * len(as_json), (len(arrow), len(as_json))


def test_a_stream_keeps_values_as_the_code_saw_them() -> None:
    columns = ["ngay", "vung", "so_tien", "dem", "lan", "rong"]
    rows = [
        [datetime.date(2013, 1, 2), "Bắc", decimal.Decimal("1.50"), 3, 1, None],
        [datetime.date(2013, 1, 3), None, decimal.Decimal("2.25"), None, "x", None],
    ]
    got_columns, got_rows = ipc.decode(ipc.encode(columns, rows))
    assert got_columns == columns
    # A decimal as a float, a date as its ISO text (as JSON gave them); a column mixing an int and
    # a string goes as text; nulls stay nulls.
    assert got_rows == [
        ["2013-01-02", "Bắc", 1.5, 3, "1", None],
        ["2013-01-03", None, 2.25, None, "x", None],
    ]


def test_a_bad_stream_or_one_past_the_limits_is_refused() -> None:
    with pytest.raises(ipc.IpcInvalid):
        ipc.decode(b"not arrow")
    stream = ipc.encode(["a"], [[1], [2], [3]])
    with pytest.raises(ipc.IpcInvalid):
        ipc.decode(stream, max_rows=2)
    with pytest.raises(ipc.IpcInvalid):
        ipc.decode(ipc.encode(["a", "b"], [[1, 2]]), max_columns=1)


def test_a_frame_holds_its_parts_and_refuses_one_that_does_not_add_up() -> None:
    body = wire.pack({"code": "x"}, [b"ab", b"", b"cde"])
    header, parts = wire.unpack(body)
    assert header["code"] == "x" and parts == [b"ab", b"", b"cde"]
    for bad in (b"", b"XXXX" + body[4:], body[:-1], body + b"z"):
        with pytest.raises(wire.FrameInvalid):
            wire.unpack(bad)


@pytest.fixture
def service() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), sandbox.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()


CODE = "result = d1.groupby('Vùng', as_index=False)['Doanh thu'].sum().sort_values('Vùng')"


@pytest.mark.parametrize("transport", ["arrow", "json"])
async def test_the_sandbox_runs_the_same_over_either_transport(
    service: str, transport: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_SANDBOX_TRANSPORT", transport)
    columns, rows = _sales(500)
    # The workspace's rows as the analysis tool sends them (figures are plain numbers there).
    plain = [[str(r[0]), r[1], float(r[3]), r[4], r[2]] for r in rows]
    names = ["Ngày", "Vùng", "Doanh thu", "Số đơn", "Sản phẩm"]
    done = await client.run(service, CODE, {"d1": {"columns": names, "rows": plain}})
    assert done.ok, done.error
    assert done.columns == ["Vùng", "Doanh thu"]
    expected: dict[str, float] = {}
    for r in plain:
        expected[str(r[1])] = expected.get(str(r[1]), 0.0) + float(r[2])
    assert {r[0]: round(r[1], 4) for r in done.rows} == {
        k: round(v, 4) for k, v in expected.items()
    }


async def test_a_refused_code_and_a_bad_reply_are_failures(
    service: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    with pytest.raises(Exception):  # noqa: B017 - the client's own static check refuses first
        await client.run(service, "import os\nresult = os.getcwd()", {})
    # A framed reply whose part is not Arrow is not read.
    reply = wire.pack({"ok": True, "stdout": "", "error": None, "truncated": False}, [b"junk"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=reply, headers={"Content-Type": wire.CONTENT_TYPE})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        client.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    done = await client.run(
        "http://sandbox", "result = 1", {"d1": {"columns": ["a"], "rows": [[1]]}}
    )
    assert not done.ok and done.error == "sandbox trả kết quả sai dạng"
    assert json.dumps(done.rows) == "[]"
