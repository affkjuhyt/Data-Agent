"""mcp_api_key_ref_is_a_secret_id

"MCP".api_key_ref was sent to the MCP server as the bearer token itself, so the
token sat in Postgres in plaintext. config_loader now treats it as the
secrets_vault id of an MCP_TOKEN secret and reads the token from OpenBao, like
the warehouse and DB connector passwords. Only the column comment changes:
"MCP" has 0 rows on the dev cluster (02/10/2026) and no API writes it. A row
holding a raw token elsewhere stops authenticating; move the token into the
Secrets Vault as MCP_TOKEN, put its id here, and the plaintext is gone.

Revision ID: 7a3c9e1f5b2d
Revises: 6b2d8f4a1c7e
Create Date: 2026-10-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "7a3c9e1f5b2d"
down_revision: str | None = "6b2d8f4a1c7e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        'COMMENT ON COLUMN "MCP".api_key_ref IS '
        "'secrets_vault.id of an MCP_TOKEN secret; config_loader reads the bearer token "
        "from OpenBao. Never the token itself.'"
    )


def downgrade() -> None:
    op.execute(
        'COMMENT ON COLUMN "MCP".api_key_ref IS '
        "'Despite the name, the bearer token itself: config_loader sends it as auth_token.'"
    )
