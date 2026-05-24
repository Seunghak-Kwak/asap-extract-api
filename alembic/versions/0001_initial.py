"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("key_id", sa.String(32), nullable=False, unique=True),
        sa.Column("secret_hash", sa.Text, nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("disabled_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_api_keys_key_id", "api_keys", ["key_id"], unique=True)

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "api_key_id",
            sa.BigInteger,
            sa.ForeignKey("api_keys.id"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("dataset", sa.String(64), nullable=False),
        sa.Column("filters", postgresql.JSONB, nullable=False),
        sa.Column("format", sa.String(16), nullable=False, server_default="csv"),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column(
            "cancel_requested",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("row_count", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("bytes", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("file_sha256", sa.String(64)),
        sa.Column("error_class", sa.String(128)),
        sa.Column("error_message", sa.Text),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("api_key_id", "idempotency_key", name="uq_jobs_idem"),
    )
    op.create_index("ix_jobs_status_created", "jobs", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_jobs_status_created", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_api_keys_key_id", table_name="api_keys")
    op.drop_table("api_keys")
