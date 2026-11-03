"""services/ml_forecast and libs/forecast_fit.client: a series goes over HTTP and comes back as the
FitResult `fit` would have given in-process, and a service that fails is not mistaken for one that
refused the series."""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import json
import math
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from fastapi.testclient import TestClient

from libs.forecast_fit import Point, client
from libs.forecast_fit.client import TrainerUnavailable
from services.ml_forecast.main import app

pytestmark = pytest.mark.unit


def _months(n: int) -> list[dt.date]:
    return [dt.date(2020 + i // 12, i % 12 + 1, 1) for i in range(n)]


def _seasonal(n: int) -> list[Point]:
    rng = random.Random(7)
    return [
        Point(p, 100 + 2.0 * i + 15 * math.sin(2 * math.pi * i / 12) + rng.gauss(0, 3))
        for i, p in enumerate(_months(n))
    ]


def _body(series: list[Point], horizon: int = 6) -> dict:
    return {
        "series": [{"period": p.period.isoformat(), "value": p.value} for p in series],
        "frequency": "month",
        "horizon": horizon,
    }


class _Stub:
    """A server that answers every POST the same way (or sleeps first)."""

    def __init__(self, status: int = 200, body: bytes = b"{}", sleep: float = 0.0):
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                time.sleep(sleep)
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(stub.body)))
                    self.end_headers()
                    self.wfile.write(stub.body)
                except OSError:
                    pass  # the client gave up first

            def log_message(self, *args) -> None:
                pass

        self.body = body
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self) -> _Stub:
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()


def test_the_service_answers_a_seasonal_series():
    with TestClient(app) as http:
        response = http.post("/fit", json=_body(_seasonal(48)))

    assert response.status_code == 200
    answer = response.json()
    assert answer["chosen"] in {"ets", "theta", "arima"}
    assert len(answer["predictions"]) == 6
    assert answer["predictions"][0]["period"] == "2024-01-01"
    assert {c["algorithm"] for c in answer["candidates"]} >= {"ets", "seasonal_naive"}


def test_the_service_refuses_a_malformed_body():
    with TestClient(app) as http:
        no_series = http.post("/fit", json={"frequency": "month", "horizon": 6})
        bad_frequency = http.post("/fit", json={**_body(_seasonal(48)), "frequency": "day"})
        bad_value = http.post(
            "/fit", json={**_body([]), "series": [{"period": "2020-01-01", "value": "x"}]}
        )

    assert no_series.status_code == bad_frequency.status_code == bad_value.status_code == 422


def test_the_service_gives_the_reason_a_series_is_refused():
    with TestClient(app) as http:
        response = http.post("/fit", json=_body(_seasonal(10)))

    assert response.status_code == 422
    assert response.json()["detail"] == "Cần ít nhất 24 kỳ để dự báo, chuỗi này có 10 kỳ."


def test_the_service_reports_healthy():
    with TestClient(app) as http:
        assert http.get("/healthz").json() == {"ok": True}


def test_the_client_returns_what_the_service_computed():
    from libs.forecast_fit import fit

    series = _seasonal(48)
    expected = fit(series, frequency="month", horizon=6)
    answer = json.dumps(
        json.loads(TestClient(app).post("/fit", json=_body(series)).content)
    ).encode()

    with _Stub(body=answer) as stub:
        got = asyncio.run(client.fit_remote(stub.url, series, frequency="month", horizon=6))

    assert dataclasses.asdict(got) == dataclasses.asdict(expected)


def test_a_malformed_answer_from_the_service_is_refused():
    wrong_shapes = [
        b"not json",
        b"[]",
        json.dumps({"chosen": "ets"}).encode(),
        json.dumps(
            {
                "chosen": None,
                "reason": None,
                "candidates": "many",
                "cutoffs": [],
                "predictions": [],
                "interval_method": None,
                "params": {},
            }
        ).encode(),
        json.dumps(
            {
                "chosen": None,
                "reason": None,
                "candidates": [],
                "cutoffs": ["soon"],
                "predictions": [],
                "interval_method": None,
                "params": {},
            }
        ).encode(),
    ]
    for body in wrong_shapes:
        with _Stub(body=body) as stub, pytest.raises(TrainerUnavailable) as caught:
            asyncio.run(client.fit_remote(stub.url, _seasonal(48), frequency="month", horizon=6))
        assert str(caught.value) == "Dịch vụ dự báo không phản hồi"


def _valid_answer(**changes) -> dict:
    answer = {
        "chosen": None,
        "reason": "x",
        "candidates": [],
        "cutoffs": [],
        "predictions": [],
        "interval_method": None,
        "params": {},
    }
    return {**answer, **changes}


def _remote(url: str):
    return asyncio.run(client.fit_remote(url, _seasonal(48), frequency="month", horizon=6))


def test_a_valid_answer_parses():
    with _Stub(body=json.dumps(_valid_answer()).encode()) as stub:
        assert _remote(stub.url).reason == "x"


def test_an_oversized_answer_is_refused(monkeypatch):
    monkeypatch.setattr(client, "MAX_RESPONSE_BYTES", 1000)
    padded = json.dumps(_valid_answer(reason="x" * 5000)).encode()
    with _Stub(body=padded) as stub, pytest.raises(TrainerUnavailable) as caught:
        _remote(stub.url)
    # The cap fired, not the parser: the same body is accepted under a larger cap.
    assert caught.value.__cause__ is None
    monkeypatch.setattr(client, "MAX_RESPONSE_BYTES", 100_000)
    with _Stub(body=padded) as stub:
        assert len(_remote(stub.url).reason) == 5000


def test_the_service_timeout_raises_trainer_unavailable(monkeypatch):
    monkeypatch.setattr(client, "REQUEST_TIMEOUT_S", 0.2)
    with (
        _Stub(body=json.dumps(_valid_answer()).encode(), sleep=1.0) as stub,
        pytest.raises(TrainerUnavailable) as caught,
    ):
        _remote(stub.url)
    assert str(caught.value) == "Dịch vụ dự báo không phản hồi"
    assert isinstance(caught.value.__cause__, httpx.TimeoutException)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_a_non_finite_number_in_the_answer_is_refused(constant):
    body = json.dumps(_valid_answer()).replace('"reason": "x"', '"reason": 1')
    body = body.replace('"params": {}', '"params": {"k": ' + constant + "}")
    with _Stub(body=body.encode()) as stub, pytest.raises(TrainerUnavailable):
        _remote(stub.url)


def test_non_finite_constants_are_refused_even_where_a_float_is_optional():
    answer = json.dumps(_valid_answer(candidates=[1])).replace("[1]", "[]")
    answer = answer.replace('"reason": "x"', '"reason": null, "extra": NaN')
    with _Stub(body=answer.encode()) as stub, pytest.raises(TrainerUnavailable):
        _remote(stub.url)


def _nested(depth: int):
    value: object = 1
    for _ in range(depth):
        value = {"a": value}
    return value


@pytest.mark.parametrize(
    "params",
    [
        {"season_length": 12, "alpha": 0.5, "model": "AAA", "ok": True, "n": None},
        {"a": {"b": {"c": [1, 2]}}},
    ],
)
def test_params_of_finite_scalars_and_shallow_structures_pass(params):
    with _Stub(body=json.dumps(_valid_answer(params=params)).encode()) as stub:
        assert _remote(stub.url).params == params


@pytest.mark.parametrize(
    "params",
    [_nested(5), {f"k{i}": 1 for i in range(250)}],
    ids=["too deep", "too many keys"],
)
def test_params_out_of_bounds_are_refused(params):
    with (
        _Stub(body=json.dumps(_valid_answer(params=params)).encode()) as stub,
        pytest.raises(TrainerUnavailable),
    ):
        _remote(stub.url)


def test_a_server_error_is_unavailable_not_a_refused_series():
    with _Stub(status=500, body=b'{"detail": "boom"}') as stub, pytest.raises(TrainerUnavailable):
        asyncio.run(client.fit_remote(stub.url, _seasonal(48), frequency="month", horizon=6))


def test_a_refused_series_is_the_same_value_error_as_in_process():
    refusal = json.dumps({"detail": "Chuỗi 10 kỳ quá ngắn"}).encode()
    with _Stub(status=422, body=refusal) as stub, pytest.raises(ValueError) as caught:
        asyncio.run(client.fit_remote(stub.url, _seasonal(10), frequency="month", horizon=6))
    assert not isinstance(caught.value, TrainerUnavailable)
    assert str(caught.value) == "Chuỗi 10 kỳ quá ngắn"


def test_nothing_listening_is_unavailable():
    with pytest.raises(TrainerUnavailable):
        asyncio.run(
            client.fit_remote("http://127.0.0.1:9", _seasonal(48), frequency="month", horizon=6)
        )


def test_fit_series_uses_the_service_when_the_url_is_set(monkeypatch):
    from libs.forecast_fit import service

    seen = {}

    async def remote(url, series, *, frequency, horizon):
        seen.update(url=url, horizon=horizon)
        return "remote"

    monkeypatch.setenv("FORECAST_SERVICE_URL", "http://ml-forecast:8150")
    monkeypatch.setattr(service, "fit_remote", remote)

    assert asyncio.run(service.fit_series(_seasonal(48), frequency="month", horizon=3)) == "remote"
    assert seen == {"url": "http://ml-forecast:8150", "horizon": 3}


def test_fit_series_fits_in_process_without_a_url(monkeypatch):
    from libs.forecast_fit import service

    monkeypatch.delenv("FORECAST_SERVICE_URL", raising=False)
    result = asyncio.run(service.fit_series(_seasonal(48), frequency="month", horizon=6))
    assert len(result.predictions) == 6


def test_fit_series_without_a_url_and_without_statsforecast_is_unavailable(monkeypatch):
    import builtins

    from libs.forecast_fit import service

    real = builtins.__import__

    def refuse(name, globals=None, locals=None, fromlist=(), level=0):
        if (
            level == 1
            and name == "backtest"
            and globals
            and globals.get("__name__") == service.__name__
        ):
            raise ImportError("No module named 'statsforecast'")
        return real(name, globals, locals, fromlist, level)

    monkeypatch.delenv("FORECAST_SERVICE_URL", raising=False)
    monkeypatch.setattr(builtins, "__import__", refuse)
    with pytest.raises(TrainerUnavailable):
        asyncio.run(service.fit_series(_seasonal(48), frequency="month", horizon=6))


def test_the_client_imports_without_statsforecast():
    import subprocess
    import sys

    code = (
        "import sys; sys.modules['statsforecast'] = None\n"
        "from libs.forecast_fit import client, service, Point\n"
        "assert 'libs.forecast_fit.backtest' not in sys.modules"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_the_package_fit_is_the_function_even_after_the_submodule_is_imported():
    import subprocess
    import sys

    code = (
        "import libs.forecast_fit.backtest\n"
        "import libs.forecast_fit.service\n"
        "from libs.forecast_fit import fit\n"
        "assert callable(fit), fit"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
