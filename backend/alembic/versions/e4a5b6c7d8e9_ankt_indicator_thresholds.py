"""ankt indicators and thresholds valid over a range of reference periods

Revision ID: e4a5b6c7d8e9
Revises: d3f4a5b6c7d8
Create Date: 2026-09-16

A ceiling is not a property of an indicator, it is a property of an indicator
DURING A PLANNING PERIOD. NQ 23/2021/QH15 set the public-debt ceiling for
2021-2025; NQ 26/2026/QH16 sets it for 2026-2030. Judging 2025 against the 2026
resolution is the mistake TECH-DESIGN R3 says an LLM will certainly make if
left to compare numbers itself, and it is the one that destroys the business
value of the whole system: "exceeded the ceiling" said wrongly.

So validity is stored as a range over REFERENCE YEARS — the year the data is
about, never the day the question is asked — and two thresholds of the same
level for the same indicator are prevented from covering the same year by an
EXCLUDE constraint rather than by application code (ADR-T4). Overlapping rows
cannot be inserted by any writer: a bug in one ingestion path cannot leave the
catalog in a state where "which ceiling applies to 2025" has two answers.

`ankt.indicator` arrives here because a threshold needs something to hang from.
It is deliberately the minimum the compute path needs, NOT the full registry of
ECO-001.5 (taxonomy, versions, approval workflow) — that is its own piece of
work. `decimal_precision` in particular closes a real gap: it is the argument
Quantity.round_to has been taking from callers with nothing authoritative
behind it.

Verified by tests/integration/test_ankt_thresholds.py.
"""
from alembic import op

# Revision identifiers, used by Alembic.
revision = "e4a5b6c7d8e9"
down_revision = "d3f4a5b6c7d8"
branch_labels = None
depends_on = None


_TABLES = ("indicator", "threshold")


def upgrade() -> None:
    # btree_gist lets an EXCLUDE mix equality on scalar columns with overlap on
    # a range; without it the constraint below can only be expressed per-range,
    # which would forbid two indicators from sharing a period.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.execute(
        """
        CREATE TABLE ankt.indicator (
            indicator_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id         uuid NOT NULL,
            code              text NOT NULL,
            name              text NOT NULL,
            domain            text,
            sub_domain        text,
            unit              text NOT NULL
                              CHECK (unit IN ('money','percent','percentage_point',
                                              'ratio','persons','index')),
            unit_multiplier   smallint NOT NULL DEFAULT 0,
            currency          char(3),
            decimal_precision smallint NOT NULL DEFAULT 1,
            stock_or_flow     text NOT NULL
                              CHECK (stock_or_flow IN ('stock','flow','ratio')),
            frequency         text NOT NULL DEFAULT 'A'
                              CHECK (frequency IN ('A','Q','M')),
            created_at        timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT indicator_code_unique UNIQUE (tenant_id, code),
            CONSTRAINT indicator_money_has_currency
                CHECK ((unit = 'money') = (currency IS NOT NULL))
        )
        """,
    )

    # `operator` states the condition a COMPLIANT figure satisfies, so direction
    # lives here and `level` carries severity only. Diagram 05 sketches the
    # level as (warning | ceiling), but a floor — reserves, coverage — is a
    # limit too, and recording one as a "ceiling" would put a falsehood in the
    # data to satisfy a column name. Worth confirming when ECO-001.5 is revised.
    op.execute(
        """
        CREATE TABLE ankt.threshold (
            threshold_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          uuid NOT NULL,
            indicator_id       uuid NOT NULL REFERENCES ankt.indicator(indicator_id)
                               ON DELETE CASCADE,
            level              text NOT NULL CHECK (level IN ('warning','limit')),
            operator           text NOT NULL CHECK (operator IN ('<=','<','>=','>')),
            value              numeric(38, 6) NOT NULL,
            unit               text NOT NULL,
            unit_multiplier    smallint NOT NULL DEFAULT 0,
            currency           char(3),
            valid_from_year    integer NOT NULL,
            valid_to_year      integer,
            legal_basis_doc_id uuid REFERENCES ankt.source_document(doc_id),
            legal_basis_label  text,
            created_at         timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT threshold_years_ordered
                CHECK (valid_to_year IS NULL OR valid_to_year >= valid_from_year),
            -- Half-open so the generated range is comparable, but authored as
            -- inclusive years because that is how a resolution is written:
            -- "2021-2025" means through the end of 2025.
            applies_to int4range GENERATED ALWAYS AS (
                int4range(valid_from_year,
                          CASE WHEN valid_to_year IS NULL THEN NULL
                               ELSE valid_to_year + 1 END)
            ) STORED
        )
        """,
    )

    op.execute(
        """
        ALTER TABLE ankt.threshold ADD CONSTRAINT threshold_no_overlapping_validity
            EXCLUDE USING gist (
                tenant_id    WITH =,
                indicator_id WITH =,
                level        WITH =,
                applies_to   WITH &&
            )
        """,
    )

    op.execute(
        """
        CREATE INDEX threshold_indicator_validity
            ON ankt.threshold USING gist (indicator_id, applies_to)
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
