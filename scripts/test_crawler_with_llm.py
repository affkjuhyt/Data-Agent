"""
Test luồng Schema Crawler Worker với LLM thật (Ollama via LiteLLM).
Chỉ mock Snowflake discovery; LLM describe + embed chạy thật.

Yêu cầu:
    - PostgreSQL tại localhost:5432 (DB: dataagent, user: admin)
    - Qdrant tại localhost:6333
    - LiteLLM tại localhost:4000 (model: planner + embedder)
    - Đã chạy alembic migration

Chạy:
    LITELLM_BASE_URL=http://localhost:4000 LITELLM_API_KEY=sk-1234 \\
        .venv-sandbox/bin/python3 scripts/test_crawler_with_llm.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "schema-crawler-worker"))

from unittest.mock import patch
import psycopg2
from qdrant_client import QdrantClient
import main as worker

TENANT_ID    = "00000000-0000-0000-0000-000000000001"
WAREHOUSE_ID = "00000000-0000-0000-0000-000000000002"

fake_meta = {
    "fact_stock_price": {
        "schema_name": "ANALYTICS",
        "table_name":  "fact_stock_price",
        "columns": [
            {"name": "ticker",     "type": "VARCHAR", "nullable": False, "comment": None},
            {"name": "trade_date", "type": "DATE",    "nullable": False, "comment": None},
            {"name": "close",      "type": "FLOAT",   "nullable": False, "comment": None},
            {"name": "volume",     "type": "NUMBER",  "nullable": False, "comment": None},
        ],
        "row_count": 4820000,
        "last_altered": None,
    }
}


def seed_test_data(conn):
    with conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "Tenants" (id, name, slug, data_residency, is_active) '
            "VALUES (%s,'Test','test','vn',true) ON CONFLICT DO NOTHING",
            (TENANT_ID,),
        )
        cur.execute(
            'INSERT INTO "KBWarehouse" (warehouse_id, tenant_id, service, is_deleted) '
            "VALUES (%s,%s,'Snowflake',false) ON CONFLICT DO NOTHING",
            (WAREHOUSE_ID, TENANT_ID),
        )
        # Force re-crawl so the updated English prompt is used
        cur.execute('DELETE FROM "SchemaCatalog" WHERE warehouse_id = %s', (WAREHOUSE_ID,))
    conn.commit()


def main():
    conn   = psycopg2.connect("host=localhost port=5432 dbname=dataagent user=admin")
    qdrant = QdrantClient(url="http://localhost:6333")

    seed_test_data(conn)

    with patch.object(worker, "discover_and_describe", return_value=fake_meta):
        worker.ensure_qdrant_collection(qdrant)
        worker.crawl_warehouse(conn, qdrant, TENANT_ID, WAREHOUSE_ID, {}, None)

    print("\n=== SchemaCatalog (PostgreSQL) ===")
    with conn.cursor() as cur:
        cur.execute('SELECT table_name, text_description FROM "SchemaCatalog" ORDER BY table_name')
        for name, desc in cur.fetchall():
            print(f"\n--- {name} ---")
            print(desc)

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
