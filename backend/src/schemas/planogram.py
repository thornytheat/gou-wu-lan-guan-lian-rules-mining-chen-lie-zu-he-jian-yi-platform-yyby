"""陈列建议请求模型。"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class GeneratePlanRequest(BaseModel):
    run_id: str | None = None


class SlotUpdateRequest(BaseModel):
    status: Literal["draft", "published", "archived"]
    note: str = Field(default="", max_length=400)


class ScheduleWindowRequest(BaseModel):
    """排期窗口：两个都缺省 = 逐建议位沿用现有占用（首次发布默认明天起 14 天）。"""

    effective_date: date | None = None
    end_date: date | None = None
