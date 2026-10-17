"""Re-processing reconciles instead of starting over (BR-108, AC-05-02-05, AC-05-03-04), and the
collection's PII policy decides what is stored (BR-110)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest

from services.worker_document.handler import Meta, mask_for, plan_chunks
from services.worker_document.processing import document_chunks, parse_tsv

pytestmark = pytest.mark.unit


def meta(policy: str = "mask") -> Meta:
    return Meta(
        org_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        collection_id=uuid.uuid4(),
        data_space_id=uuid.uuid4(),
        title="Quyết định 123",
        doc_no="123/QĐ",
        effective_from=None,
        created_by=None,
        object_key="k",
        media="pdf",
        qdrant_collection="doc_x",
        embed_model="bge-m3",
        pii_policy=policy,
    )


@dataclass
class StoredChunk:
    id: uuid.UUID
    page_from: int
    page_to: int
    section_path: str | None
    text: str
    context_header: str | None
    human_corrected: bool = False
    qdrant_point_id: uuid.UUID | None = None
    embed_model: str | None = "bge-m3"


def stored(pages: list[str]) -> list[StoredChunk]:
    out = []
    for d in document_chunks(pages):
        i = uuid.uuid4()
        out.append(
            StoredChunk(i, d.page_from, d.page_to, d.section_path, d.text, None, qdrant_point_id=i)
        )
    return out


PAGES = [
    "Điều 1. Phạm vi\n\nQuy định về lãi suất.",
    "Điều 2. Lãi suất\n\n1. Kỳ hạn 12 tháng 5,8%/năm.",
    "Điều 3. Hiệu lực\n\nCó hiệu lực từ ngày ký.",
]


@pytest.mark.req("AC-05-02-05")
def test_only_the_changed_page_is_processed_again() -> None:
    existing = stored(PAGES)
    changed = [PAGES[0], "Điều 2. Lãi suất\n\n1. Kỳ hạn 12 tháng 6,0%/năm.", PAGES[2]]
    plan = plan_chunks(meta(), changed, {1, 3}, existing)
    assert [c.draft.page_from for c in plan.new] == [2]
    assert {c.id for c in plan.kept} == {existing[0].id, existing[2].id}
    assert plan.removed == [existing[1].id]
    assert plan.reindex == []


def test_a_redelivered_event_changes_nothing() -> None:
    existing = stored(PAGES)
    plan = plan_chunks(meta(), PAGES, {1, 2, 3}, existing)
    assert (plan.new, plan.removed, plan.reindex) == ([], [], [])


def test_a_kept_chunk_without_a_vector_is_embedded_again() -> None:
    existing = stored(PAGES)
    existing[0].qdrant_point_id = None
    existing[2].embed_model = "bge-m2"
    plan = plan_chunks(meta(), PAGES, {1, 2, 3}, existing)
    assert {c.id for c in plan.reindex} == {existing[0].id, existing[2].id}


@pytest.mark.req("AC-05-03-04")
def test_a_corrected_chunk_stays_while_its_page_is_unchanged() -> None:
    existing = stored(PAGES)
    existing[1].text, existing[1].human_corrected = "1. Kỳ hạn 12 tháng 5,9%/năm.", True
    plan = plan_chunks(meta(), PAGES, {1, 2, 3}, existing)
    assert existing[1] in plan.kept and existing[1].id not in plan.removed
    assert all(c.draft.page_from != 2 for c in plan.new)
    # The page itself changed: the correction no longer describes it and is processed again.
    plan = plan_chunks(meta(), PAGES, {1, 3}, existing)
    assert existing[1].id in plan.removed
    assert [c.draft.page_from for c in plan.new] == [2]


def test_personal_data_is_masked_or_kept_by_policy() -> None:
    text = "Khách hàng số CCCD 001203004567, điện thoại 0912345678."
    masked, flag = mask_for("mask", text)
    assert "001203004567" not in masked and "0912345678" not in masked and flag
    kept, flag = mask_for("keep_for_authorized", text)
    assert kept == text and flag is False
    assert mask_for("keep_for_authorized", "Không có dữ liệu cá nhân.")[1] is True


def test_masked_chunks_are_planned_masked() -> None:
    plan = plan_chunks(meta(), ["Điều 1. Liên hệ\n\nGọi 0912345678."], set(), [])
    assert "0912345678" not in plan.new[0].text and plan.new[0].pii_masked


@pytest.mark.req("AC-05-02-07")
def test_ocr_confidence_is_the_length_weighted_word_confidence() -> None:
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    rows = [
        "5\t1\t1\t1\t1\t1\t0\t0\t1\t1\t96\tLãi",
        "5\t1\t1\t1\t1\t2\t0\t0\t1\t1\t90\tsuất",
        "5\t1\t1\t1\t2\t1\t0\t0\t1\t1\t60\t5,8%",
        "4\t1\t1\t1\t2\t0\t0\t0\t1\t1\t-1\t",
        "5\t1\t2\t1\t1\t1\t0\t0\t1\t1\t-1\t ",
        "5\t1\t2\t1\t1\t2\t0\t0\t1\t1\t80\tĐiều",
    ]
    text, conf = parse_tsv("\n".join([header, *rows]))
    assert text == "Lãi suất\n5,8%\n\nĐiều"
    assert conf == round((96 * 3 + 90 * 4 + 60 * 4 + 80 * 4) / 15 / 100, 3)
    assert parse_tsv(header) == ("", 0.0)
