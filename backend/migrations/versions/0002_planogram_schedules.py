"""货架位排期占用表（演示环境实际由 scripts/bootstrap_db.py 建表）。

Revision ID: 0002_planogram_schedules
Revises: 0001_initial
Create Date: 2026-11-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_planogram_schedules"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

SCHEMA = "basketrule"


def upgrade() -> None:
    op.create_table(
        "planogram_schedules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("schedule_key", sa.String(220), nullable=False, unique=True),
        sa.Column("plan_code", sa.String(40), nullable=False),
        sa.Column(
            "slot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.planogram_slots.id"),
            nullable=False,
        ),
        sa.Column("slot_key", sa.String(140), nullable=False),
        sa.Column("conflict_group", sa.String(80), nullable=False, server_default=""),
        sa.Column("zone", sa.String(80), nullable=False, server_default=""),
        sa.Column("shelf_label", sa.String(80), nullable=False, server_default=""),
        sa.Column("rule_label", sa.String(200), nullable=False, server_default=""),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="scheduled"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )
    # 不变量兜底：一个建议位同一时刻最多一条「有效占用」→ 同一发布重跑不会重复占位
    op.create_index(
        "uq_planogram_schedules_live_slot",
        "planogram_schedules",
        ["slot_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'scheduled'"),
    )
    op.create_index(
        "ix_planogram_schedules_group_dates",
        "planogram_schedules",
        ["conflict_group", "effective_date", "end_date"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("planogram_schedules", schema=SCHEMA)
