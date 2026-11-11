"""acquire.schema.test_requested: a schema version run on its held-out sample (UC-14-02, BR-151).

POST /v1/crawl-schemas/{id}:test moves the version to testing with no result; this reads the
stored originals of its sample pages (the source's latest page of each URL when the version names
none), extracts them with the version, and writes test_required_accuracy: the share of the
required cells (each record's required fields, every field when none is required) that came out
with a value read as the field's type (confidence 1) and inside its value_constraints. The version
stays in testing: approving it is the owner's decision, which the api refuses under 95%.

This measures extraction completeness, not labelled correctness: nobody has said what each page
should yield, so a value that is well-formed and in its domain but read from the wrong cell counts
as right. The random sample a person reviews in every run (extract.sample) is what catches that.

The pages come from the store, never from the site: a test is not a crawl, and the originals are
the ones the version will be judged on again (BR-150).
"""

from __future__ import annotations

import decimal
import logging
import uuid
from typing import Any

from sqlalchemy import update

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import acquire
from libs.tabular import profile as ingest_profile
from services.crawler import extract, runs
from services.crawler.events import Event
from services.crawler.storage import RawStore

log = logging.getLogger(__name__)

TEST_REQUESTED = "acquire.schema.test_requested"
_PLACES = decimal.Decimal("0.0001")


def required_accuracy(
    records: list[extract.Record], fields: list[dict[str, Any]]
) -> decimal.Decimal:
    """The share of required cells extracted complete: typed (confidence 1) and in domain."""
    required = [f for f in fields if f.get("required")] or fields
    cells = right = 0
    for record in records:
        for f in required:
            cells += 1
            value = record.values.get(f["name"])
            right += (
                value is not None
                and record.field_confidence.get(f["name"]) == 1.0
                and not extract.out_of_domain(value, f.get("value_constraints") or {})
            )
    if not cells:
        return decimal.Decimal(0).quantize(_PLACES)
    return (decimal.Decimal(right) / decimal.Decimal(cells)).quantize(_PLACES)


async def handle(event: Event, store: RawStore) -> None:
    org_id = event.org_id
    version_id = uuid.UUID(str(event.payload.get("schema_version_id") or event.aggregate_id))
    async with scoped_session(runs.ROLE, org_id) as session:
        version = await session.get(acquire.CrawlSchemaVersion, version_id)
        # Approved, rejected or tested again since: this request no longer speaks for it.
        if version is None or version.status != "testing":
            return
        source = await session.get(acquire.CrawlSource, version.source_id)
        assert source is not None
        reading = await ingest_profile.load(session, source.data_space_id, source.ingest_profile)
        selector, fields = version.record_selector, list(version.fields or [])
        sample = [uuid.UUID(str(p)) for p in event.payload.get("sample_page_ids") or []]
    pages = await runs.stored_pages(org_id, source.id, store, sample or None)
    records: list[extract.Record] = []
    for page in pages:
        try:
            records += extract.extract(
                page.content,
                page.content_type,
                selector,
                fields,
                reading.decimal_mark,
                reading.date_order,
            )
        except extract.ExtractionError as exc:
            # An unreadable page adds no cell, so it does not move the share; a version that
            # reads none of its sample has no cell at all and scores 0.
            log.info("schema test %s: page %s not extracted: %s", version_id, page.id, exc)
    accuracy = required_accuracy(records, fields)
    async with scoped_session(runs.ROLE, org_id) as session:
        await session.execute(
            update(acquire.CrawlSchemaVersion)
            .where(
                acquire.CrawlSchemaVersion.id == version_id,
                acquire.CrawlSchemaVersion.status == "testing",
            )
            .values(test_required_accuracy=accuracy)
        )
    log.info(
        "schema test %s: %s of required fields on %d pages, %d records",
        version_id,
        accuracy,
        len(pages),
        len(records),
    )
