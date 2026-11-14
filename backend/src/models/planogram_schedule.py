"""货架位排期占用（按生效/结束日期占用货架位，跨建议单可核对）。

不变量：
  - 一个建议位（slot）同一时刻最多一条「有效占用」（status=scheduled），
    由部分唯一索引 uq_planogram_schedules_live_slot 兜底 → 同一发布重跑不会重复占位。
  - 调整只替换待生效占用（effective_date > today）；已生效/已结束的占用保持原样。
  - 被替换的旧版占用保留为 status=replaced，切换失败整体回滚后旧版排期不变。
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.config.settings import settings
from src.models.base import Base, TimestampMixin, new_uuid


class PlanogramSchedule(Base, TimestampMixin):
    __tablename__ = "planogram_schedules"
    __table_args__ = (
        Index(
            "uq_planogram_schedules_live_slot",
            "slot_id",
            unique=True,
            postgresql_where="status = 'scheduled'",
        ),
        Index("ix_planogram_schedules_group_dates", "conflict_group", "effective_date", "end_date"),
        {"schema": settings.db_schema},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    # 幂等自然键：{slot_key}@{生效日期}（同键已存在时追加 #2/#3…，重跑同一发布命中同键即跳过）
    schedule_key: Mapped[str] = mapped_column(String(220), unique=True, nullable=False, index=True)
    plan_code: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    slot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.planogram_slots.id"), nullable=False
    )
    slot_key: Mapped[str] = mapped_column(String(140), nullable=False)
    conflict_group: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    zone: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    shelf_label: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    rule_label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled", index=True)
