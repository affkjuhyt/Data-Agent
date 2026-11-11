"""propose_from_relationships: AdventureWorks-shaped FKs become suggested dimensions."""

from __future__ import annotations

import uuid

import pytest

from libs.mart_model import propose_from_relationships, relations_match_active

pytestmark = pytest.mark.unit

FACT = uuid.UUID("00000000-0000-0000-0000-0000000000f1")
DATE = uuid.UUID("00000000-0000-0000-0000-0000000000d1")
PRODUCT = uuid.UUID("00000000-0000-0000-0000-0000000000d2")
CUSTOMER = uuid.UUID("00000000-0000-0000-0000-0000000000d3")


def _ds(table: str) -> dict:
    return {"table_name": table, "schema_name": "public", "display_name": ""}


def test_proposes_one_dim_per_target_preferring_order_date() -> None:
    datasets = {
        DATE: _ds("dim_date"),
        PRODUCT: _ds("dim_product"),
        CUSTOMER: _ds("dim_customer"),
    }
    relationships = [
        {
            "from_dataset_id": FACT,
            "to_dataset_id": DATE,
            "from_columns": ["due_date_key"],
            "to_columns": ["date_key"],
            "status": "active",
        },
        {
            "from_dataset_id": FACT,
            "to_dataset_id": DATE,
            "from_columns": ["order_date_key"],
            "to_columns": ["date_key"],
            "status": "active",
        },
        {
            "from_dataset_id": FACT,
            "to_dataset_id": PRODUCT,
            "from_columns": ["product_key"],
            "to_columns": ["product_key"],
            "status": "active",
        },
        {
            "from_dataset_id": FACT,
            "to_dataset_id": CUSTOMER,
            "from_columns": ["customer_key"],
            "to_columns": ["customer_key"],
            "status": "active",
        },
    ]
    kept, suggested, relations = propose_from_relationships(
        fact_dataset_id=FACT,
        relationships=relationships,
        datasets=datasets,
        dimensions=[],
    )
    assert suggested == []
    assert {d["name"] for d in kept} == {"dim_date", "dim_product", "dim_customer"}
    date = next(d for d in kept if d["name"] == "dim_date")
    assert date["key"] == "order_date_key"
    assert {"from": "order_date_key", "to": "dim_date.date_key"} in relations
    assert {"from": "product_key", "to": "dim_product.product_key"} in relations


def test_named_dimensions_become_relations_not_suggestions() -> None:
    datasets = {PRODUCT: _ds("dim_product"), DATE: _ds("dim_date")}
    relationships = [
        {
            "from_dataset_id": FACT,
            "to_dataset_id": PRODUCT,
            "from_columns": ["product_key"],
            "to_columns": ["product_key"],
            "status": "active",
        },
        {
            "from_dataset_id": FACT,
            "to_dataset_id": DATE,
            "from_columns": ["order_date_key"],
            "to_columns": ["date_key"],
            "status": "active",
        },
    ]
    kept, suggested, relations = propose_from_relationships(
        fact_dataset_id=FACT,
        relationships=relationships,
        datasets=datasets,
        dimensions=[{"name": "dim_product", "key": "product_key"}],
    )
    assert [d["name"] for d in kept] == ["dim_product"]
    assert [s["name"] for s in suggested] == ["dim_date"]
    assert relations == [{"from": "product_key", "to": "dim_product.product_key"}]


def test_relations_match_active() -> None:
    datasets = {PRODUCT: _ds("dim_product")}
    relationships = [
        {
            "from_dataset_id": FACT,
            "to_dataset_id": PRODUCT,
            "from_columns": ["product_key"],
            "to_columns": ["product_key"],
            "status": "active",
        }
    ]
    ok, failures = relations_match_active(
        fact_dataset_id=FACT,
        relations=[{"from": "product_key", "to": "dim_product.product_key"}],
        relationships=relationships,
        datasets=datasets,
    )
    assert ok and failures == []
    bad, why = relations_match_active(
        fact_dataset_id=FACT,
        relations=[{"from": "x", "to": "dim_product.product_key"}],
        relationships=relationships,
        datasets=datasets,
    )
    assert not bad and why
