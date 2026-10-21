"""The crawler against the V3 database (backend/alembic/sql, applied by the migrate suite's
`ddl_server`) as the roles it uses in production: svc_outbox_relay to claim acquire.outbox and
svc_acquire for the run. The site is a local HTTP server; originals go to an in-memory store."""

from __future__ import annotations

import datetime
import threading
import time
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import pytest
from psycopg2.extras import Json

from libs.dataagent_db import engine_for
from services.crawler import fetch
from tests.integration.migrate.conftest import ddl_server  # noqa: F401  (fixture)

PASSWORD = "crawler-integration"
ROLES = ("svc_acquire", "svc_outbox_relay")
NOW = datetime.datetime.now(datetime.UTC)
CHECKLIST = {k: {"result": "pass"} for k in ("terms", "robots", "copyright", "personal_data")}
SHA = "a" * 64
FIELDS = [
    {"name": "bank", "type": "text", "required": True, "key": True, "locator": "td.bank"},
    {
        "name": "rate_12m",
        "type": "percent",
        "required": True,
        "locator": "td.r12",
        "value_constraints": {"min": 0, "max": 15, "unit": "%/năm"},
    },
]


def _login_url(dsn: str, role: str) -> str:
    parts = urlsplit(dsn)
    return urlunsplit(
        (
            "postgresql+asyncpg",
            f"{role}:{PASSWORD}@{parts.hostname}:{parts.port}",
            parts.path,
            "",
            "",
        )
    )


@pytest.fixture(scope="session")
def crawler_database(ddl_server: dict[str, str]) -> Iterator[str]:  # noqa: F811
    dsn = ddl_server["dataagent"]
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        for role in ROLES:
            cur.execute(f"ALTER ROLE {role} WITH LOGIN PASSWORD %s", (PASSWORD,))
    conn.close()
    mp = pytest.MonkeyPatch()
    for role in ROLES:
        mp.setenv(f"DATAAGENT_DB_URL__{role.upper()}", _login_url(dsn, role))
    engine_for.cache_clear()
    yield dsn
    engine_for.cache_clear()
    mp.undo()


class Store:
    """The RawStore of the tests: what was put, and how often."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts = 0

    async def put(self, key: str, content: bytes, content_type: str) -> None:
        self.puts += 1
        self.objects.setdefault(key, content)

    async def get(self, key: str) -> bytes:
        return self.objects[key]


class Site:
    def __init__(self) -> None:
        self.pages: dict[str, tuple[int, str, bytes]] = {
            "/robots.txt": (200, "text/plain", b"User-agent: *\nDisallow: /private\n"),
        }
        self.hits: list[str] = []
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                site.hits.append(self.path)
                status, kind, body = site.pages.get(self.path, (404, "text/plain", b""))
                self.send_response(status)
                self.send_header("content-type", kind)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                pass

        self.http = HTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://localhost:{self.http.server_port}"


def rates(*rows: tuple[str, str]) -> bytes:
    cells = "".join(f'<tr><td class="bank">{b}</td><td class="r12">{r}</td></tr>' for b, r in rows)
    return f'<table class="lai-suat"><tr><th>NH</th><th>12T</th></tr>{cells}</table>'.encode()


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch) -> Iterator[Site]:
    s = Site()
    threading.Thread(target=s.http.serve_forever, daemon=True).start()
    # The site is on 127.0.0.1, which the crawler otherwise refuses; the pace is unit-tested.
    monkeypatch.setattr(fetch, "ALLOW_PRIVATE", True)
    monkeypatch.setattr(fetch, "PACER", fetch.DomainPacer())
    monkeypatch.setattr(fetch.DomainPacer, "wait", _no_wait)
    yield s
    s.http.shutdown()


async def _no_wait(self: fetch.DomainPacer, url: str, interval: float) -> None:
    return None


class Seed:
    def __init__(self, dsn: str) -> None:
        self.conn = psycopg2.connect(dsn)
        self.conn.autocommit = True

    def row(self, table: str, **values: object) -> uuid.UUID:
        values.setdefault("id", uuid.uuid4())
        cols = ", ".join(values)
        marks = ", ".join(["%s"] * len(values))
        params = [str(v) if isinstance(v, uuid.UUID) else v for v in values.values()]
        with self.conn.cursor() as cur:
            cur.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", params)
        return values["id"]  # type: ignore[return-value]

    def run_sql(self, sql: str, *params: object) -> None:
        with self.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])

    def all(self, sql: str, *params: object) -> list[tuple[Any, ...]]:
        with self.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
            return list(cur.fetchall())

    def one(self, sql: str, *params: object) -> Any:
        rows = self.all(sql, *params)
        return rows[0][0] if rows and len(rows[0]) == 1 else (rows[0] if rows else None)


class World:
    """One organisation with a Data Space, an owner and a legal reviewer."""

    def __init__(self, seed: Seed) -> None:
        self.seed = seed
        self.org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Tổ chức")
        self.owner = self._user("owner")
        self.legal = self._user("legal")
        self.space = seed.row(
            "org.data_space",
            org_id=self.org,
            code="DS-TT",
            name="Thị trường",
            steward_id=self.owner,
        )

    def _user(self, name: str) -> uuid.UUID:
        return self.seed.row(
            "org.app_user",
            org_id=self.org,
            external_id=f"kc-{name}-{uuid.uuid4().hex[:6]}",
            username=f"{name}-{uuid.uuid4().hex[:6]}",
            display_name=name,
        )

    def source(
        self, site: Site, *, kind: str = "web_page", path: str = "/lai-suat", gate: str = "all"
    ) -> tuple[uuid.UUID, uuid.UUID]:
        source = self.seed.row(
            "acquire.crawl_source",
            org_id=self.org,
            data_space_id=self.space,
            name=f"Nguồn {uuid.uuid4().hex[:6]}",
            goal="Lãi suất 12 tháng",
            kind=kind,
            seed_urls=Json([f"{site.base}{path}"]),
            allowed_domains=["localhost"],
            trust_level=0.9,
            owner_id=self.owner,
            review_gate=gate,
            terms_url=f"{site.base}/dieu-khoan",
            legal_status="approved",
            legal_checklist=Json(CHECKLIST),
            legal_evidence=Json(
                [
                    {
                        "kind": "terms",
                        "object_key": "legal/t.html",
                        "sha256": SHA,
                        "captured_at": NOW.isoformat(),
                    }
                ]
            ),
            legal_reviewed_by=self.legal,
            legal_reviewed_at=NOW - datetime.timedelta(days=1),
            legal_review_due_at=NOW + datetime.timedelta(days=90),
            terms_sha256=SHA,
        )
        version = self.seed.row(
            "acquire.crawl_schema_version",
            org_id=self.org,
            source_id=source,
            version=1,
            status="active",
            record_selector="table.lai-suat tr",
            fields=Json(FIELDS),
            approved_by=self.owner,
            approved_at=NOW - datetime.timedelta(days=2),
            test_required_accuracy=0.97,
        )
        self.seed.run_sql(
            "UPDATE acquire.crawl_source SET status = 'active', active_schema_version_id = %s WHERE id = %s",
            version,
            source,
        )
        return source, version

    def queue(self, source: uuid.UUID, trigger: str = "manual") -> uuid.UUID:
        """A run and its event, as POST /v1/crawl-sources/{id}/runs writes them."""
        run = self.seed.row(
            "acquire.crawl_run", org_id=self.org, source_id=source, trigger=trigger, status="queued"
        )
        self.seed.row(
            "acquire.outbox",
            org_id=self.org,
            aggregate_type="crawl_run",
            aggregate_id=run,
            event_type="acquire.run.queued",
            payload=Json({"run_id": str(run), "source_id": str(source), "trigger": trigger}),
        )
        return run


@pytest.fixture
def seed(crawler_database: str) -> Iterator[Seed]:
    s = Seed(crawler_database)
    yield s
    s.conn.close()


@pytest.fixture
def world(seed: Seed) -> World:
    return World(seed)


@pytest.fixture
def store() -> Store:
    return Store()


def wait_until(check: Any, seconds: float = 5) -> None:
    end = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < end, "condition not met"
        time.sleep(0.05)
