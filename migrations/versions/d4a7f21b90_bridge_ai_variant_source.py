"""bridge-ai is a legal variant source

The Workers bridge generates variants on Cloudflare's edge
(Workers AI with honest labeling); the schema lock correctly
refused the new source until the allowlist is widened here.
"""
import sqlalchemy as sa
from alembic import op

revision = "d4a7f21b90"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_variant_source", "variants",
                      type_="check")
    op.create_check_constraint(
        "ck_variant_source", "variants",
        "source IN ('provider', 'template', 'bridge-ai')")
    # ck_gen_status always allowed provider_unavailable (20
    # chars); the column was too narrow to hold it
    op.alter_column(
        "generation_logs", "status",
        existing_type=sa.String(16),
        type_=sa.String(32),
        existing_nullable=False)


def downgrade():
    op.execute(
        "DELETE FROM generation_logs WHERE "
        "length(status) > 16")
    op.alter_column(
        "generation_logs", "status",
        existing_type=sa.String(32),
        type_=sa.String(16),
        existing_nullable=False)
    op.execute(
        "DELETE FROM variants WHERE source = 'bridge-ai'")
    op.drop_constraint("ck_variant_source", "variants",
                       type_="check")
    op.create_check_constraint(
        "ck_variant_source", "variants",
        "source IN ('provider', 'template')")
