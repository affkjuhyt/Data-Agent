"""ingest_artifact_and_fetch

S2-02 / NOP-114 (docs/v2/14-input-data-db.md §4): the immutable raw layer.

  * ingest.artifact: every distinct content a source has served, keyed by
    (source_id, sha256); the bytes live in MinIO bucket `raw` at
    {source_id}/{sha256} and are never overwritten or deleted (ADR-V2-21).
  * ingest.fetch: every successful download, including those whose content was
    unchanged, so "the source is alive but has not published" is visible.
  * "KBData".artifact_sha256 ties a document's KB search copy back to its raw.

Both tables are append-only: no role holds UPDATE or DELETE (the default
privileges NOP-147 gives ingest_svc on its schema are revoked here). Writers:
crawler_svc (ADR-V2-46). Readers: ingest_svc, registry_svc, governance_svc,
maintenance. Row-level security by tenant_id, FORCEd.

Revision ID: 9f3c7a1e5d2b
Revises: 8e4b6d1f3a7c
Create Date: 2026-10-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "9f3c7a1e5d2b"
down_revision: str | None = "8e4b6d1f3a7c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TID = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
_TABLES = ("ingest.artifact", "ingest.fetch")
_READERS = ("ingest_svc", "registry_svc", "governance_svc", "maintenance")

_DDL = (
    """
    CREATE TABLE ingest.artifact (
        artifact_id      uuid PRIMARY KEY,
        tenant_id        uuid NOT NULL,
        source_id        uuid NOT NULL,
        sha256           char(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
        object_key       text NOT NULL,
        bytes            bigint NOT NULL CHECK (bytes >= 0),
        content_type     text NOT NULL,
        filename         text,
        fetched_from_url text,
        first_seen_at    timestamptz NOT NULL DEFAULT now(),
        first_run_id     uuid,
        UNIQUE (source_id, sha256)
    )
    """,
    """
    CREATE TABLE ingest.fetch (
        fetch_id      uuid PRIMARY KEY,
        tenant_id     uuid NOT NULL,
        source_id     uuid NOT NULL,
        run_id        uuid NOT NULL,
        artifact_id   uuid NOT NULL REFERENCES ingest.artifact,
        fetched_at    timestamptz NOT NULL,
        http_status   smallint,
        etag          text,
        last_modified timestamptz,
        url           text NOT NULL
    )
    """,
    "CREATE INDEX ix_fetch_source_time ON ingest.fetch (source_id, fetched_at DESC)",
    "COMMENT ON TABLE ingest.artifact IS 'Every distinct content a source served, append-only. "
    "Bytes in MinIO bucket raw at {source_id}/{sha256} (object_key), never overwritten or deleted.'",
    "COMMENT ON TABLE ingest.fetch IS 'Every successful download of a source, append-only, "
    "including unchanged content.'",
)


def upgrade() -> None:
    for statement in _DDL:
        op.execute(statement)
    for table in _TABLES:
        # Append-only: nobody updates or deletes, whatever the schema defaults granted.
        op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM ingest_svc")
        op.execute(f"GRANT SELECT, INSERT ON {table} TO crawler_svc")
        for role in _READERS:
            op.execute(f"GRANT SELECT ON {table} TO {role}")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} AS PERMISSIVE FOR ALL TO PUBLIC "
            f"USING (tenant_id = {_TID}) WITH CHECK (tenant_id = {_TID})"
        )
    op.execute('ALTER TABLE "KBData" ADD COLUMN artifact_sha256 char(64)')
    op.execute(
        'COMMENT ON COLUMN "KBData".artifact_sha256 IS '
        "'SHA-256 of the raw this search copy was made from: ingest.artifact (source_id, sha256). "
        "NULL for documents not fetched by a source.'"
    )


def downgrade() -> None:
    op.execute('ALTER TABLE "KBData" DROP COLUMN artifact_sha256')
    op.execute("DROP TABLE ingest.fetch")
    op.execute("DROP TABLE ingest.artifact")
