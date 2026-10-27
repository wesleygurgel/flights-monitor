"""prices_since nas rotas

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-26 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('routes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('prices_since', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('routes', schema=None) as batch_op:
        batch_op.drop_column('prices_since')
