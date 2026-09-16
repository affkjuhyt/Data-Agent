"""ankt observation store: variables, source documents, releases, observations

Revision ID: d3f4a5b6c7d8
Revises: c7d8e9f0a1b2
Create Date: 2026-09-16

The unit of truth for ANKT is "a figure published in a specific document at a
specific vintage", not "a row in a warehouse" (README section 1). These four
tables are that unit. They live in their own `ankt` schema so the indicator
platform's storage is separable from the KB tables in `public` — the target
design moves observations to ClickHouse once volume justifies it (ADR-1), and a
schema boundary is what makes that a migration rather than an excavation.

Three guarantees are enforced by the database rather than by the code that
writes to it, because a guarantee that depends on every writer remembering is
not a guarantee:

**Append-only values.** `observation_value_is_immutable` rejects any UPDATE
that changes a figure or which series/period/release it belongs to. A revised
number is a NEW observation against a NEW release; the superseded one stays
readable, which is what makes an old answer reproducible. Review may still move
`status` candidate -> approved/rejected, because approving is not rewriting.

**Idempotent ingestion.** The unique key (tenant, variable, area, period,
release) means re-running a connector over the same release cannot duplicate a
figure (README section 3.3 principle 1).

**Tenant isolation.** RLS here is written out explicitly: the policy loop in
a1b2c3d4e5f6 only covers `table_schema = 'public'`, so tables in `ankt` would
otherwise be created with no policy at all — visible to every tenant, silently.

Verified by tests/integration/test_ankt_observation_store.py.
"""
from alembic import op

# Revision identifiers, used by Alembic.
revision = "d3f4a5b6c7d8"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


_TABLES = ("variable", "source_document", "release", "observation")


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS ankt")

    # Mirrors libs.indicator.quantity.Unit and formula.StockOrFlow. Kept as
    # CHECK constraints rather than PG enums: adding a value to an enum needs
    # ALTER TYPE and cannot run inside some transaction contexts, whereas the
    # catalog will gain units as domains are added.
    op.execute(
        """
        CREATE TABLE ankt.variable (
            variable_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       uuid NOT NULL,
            code            text NOT NULL,
            concept         text,
            measure         text,
            price_basis     text,
            unit            text NOT NULL
                            CHECK (unit IN ('money','percent','percentage_point',
                                            'ratio','persons','index')),
            unit_multiplier smallint NOT NULL DEFAULT 0,
            currency        char(3),
            stock_or_flow   text NOT NULL
                            CHECK (stock_or_flow IN ('stock','flow','ratio')),
            perimeter       text,
            created_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT variable_code_unique UNIQUE (tenant_id, code),
            CONSTRAINT variable_money_has_currency
                CHECK ((unit = 'money') = (currency IS NOT NULL))
        )
        """,
    )

    op.execute(
        """
        CREATE TABLE ankt.source_document (
            doc_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id      uuid NOT NULL,
            title          text NOT NULL,
            doc_number     text,
            issuer         text,
            issued_date    date,
            effective_from date,
            effective_to   date,
            doc_type       text,
            url            text,
            file_uri       text,
            sha256         char(64) NOT NULL,
            source_tier    text,
            classification text NOT NULL DEFAULT 'internal',
            created_at     timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT source_document_sha_unique UNIQUE (tenant_id, sha256)
        )
        """,
    )

    op.execute(
        """
        CREATE TABLE ankt.release (
            release_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       uuid NOT NULL,
            doc_id          uuid REFERENCES ankt.source_document(doc_id),
            release_date    date NOT NULL,
            vintage_label   text,
            revision_status text NOT NULL DEFAULT 'preliminary'
                            CHECK (revision_status IN
                                   ('estimate','preliminary','official','final')),
            created_at      timestamptz NOT NULL DEFAULT now()
        )
        """,
    )

    # NUMERIC(38, 6) matches the Decimal(38, 6) the ClickHouse sketch uses
    # (SERVICE-DESIGN section 3.2), so moving the table later does not change
    # any stored figure.
    op.execute(
        """
        CREATE TABLE ankt.observation (
            obs_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       uuid NOT NULL,
            variable_id     uuid NOT NULL REFERENCES ankt.variable(variable_id),
            release_id      uuid NOT NULL REFERENCES ankt.release(release_id),
            reference_area  text NOT NULL DEFAULT 'VN',
            period          text NOT NULL,
            value           numeric(38, 6) NOT NULL,
            unit            text NOT NULL,
            unit_multiplier smallint NOT NULL DEFAULT 0,
            currency        char(3),
            source_ref      jsonb,
            extracted_by    text NOT NULL DEFAULT 'manual'
                            CHECK (extracted_by IN ('ocr','api','manual','import')),
            reviewed_by     text,
            status          text NOT NULL DEFAULT 'candidate'
                            CHECK (status IN ('candidate','approved','rejected')),
            created_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT observation_natural_key
                UNIQUE (tenant_id, variable_id, reference_area, period, release_id)
        )
        """,
    )

    # The point-in-time read: newest release at or before `as_of`, for one
    # series and period. Ordering by created_at after release_date keeps the
    # answer deterministic when two releases share a publication date.
    op.execute(
        """
        CREATE INDEX observation_series_period
            ON ankt.observation (tenant_id, variable_id, reference_area, period)
        """,
    )
    op.execute("CREATE INDEX release_date_idx ON ankt.release (tenant_id, release_date)")

    op.execute(
        """
        CREATE FUNCTION ankt.reject_observation_value_update() RETURNS trigger AS $$
        BEGIN
            IF NEW.value           IS DISTINCT FROM OLD.value
            OR NEW.variable_id     IS DISTINCT FROM OLD.variable_id
            OR NEW.release_id      IS DISTINCT FROM OLD.release_id
            OR NEW.period          IS DISTINCT FROM OLD.period
            OR NEW.reference_area  IS DISTINCT FROM OLD.reference_area
            OR NEW.unit            IS DISTINCT FROM OLD.unit
            OR NEW.unit_multiplier IS DISTINCT FROM OLD.unit_multiplier THEN
                RAISE EXCEPTION
                    'ankt.observation is append-only: a corrected figure is a new '
                    'observation against a new release, so the superseded one stays '
                    'readable and an earlier answer can still be reproduced';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """,
    )
    op.execute(
        """
        CREATE TRIGGER observation_value_is_immutable
            BEFORE UPDATE ON ankt.observation
            FOR EACH ROW EXECUTE FUNCTION ankt.reject_observation_value_update()
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
    op.execute("DROP TRIGGER IF EXISTS observation_value_is_immutable ON ankt.observation")
    op.execute("DROP FUNCTION IF EXISTS ankt.reject_observation_value_update()")
    for table in reversed(_TABLES):
        op.execute(f"DROP TABLE IF EXISTS ankt.{table} CASCADE")
    op.execute("DROP SCHEMA IF EXISTS ankt CASCADE")
