"""A crawl run end to end on the V3 schema: claimed from acquire.outbox, pages and originals stored
(BR-150), records extracted for review (UC-14-04), and the run's failures (UC-14-01, UC-14-06)."""

from __future__ import annotations

import datetime
import uuid

import pytest

from services.crawler import runs
from services.crawler.events import OutboxTableSource
from tests.integration.crawler.conftest import Seed, Site, Store, World, rates

pytestmark = [pytest.mark.integration]


async def _work(store: Store) -> int:
    """One claim, as main.run makes it; the number of events handled."""
    async with OutboxTableSource(frozenset({runs.QUEUED})).claim(5) as events:
        for event in events:
            await runs.handle(event, store)
        return len(events)


def _run(seed: Seed, run: uuid.UUID) -> tuple:
    return seed.one(
        "SELECT status, pages_fetched, pages_changed, records_extracted, records_flagged,"
        " started_at IS NOT NULL, finished_at IS NOT NULL FROM acquire.crawl_run WHERE id = %s",
        run,
    )


def _completed(seed: Seed, run: uuid.UUID) -> dict:
    return seed.one(
        "SELECT payload FROM acquire.outbox WHERE aggregate_id = %s"
        " AND event_type = 'acquire.run.completed'",
        run,
    )


@pytest.mark.req("AC-14-02-07")
async def test_a_run_stores_its_pages_and_extracts_records_for_review(
    world: World, seed: Seed, site: Site, store: Store
) -> None:
    site.pages["/lai-suat"] = (
        200,
        "text/html; charset=utf-8",
        rates(("Ngân hàng A", "4,8"), ("Ngân hàng B", "48")),
    )
    source, version = world.source(site)
    run = world.queue(source)

    assert await _work(store) == 1

    assert _run(seed, run) == ("in_review", 1, 1, 2, 2, True, True)
    url, sha, key, retain = seed.one(
        "SELECT url, sha256, object_key, retain_until FROM acquire.crawl_page WHERE run_id = %s",
        run,
    )
    assert url == f"{site.base}/lai-suat"
    assert key == f"{world.org}/{source}/{sha}" and store.objects[key] == site.pages["/lai-suat"][2]
    assert retain > datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=399)
    records = seed.all(
        "SELECT entity_key, values->>'rate_12m', status, review_reasons, page_id IS NOT NULL,"
        " schema_version_id FROM acquire.extracted_record WHERE run_id = %s ORDER BY entity_key",
        run,
    )
    # 48 %/năm is out of the field's 0-15 domain; the one clean record is the random sample.
    assert records == [
        ("Ngân hàng A", "4.8", "flagged", ["random_sample"], True, version),
        ("Ngân hàng B", "48", "flagged", ["out_of_domain"], True, version),
    ]
    event = _completed(seed, run)
    assert event["status"] == "in_review" and event["records_flagged"] == 2
    # The claim was committed: the event is not handed out again.
    assert await _work(store) == 0


async def test_an_unchanged_page_is_not_stored_again(
    world: World, seed: Seed, site: Site, store: Store
) -> None:
    site.pages["/lai-suat"] = (200, "text/html; charset=utf-8", rates(("A", "4,8")))
    source, _ = world.source(site, gate="exceptions")
    first = world.queue(source)
    await _work(store)
    second = world.queue(source)
    await _work(store)
    assert _run(seed, second)[:3] == ("in_review", 1, 0)
    assert len(store.objects) == 1
    assert (
        seed.one(
            "SELECT count(DISTINCT sha256) FROM acquire.crawl_page WHERE run_id IN (%s, %s)",
            first,
            second,
        )
        == 1
    )


async def test_under_the_exceptions_gate_clean_records_are_approved(
    world: World, seed: Seed, site: Site, store: Store
) -> None:
    rows = [(f"Ngân hàng {i}", "4,8") for i in range(8)]
    site.pages["/lai-suat"] = (200, "text/html; charset=utf-8", rates(*rows))
    source, _ = world.source(site, gate="exceptions")
    run = world.queue(source)
    await _work(store)
    counts = dict(
        seed.all(
            "SELECT status, count(*) FROM acquire.extracted_record WHERE run_id = %s GROUP BY status",
            run,
        )
    )
    # At least five sampled for review whatever the gate; the rest pass without a person.
    assert counts == {"flagged": 5, "approved": 3}


async def test_a_page_robots_forbids_is_never_requested_and_the_run_fails(
    world: World, seed: Seed, site: Site, store: Store
) -> None:
    site.pages["/private/lai-suat"] = (200, "text/html", rates(("A", "4,8")))
    source, _ = world.source(site, path="/private/lai-suat")
    run = world.queue(source)
    await _work(store)
    assert _run(seed, run)[0] == "failed"
    assert "/private/lai-suat" not in site.hits
    assert seed.one("SELECT count(*) FROM acquire.crawl_page WHERE run_id = %s", run) == 0


@pytest.mark.req("AC-14-06-04")
async def test_three_blocked_runs_in_a_row_pause_the_source(
    world: World, seed: Seed, site: Site, store: Store
) -> None:
    site.pages["/lai-suat"] = (429, "text/plain", b"slow down")
    source, _ = world.source(site)
    for _ in range(3):
        world.queue(source)
        await _work(store)
    assert seed.one(
        "SELECT status, consecutive_blocked FROM acquire.crawl_source WHERE id = %s", source
    ) == (
        "paused",
        3,
    )
    assert (
        seed.one(
            "SELECT payload->>'reason' FROM acquire.outbox WHERE aggregate_id = %s"
            " AND event_type = 'acquire.source.status_changed'",
            source,
        )
        == "blocked"
    )


@pytest.mark.req("AC-14-01-08")
async def test_a_run_whose_legal_review_lapsed_fetches_nothing(
    world: World, seed: Seed, site: Site, store: Store
) -> None:
    site.pages["/lai-suat"] = (200, "text/html", rates(("A", "4,8")))
    source, _ = world.source(site)
    run = world.queue(source)
    # The review lapsed after the run was queued (the source trigger pauses it too).
    seed.run_sql(
        "UPDATE acquire.crawl_source SET legal_review_due_at = legal_reviewed_at + interval '1 second'"
        " WHERE id = %s",
        source,
    )
    await _work(store)
    assert _run(seed, run)[0] == "failed"
    assert site.hits == []


async def test_a_retry_re_extracts_the_stored_originals_without_fetching(
    world: World, seed: Seed, site: Site, store: Store
) -> None:
    site.pages["/lai-suat"] = (200, "text/html; charset=utf-8", rates(("A", "4,8")))
    source, _ = world.source(site)
    world.queue(source)
    await _work(store)
    site.hits.clear()
    retry = world.queue(source, trigger="retry")
    await _work(store)
    assert site.hits == []
    assert _run(seed, retry)[:4] == ("in_review", 0, 0, 1)


async def test_a_run_abandoned_mid_way_is_failed_when_its_event_comes_back(
    world: World, seed: Seed, site: Site, store: Store
) -> None:
    source, _ = world.source(site)
    run = world.queue(source)
    seed.run_sql(
        "UPDATE acquire.crawl_run SET status = 'fetching', started_at = now() WHERE id = %s", run
    )
    await _work(store)
    assert _run(seed, run)[0] == "failed"
    assert "abandoned" in _completed(seed, run)["reason"]
    # The source is free again: a new run can be queued.
    world.queue(source)
