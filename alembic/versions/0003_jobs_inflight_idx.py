"""jobs index for in-flight cap COUNT

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-25

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The per-key in-flight cap (POST /v1/extracts) runs:
    #   SELECT count(*) FROM jobs WHERE api_key_id = ? AND status IN (...)
    # This index makes it index-only.
    op.create_index("ix_jobs_key_status", "jobs", ["api_key_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_jobs_key_status", table_name="jobs")
