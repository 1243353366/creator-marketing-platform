from alembic import op
import sqlalchemy as sa
"""extend review status lifecycle

Review-only migration: widen the status check to include the
controlled experiment lifecycle (testing, released, rolled_back).
A released change always passes through testing first; rollback is
reachable from both testing and released states.
"""
revision = "c1fe2a9d01"
down_revision = "b744bba564a5"
branch_labels = None
depends_on = None

OLD = ("ck_review_status")
NEW_STATES = ("proposed", "approved", "rejected", "testing",
              "released", "rolled_back")


def upgrade():
    op.execute(
        "ALTER TABLE monthly_reviews "
        "DROP CONSTRAINT IF EXISTS ck_review_status")
    op.execute(
        "ALTER TABLE monthly_reviews "
        "ADD CONSTRAINT ck_review_status CHECK (status IN "
        "('proposed', 'approved', 'rejected', 'testing', "
        "'released', 'rolled_back'))")


def downgrade():
    op.execute(
        "ALTER TABLE monthly_reviews "
        "DROP CONSTRAINT IF EXISTS ck_review_status")
    op.execute(
        "ALTER TABLE monthly_reviews "
        "ADD CONSTRAINT ck_review_status CHECK (status IN "
        "('proposed', 'approved', 'rejected'))")
