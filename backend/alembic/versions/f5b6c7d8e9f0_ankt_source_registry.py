"""ankt source registry: sources, series bindings and connector run history

Revision ID: f5b6c7d8e9f0
Revises: e4a5b6c7d8e9
Create Date: 2026-09-16

Where a source lives decides who can add one. Held in a YAML file in the repo,
adding the BIS feed means a code change, a review and a deploy — so the people
who actually know which sources matter, the data stewards, cannot do it, and
nothing can be listed on a screen. These three tables move that decision out of
the repository and into the product.

`connector` names a PROTOCOL, not a publisher: 'sdmx', 'rest-json'. TECH-DESIGN
T10 counts the 38 sources in data/sources.yaml as 10 http-file, 9 rest-api,
5 sdmx, 5 web-crawl, 3 bulk-parquet, 1 rss, 1 manual-upload — seven plugins for
thirty-eight sources. A plugin per publisher would be thirty-eight plugins, and
the thirty-ninth source would still need a deploy. Everything specific to one
source lives in `config` and in the binding's `key`.

`connector_run` exists so the Nguồn dữ liệu screen can answer what POC-PLAN's
first demo scene asks of it — how many sources, when each last ran, how many
records, what state. None of that is derivable from a file.

Verified by tests/integration/test_ankt_source_registry.py.
"""
from alembic import op

# Revision identifiers, used by Alembic.
revision = "f5b6c7d8e9f0"
down_revision = "e4a5b6c7d8e9"
branch_labels = None
depends_on = None


_TABLES = ("source", "source_binding", "connector_run")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ankt.source (
            source_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id      uuid NOT NULL,
            code           text NOT NULL,
            title          text NOT NULL,
            publisher      text,
            -- The protocol plugin that can read this source, NOT its publisher.
            connector      text NOT NULL,
            -- Everything the plugin needs that differs per source: base_url,
            -- dataflow, response shape. Opaque here on purpose — the plugin
            -- validates it, so a new protocol needs no migration.
            config         jsonb NOT NULL DEFAULT '{}'::jsonb,
            schedule       text,
            licence        text,
            classification text NOT NULL DEFAULT 'public',
            enabled        boolean NOT NULL DEFAULT true,
            created_at     timestamptz NOT NULL DEFAULT now(),
            updated_at     timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT source_code_unique UNIQUE (tenant_id, code)
        )
        """,
    )

    op.execute(
        """
        CREATE TABLE ankt.source_binding (
            binding_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   uuid NOT NULL,
            source_id   uuid NOT NULL REFERENCES ankt.source(source_id) ON DELETE CASCADE,
            variable_id uuid NOT NULL REFERENCES ankt.variable(variable_id),
            -- The series identifier in the source's own vocabulary:
            -- 'VNM.GGXWDG.A' for SDMX, 'NY.GDP.MKTP.CN' for a World Bank code.
            series_key  text NOT NULL,
            enabled     boolean NOT NULL DEFAULT true,
            created_at  timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT source_binding_unique UNIQUE (tenant_id, source_id, series_key)
        )
        """,
    )

    op.execute(
        """
        CREATE TABLE ankt.connector_run (
            run_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   uuid NOT NULL,
            source_id   uuid NOT NULL REFERENCES ankt.source(source_id) ON DELETE CASCADE,
            started_at  timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz,
            -- 'partial' is its own state: four series of five loaded is neither
            -- a success to report as green nor a failure to retry wholesale.
            status      text NOT NULL DEFAULT 'running'
                        CHECK (status IN ('running','ok','partial','failed')),
            fetched     integer NOT NULL DEFAULT 0,
            inserted    integer NOT NULL DEFAULT 0,
            skipped     integer NOT NULL DEFAULT 0,
            error       text,
            warnings    jsonb NOT NULL DEFAULT '[]'::jsonb,
            triggered_by text NOT NULL DEFAULT 'manual'
                        CHECK (triggered_by IN ('manual','schedule','backfill'))
        )
        """,
    )
    op.execute(
        """
        CREATE INDEX connector_run_latest
            ON ankt.connector_run (tenant_id, source_id, started_at DESC)
        """,
    )

    op.execute("GRANT USAGE ON SCHEMA ankt TO app_user")
    for table in _TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ankt.{table} TO app_user")
        op.execute(f"ALTER TABLE ankt.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE ankt.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON ankt.{table}
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            """,
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP TABLE IF EXISTS ankt.{table} CASCADE")
