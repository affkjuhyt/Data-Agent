"""The REST connector's try, worker side (UC-04-01): one GET to a public https address, pinned to
the address that was checked, with the key read from OpenBao, no redirect followed and the answer
capped; the fields of the first record become the suggested mapping."""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest

from services.worker_agent import questions, rest_tries
from services.worker_agent.query.secrets import StaticReader

pytestmark = [pytest.mark.unit]

ORG = uuid.uuid4()
REF = f"secret/tenants/{ORG}/rest/cards"
PUBLIC = "93.184.216.34"


def job(**request: Any) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "org_id": str(ORG),
        "data_space_id": str(uuid.uuid4()),
        "requested_by": str(uuid.uuid4()),
        "request": {
            "url": "https://api.the-bank.vn/v1/cards",
            "query": "page=1&updated_since={{last_run}}",
            "auth": "bearer",
            "secret_ref": REF,
            "headers": [{"name": "Accept", "value": "application/json"}],
            "records_path": None,
            **request,
        },
    }


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Public DNS for the test host names, the key in a static OpenBao, and the API's answers
    given per path."""
    seen: list[httpx.Request] = []

    async def resolve(host: str, port: int) -> list[str]:
        return {
            "api.the-bank.vn": [PUBLIC],
            "auth.the-bank.vn": [PUBLIC],
            "inside.the-bank.vn": ["10.0.0.7"],
            "mixed.the-bank.vn": [PUBLIC, "127.0.0.1"],
        }.get(host, [])

    monkeypatch.setattr(rest_tries, "_resolve", resolve)
    monkeypatch.setattr(
        questions,
        "_secrets",
        StaticReader(
            {
                REF: {"token": "tok-123"},
                f"secret/tenants/{ORG}/rest/key": {"api_key": "key-9", "header": "X-Bank-Key"},
                f"secret/tenants/{ORG}/rest/oauth": {
                    "user": "client-1",
                    "password": "shh",
                    "token_url": "https://auth.the-bank.vn/oauth/token",
                },
            }
        ),
    )

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "from-oauth", "token_type": "Bearer"})
        if request.url.path == "/moved":
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})
        if request.url.path == "/big":
            return httpx.Response(200, content=b"x" * (rest_tries.MAX_BYTES * 2))
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "card_id": "C-1",
                        "limit": 50_000_000,
                        "active": True,
                        "opened_on": "2026-01-02",
                        "owner": {"name": "Nguyễn Văn A"},
                    }
                ],
                "next": None,
            },
        )

    monkeypatch.setattr(rest_tries, "_transport", lambda: httpx.MockTransport(answer))
    return seen


async def test_the_call_is_pinned_to_the_checked_address_with_the_key(
    world: list[httpx.Request],
) -> None:
    result = await rest_tries.try_request(job())
    ok = result["ok"]
    assert ok["status"] == 200 and isinstance(ok["duration_ms"], int)
    [sent] = world
    # Connected to the address that was checked; TLS and Host still name the host.
    assert sent.url.host == PUBLIC and sent.headers["Host"] == "api.the-bank.vn"
    assert sent.extensions["sni_hostname"] == "api.the-bank.vn"
    assert sent.method == "GET" and sent.url.path == "/v1/cards"
    assert sent.url.params["page"] == "1" and "{{" not in str(sent.url)
    assert sent.headers["Authorization"] == "Bearer tok-123"
    assert sent.headers["Accept"] == "application/json"
    assert json.loads(ok["body"])["data"][0]["card_id"] == "C-1"
    assert ok["mapping"] == [
        {"path": "data[].card_id", "column": "card_id", "type": "text", "note": None},
        {"path": "data[].limit", "column": "limit", "type": "number", "note": None},
        {"path": "data[].active", "column": "active", "type": "boolean", "note": None},
        {"path": "data[].opened_on", "column": "opened_on", "type": "date", "note": None},
        {
            "path": "data[].owner",
            "column": "owner",
            "type": "text",
            "note": "Giá trị lồng nhau, lưu dạng JSON",
        },
    ]


async def test_an_api_key_and_an_oauth_client_are_read_from_the_secret(
    world: list[httpx.Request],
) -> None:
    keyed = await rest_tries.try_request(
        job(auth="api_key", secret_ref=f"secret/tenants/{ORG}/rest/key")
    )
    assert keyed["ok"]["status"] == 200 and world[-1].headers["X-Bank-Key"] == "key-9"
    oauth = await rest_tries.try_request(
        job(auth="oauth2_client", secret_ref=f"secret/tenants/{ORG}/rest/oauth")
    )
    assert oauth["ok"]["status"] == 200
    token, call = world[-2], world[-1]
    assert token.url.host == PUBLIC and token.headers["Host"] == "auth.the-bank.vn"
    assert b"grant_type=client_credentials" in token.content
    assert call.headers["Authorization"] == "Bearer from-oauth"


async def test_a_redirect_is_reported_not_followed_and_a_big_answer_is_cut(
    world: list[httpx.Request],
) -> None:
    moved = await rest_tries.try_request(job(url="https://api.the-bank.vn/moved"))
    assert moved["ok"]["status"] == 302 and len(world) == 1
    big = await rest_tries.try_request(job(url="https://api.the-bank.vn/big"))
    assert big["ok"]["status"] == 200
    assert len(big["ok"]["body"]) <= rest_tries.PREVIEW_CHARS + 200
    assert "256 KB" in big["ok"]["body"] and big["ok"]["mapping"] == []


@pytest.mark.parametrize(
    "url",
    [
        "http://api.the-bank.vn/v1/cards",
        "https://inside.the-bank.vn/v1",
        "https://mixed.the-bank.vn/v1",
        "https://localhost/v1",
        "https://127.0.0.1/v1",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/v1",
        "https://kong.dataagent.svc/v1",
        "https://user:pw@api.the-bank.vn/v1",
        "https://nowhere.the-bank.vn/v1",
    ],
)
async def test_only_public_https_addresses_are_called(world: list[httpx.Request], url: str) -> None:
    result = await rest_tries.try_request(job(url=url))
    assert "refused" in result and world == []


async def test_a_secret_outside_the_organisation_or_missing_is_refused(
    world: list[httpx.Request],
) -> None:
    foreign = await rest_tries.try_request(job(secret_ref=f"secret/tenants/{uuid.uuid4()}/k"))
    missing = await rest_tries.try_request(job(secret_ref=f"secret/tenants/{ORG}/rest/none"))
    assert "refused" in foreign and "refused" in missing and world == []
    # A credential header that reached the worker anyway is not sent.
    await rest_tries.try_request(job(headers=[{"name": "Cookie", "value": "s=1"}]))
    assert "Cookie" not in world[-1].headers


async def test_an_unreachable_api_is_a_failure_said_without_detail(
    world: list[httpx.Request], monkeypatch: pytest.MonkeyPatch
) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out connecting to 93.184.216.34")

    monkeypatch.setattr(rest_tries, "_transport", lambda: httpx.MockTransport(down))
    result = await rest_tries.try_request(job())
    assert result == {"failed": "Không kết nối được tới API (ConnectTimeout)."}


def test_the_records_are_found_or_named() -> None:
    nested = {"result": {"items": [{"id": 1, "amount": 2.5}]}}
    assert [m["path"] for m in rest_tries.mapping_of(nested, "result.items")] == [
        "result.items[].id",
        "result.items[].amount",
    ]
    assert [m["path"] for m in rest_tries.mapping_of([{"a": "x"}], None)] == ["[].a"]
    assert [m["path"] for m in rest_tries.mapping_of({"id": 7, "name": "A"}, None)] == [
        "id",
        "name",
    ]
    assert rest_tries.mapping_of({"result": {}}, "result.items") == []
    assert rest_tries.mapping_of("plain", None) == []
    # A column name is a plain identifier whatever the field is called.
    [field] = rest_tries.mapping_of([{"Ngày mở-thẻ": "2026-01-02T08:00:00Z"}], None)
    assert field["column"] == "ngay_mo_the" and field["type"] == "date"


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.ttl: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    def pipeline(self, transaction: bool = True) -> FakeRedis:
        return self

    async def __aenter__(self) -> FakeRedis:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def rpush(self, key: str, value: str) -> None:
        self.lists.setdefault(key, []).append(value)

    def expire(self, key: str, seconds: int) -> None:
        self.ttl[key] = seconds

    def delete(self, *keys: str) -> None:
        for key in keys:
            self.values.pop(key, None)

    async def execute(self) -> None:
        return None


async def test_the_answer_goes_back_on_the_channel_and_the_request_is_dropped(
    world: list[httpx.Request],
) -> None:
    redis, asked = FakeRedis(), job()
    redis.values[f"connector:try:{asked['id']}"] = json.dumps(asked)
    await rest_tries.handle(redis, asked["id"])
    [answer] = redis.lists[f"connector:try:{asked['id']}:result"]
    assert json.loads(answer)["ok"]["status"] == 200
    assert redis.values == {} and redis.ttl[f"connector:try:{asked['id']}:result"] == 60
    # An expired request is answered by nothing.
    await rest_tries.handle(redis, str(uuid.uuid4()))
    assert len(redis.lists) == 1
