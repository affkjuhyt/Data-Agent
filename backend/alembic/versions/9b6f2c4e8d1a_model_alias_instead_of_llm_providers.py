"""model_alias_instead_of_llm_providers

"LLMProviders" was a second registry next to the LiteLLM gateway: only seeds
wrote it, the model picker lists LiteLLM's aliases, and the only column ever
read was model_id. The two disagreed — the picker sends a LiteLLM alias, the
API cast it to a UUID for AgentVersions.responder_model_id, and choosing a model
answered 500. An agent whose responder_model_id was NULL also vanished, because
config_loader inner-joined the table.

AgentVersions.responder_model and Guardrails.guardrail_model now hold the
LiteLLM alias itself, backfilled from the provider row each pointed at (its
"openai/" SDK prefix dropped — the runtime adds it), and the table goes.

Downgrade rebuilds "LLMProviders" from the aliases still in use (one row each,
name = alias, endpoint_url empty) and points the id columns back at them; rows
no version or guardrail referenced are not restored.

Revision ID: 9b6f2c4e8d1a
Revises: 0c7e5a3b9f2d
Create Date: 2026-10-02
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "9b6f2c4e8d1a"
down_revision: str | None = "0c7e5a3b9f2d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RESPONDER_COMMENT = (
    "LiteLLM model alias (as GET /api/llm-providers lists it, e.g. responder) the "
    "agent answers with; NULL = the deployment default."
)
_GUARDRAIL_COMMENT = "LiteLLM model alias the guardrail LLM check calls; NULL = guardrail."

# (table, alias column, old id column, old FK name)
_LINKS = (
    (
        "AgentVersions",
        "responder_model",
        "responder_model_id",
        "AgentVersions_responder_model_id_fkey",
    ),
    ("Guardrails", "guardrail_model", "guardrail_model_id", "Guardrails_guardrail_model_id_fkey"),
)


def upgrade() -> None:
    for table, alias_col, id_col, _ in _LINKS:
        op.add_column(table, sa.Column(alias_col, sa.String(length=255), nullable=True))
        op.execute(
            f"UPDATE \"{table}\" t SET {alias_col} = regexp_replace(p.model_id, '^openai/', '') "
            f'FROM "LLMProviders" p WHERE p.id = t.{id_col}'
        )
        # Dropping the column drops its FK constraint with it.
        op.drop_column(table, id_col)
    op.execute(f"COMMENT ON COLUMN \"AgentVersions\".responder_model IS '{_RESPONDER_COMMENT}'")
    op.execute(f"COMMENT ON COLUMN \"Guardrails\".guardrail_model IS '{_GUARDRAIL_COMMENT}'")
    op.drop_table("LLMProviders")


def downgrade() -> None:
    op.create_table(
        "LLMProviders",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column("endpoint_url", sa.Text(), nullable=False),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "inserted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_llmproviders_inserted_at_desc",
        "LLMProviders",
        [sa.literal_column("inserted_at DESC")],
        unique=False,
    )
    op.create_index(
        "idx_llmproviders_type_is_default",
        "LLMProviders",
        ["type", "is_default"],
        unique=False,
        postgresql_where=sa.text("is_default = true"),
    )
    op.execute(
        """
        INSERT INTO "LLMProviders" (id, name, model_id, endpoint_url, type)
        SELECT gen_random_uuid(), alias, 'openai/' || alias, '', 'chat'
        FROM (
            SELECT responder_model AS alias FROM "AgentVersions" WHERE responder_model IS NOT NULL
            UNION
            SELECT guardrail_model FROM "Guardrails" WHERE guardrail_model IS NOT NULL
        ) aliases
        """
    )
    for table, alias_col, id_col, fk_name in _LINKS:
        op.add_column(table, sa.Column(id_col, sa.UUID(), nullable=True))
        op.create_foreign_key(fk_name, table, "LLMProviders", [id_col], ["id"])
        op.execute(
            f'UPDATE "{table}" t SET {id_col} = p.id '
            f'FROM "LLMProviders" p WHERE p.name = t.{alias_col}'
        )
        op.drop_column(table, alias_col)
