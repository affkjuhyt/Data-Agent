"""graph mirror provenance: tenant/kb scoping + per-chunk block_id on KBNeo4j tables

Revision ID: c9d0e1f2a3b4
Revises: c3d4e5f6a7b8
Create Date: 2026-06-27

KBNeo4jNode/KBNeo4jRelationship become the Postgres mirror of the ingestion graph.
Nodes gain tenant_id/knowledge_base and an optional connection_id. Relationships
become one row per (entity pair, chunk): block_id joins the primary key so an edge
exists while >=1 row references it and its count is COUNT(*) over the pair.

Renamed from its original revision id d4e5f6a7b8c9 — that id collided with
d4e5f6a7b8c9_schema_catalog.py (an independent branch also forking off
c3d4e5f6a7b8, added by a different PR around the same time). Same class of
bug as the b2c3d4e5f6a7 collision documented in
docs/Sandbox_SchemaCrawler_Implementation.md §4/§8 — two branches picked the
same hex id independently. Fixed the same way: rename the newer of the two
(this one; schema_catalog's id already had a real dependent —
c75156d1bd53's merge — so it was left untouched) and add a merge migration
(d0e1f2a3b4c5) joining this branch back into the main line.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("KBNeo4jNode", "connection_id", existing_type=UUID(), nullable=True)
    op.add_column("KBNeo4jNode", sa.Column("tenant_id", UUID(), nullable=True))
    op.add_column("KBNeo4jNode", sa.Column("knowledge_base", sa.String(512), nullable=True))
    op.create_index("idx_kbneo4jnode_tenant_kb", "KBNeo4jNode", ["tenant_id", "knowledge_base"])

    op.drop_constraint("KBNeo4jRelationship_pkey", "KBNeo4jRelationship", type_="primary")
    op.add_column("KBNeo4jRelationship", sa.Column("block_id", UUID(), nullable=False))
    op.add_column("KBNeo4jRelationship", sa.Column("data_id", UUID(), nullable=True))
    op.add_column("KBNeo4jRelationship", sa.Column("tenant_id", UUID(), nullable=True))
    op.add_column("KBNeo4jRelationship", sa.Column("knowledge_base", sa.String(512), nullable=True))
    op.create_primary_key("KBNeo4jRelationship_pkey", "KBNeo4jRelationship", ["from_node", "to_node", "block_id"])
    op.create_index("idx_kbneo4jrelationship_pair", "KBNeo4jRelationship", ["from_node", "to_node"])
    op.create_index("idx_kbneo4jrelationship_block", "KBNeo4jRelationship", ["block_id"])
    op.create_index("idx_kbneo4jrelationship_data", "KBNeo4jRelationship", ["data_id"])
    op.create_index("idx_kbneo4jrelationship_tenkb", "KBNeo4jRelationship", ["tenant_id", "knowledge_base"])


def downgrade() -> None:
    op.drop_index("idx_kbneo4jrelationship_tenkb", "KBNeo4jRelationship")
    op.drop_index("idx_kbneo4jrelationship_data", "KBNeo4jRelationship")
    op.drop_index("idx_kbneo4jrelationship_block", "KBNeo4jRelationship")
    op.drop_index("idx_kbneo4jrelationship_pair", "KBNeo4jRelationship")
    op.drop_constraint("KBNeo4jRelationship_pkey", "KBNeo4jRelationship", type_="primary")
    op.drop_column("KBNeo4jRelationship", "knowledge_base")
    op.drop_column("KBNeo4jRelationship", "tenant_id")
    op.drop_column("KBNeo4jRelationship", "data_id")
    op.drop_column("KBNeo4jRelationship", "block_id")
    op.create_primary_key("KBNeo4jRelationship_pkey", "KBNeo4jRelationship", ["from_node", "to_node"])

    op.drop_index("idx_kbneo4jnode_tenant_kb", "KBNeo4jNode")
    op.drop_column("KBNeo4jNode", "knowledge_base")
    op.drop_column("KBNeo4jNode", "tenant_id")
    op.alter_column("KBNeo4jNode", "connection_id", existing_type=UUID(), nullable=False)
