"""A schema version run on its held-out sample (POST /v1/crawl-schemas/{id}:test, UC-14-02, BR-151):
claimed from acquire.outbox, the version's test_required_accuracy written, the version left in
testing for its owner to approve."""

from __future__ import annotations

import decimal
import uuid

import pytest
from psycopg2.extras import Json

from services.crawler import schema_tests
from services.crawler.events import OutboxTableSource
from services.crawler.main import HANDLERS
from tests.integration.crawler.conftest import FIELDS, Seed, Site, Store, World, rates

pytestmark = [pytest.mark.integration]


async def _work(store: Store) -> int:
    async with OutboxTableSource(frozenset(HANDLERS)).claim(5) as events:
        for event in events:
            await HANDLERS[event.event_type](event, store)
        return len(events)


def _testing(world: World, source: uuid.UUID, *, status: str = "testing") -> uuid.UUID:
    return world.seed.row(
        "acquire.crawl_schema_version",
        org_id=world.org,
        source_id=source,
        version=2,
        status=status,
        record_selector="table.lai-suat tr",
        fields=Json(FIELDS),
    )


def _request(world: World, version: uuid.UUID, source: uuid.UUID, pages: list[uuid.UUID]) -> None:
    """The event as POST /v1/crawl-schemas/{id}:test writes it."""
    world.seed.row(
        "acquire.outbox",
        org_id=world.org,
        aggregate_type="crawl_schema_version",
        aggregate_id=version,
        event_type=schema_tests.TEST_REQUESTED,
        payload=Json(
            {
                "schema_version_id": str(version),
                "source_id": str(source),
                "data_space_id": str(world.space),
                "sample_page_ids": [str(p) for p in pages],
                "status": "testing",
            }
        ),
    )


def _version(seed: Seed, version: uuid.UUID) -> tuple:
    return seed.one(
        "SELECT status, test_required_accuracy FROM acquire.crawl_schema_version WHERE id = %s",
        version,
    )


async def test_the_test_measures_required_fields_on_the_latest_stored_pages(
    world: World, seed: Seed, site: Site, store: Store
) -> None:
    # Three records, two required fields: 48 is out of the 0-15 domain and "x" is no number,
    # so four of the six required cells are right.
    site.pages["/lai-suat"] = (
        200,
        "text/html; charset=utf-8",
        rates(("Ngân hàng A", "4,8"), ("Ngân hàng B", "48"), ("Ngân hàng C", "x")),
    )
    source, _ = world.source(site)
    world.queue(source)
    await _work(store)
    version = _testing(world, source)
    _request(world, version, source, [])
    site.hits.clear()

    assert await _work(store) == 1

    assert _version(seed, version) == ("testing", decimal.Decimal("0.6667"))
    # The test reads the stored originals, never the site.
    assert site.hits == []
    assert await _work(store) == 0


async def test_the_named_sample_pages_are_the_ones_read(
    world: World, seed: Seed, site: Site, store: Store
) -> None:
    site.pages["/lai-suat"] = (200, "text/html; charset=utf-8", rates(("A", "4,8"), ("B", "5")))
    source, _ = world.source(site)
    first = world.queue(source)
    await _work(store)
    site.pages["/lai-suat"] = (200, "text/html; charset=utf-8", rates(("A", "48"), ("B", "x")))
    world.queue(source)
    await _work(store)
    kept = seed.one("SELECT id FROM acquire.crawl_page WHERE run_id = %s", first)
    version = _testing(world, source)
    _request(world, version, source, [kept])

    await _work(store)

    assert _version(seed, version) == ("testing", decimal.Decimal("1.0000"))


async def test_a_version_no_longer_testing_is_left_alone(
    world: World, seed: Seed, site: Site, store: Store
) -> None:
    site.pages["/lai-suat"] = (200, "text/html; charset=utf-8", rates(("A", "4,8")))
    source, _ = world.source(site)
    world.queue(source)
    await _work(store)
    version = _testing(world, source, status="draft")
    _request(world, version, source, [])

    assert await _work(store) == 1

    assert _version(seed, version) == ("draft", None)


async def test_no_page_to_read_measures_nothing_right(
    world: World, seed: Seed, site: Site, store: Store
) -> None:
    source, _ = world.source(site)
    version = _testing(world, source)
    _request(world, version, source, [])

    await _work(store)

    # A test with nothing to read is a result: nothing extracted, so the owner cannot approve.
    assert _version(seed, version) == ("testing", decimal.Decimal("0.0000"))
