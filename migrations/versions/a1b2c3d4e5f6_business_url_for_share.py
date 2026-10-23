"""brand profiles carry a place-of-business link for sharing

Revision ID: a1b2c3d4e5f6
Revises: '0eead6675a90'
Create Date: 2026-10-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "0eead6675a90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("brand_profiles",
                  sa.Column("business_url", sa.String(length=500),
                            nullable=True))


def downgrade() -> None:
    op.drop_column("brand_profiles", "business_url")
