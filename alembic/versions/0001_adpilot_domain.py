"""Create the initial AdPilot relational domain."""
from alembic import op

from src.adpilot.db import Base
from src.adpilot import models  # noqa: F401

revision = "0001_adpilot_domain"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(bind=op.get_bind())


def downgrade():
    Base.metadata.drop_all(bind=op.get_bind())
