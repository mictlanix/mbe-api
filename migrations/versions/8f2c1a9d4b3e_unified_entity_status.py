"""Unified entity status

Replace boolean lifecycle flags (disabled/active/deactivated/enabled) with a single
non-nullable integer `status` column (0=ACTIVE, 1=INACTIVE, 2=ARCHIVED) on all
status-bearing tables. See specs/005-unified-entity-status/data-model.md.

Revision ID: 8f2c1a9d4b3e
Revises:
Create Date: 2026-07-19

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8f2c1a9d4b3e"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, legacy column) where legacy polarity is "disabled": NULL/0 -> ACTIVE, else INACTIVE
DISABLED_POLARITY = [
    ("user", "disabled"),
    ("customer", "disabled"),
    ("address", "disabled"),
    ("facility", "disabled"),
    ("warehouse", "disabled"),
    ("point_sale", "disabled"),
    ("cash_drawer", "disabled"),
    ("product", "deactivated"),
]

# (table, legacy column) where legacy polarity is "active": 1 -> ACTIVE, else INACTIVE
ACTIVE_POLARITY = [
    ("payment_method_option", "enabled"),
    ("vehicle", "active"),
    ("vehicle_operator", "active"),
    ("taxpayer_certificate", "active"),
]

ALL_TABLES = [t for t, _ in DISABLED_POLARITY] + [t for t, _ in ACTIVE_POLARITY] + ["employee"]


def _add_status(table: str) -> None:
    op.add_column(
        table,
        sa.Column("status", sa.SmallInteger(), nullable=False, server_default="0"),
    )


def upgrade() -> None:
    """Upgrade schema."""
    for table in ALL_TABLES:
        _add_status(table)

    for table, column in DISABLED_POLARITY:
        op.execute(
            f"UPDATE `{table}` SET status = "
            f"CASE WHEN `{column}` IS NULL OR `{column}` = 0 THEN 0 ELSE 1 END"
        )
        op.drop_column(table, column)

    for table, column in ACTIVE_POLARITY:
        op.execute(
            f"UPDATE `{table}` SET status = CASE WHEN `{column}` = 1 THEN 0 ELSE 1 END"
        )
        op.drop_column(table, column)

    # employee carries both flags: the restrictive one wins
    op.execute(
        "UPDATE employee SET status = "
        "CASE WHEN active = 1 AND (disabled IS NULL OR disabled = 0) THEN 0 ELSE 1 END"
    )
    op.drop_column("employee", "active")
    op.drop_column("employee", "disabled")


def downgrade() -> None:
    """Downgrade schema."""
    for table, column in DISABLED_POLARITY:
        nullable = table != "user"
        # warehouse.disabled was SmallInteger in the legacy schema, the rest Boolean
        col_type = sa.SmallInteger() if table == "warehouse" else sa.Boolean()
        op.add_column(
            table,
            sa.Column(column, col_type, nullable=nullable, server_default="0"),
        )
        op.execute(
            f"UPDATE `{table}` SET `{column}` = CASE WHEN status = 0 THEN 0 ELSE 1 END"
        )
        op.drop_column(table, "status")

    for table, column in ACTIVE_POLARITY:
        op.add_column(
            table,
            sa.Column(column, sa.Boolean(), nullable=False, server_default="1"),
        )
        op.execute(
            f"UPDATE `{table}` SET `{column}` = CASE WHEN status = 0 THEN 1 ELSE 0 END"
        )
        op.drop_column(table, "status")

    op.add_column(
        "employee", sa.Column("active", sa.Boolean(), nullable=False, server_default="1")
    )
    op.add_column("employee", sa.Column("disabled", sa.Boolean(), nullable=True))
    op.execute(
        "UPDATE employee SET active = CASE WHEN status = 0 THEN 1 ELSE 0 END, "
        "disabled = CASE WHEN status = 0 THEN 0 ELSE 1 END"
    )
    op.drop_column("employee", "status")
