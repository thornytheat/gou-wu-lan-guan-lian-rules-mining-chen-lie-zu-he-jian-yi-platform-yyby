"""排期占用：按生效/结束日期占用货架位（只替换未生效、幂等重跑、跨建议单冲突）。

固定「今天」= 2026-11-14（与演示剧本 W45 对齐）。候选窗口取自 seed 主流程：
W44 样板计划占用端架 A1（11-03 ~ 11-16，生效中），新计划默认窗口 11-15 ~ 11-28。
"""
from __future__ import annotations

from datetime import date

import pytest

from src.constants.enums import SchedulePhase, ScheduleStatus
from src.services.schedule_core import (
    available_positions,
    find_conflicts,
    overlaps,
    phase_of,
    plan_schedule,
)

TODAY = date(2026, 11, 14)
SCHEDULED = ScheduleStatus.SCHEDULED.value
REPLACED = ScheduleStatus.REPLACED.value


def row(
    slot_key: str,
    plan: str = "PLAN-B",
    group: str = "A1",
    eff: date = date(2026, 11, 15),
    end: date = date(2026, 11, 28),
    status: str = SCHEDULED,
    row_id: str | None = None,
) -> dict:
    return {
        "id": row_id or f"id-{slot_key}",
        "slot_key": slot_key,
        "plan_code": plan,
        "conflict_group": group,
        "zone": f"zone-{group}",
        "shelf_label": f"shelf-{group}",
        "rule_label": f"rule-{slot_key}",
        "effective_date": eff,
        "end_date": end,
        "status": status,
    }


# ------------------------------------------------------------------ 阶段推导


def test_phase_of_derives_from_dates_and_status() -> None:
    assert phase_of(SCHEDULED, date(2026, 11, 15), date(2026, 11, 28), TODAY) == SchedulePhase.PENDING.value
    assert phase_of(SCHEDULED, date(2026, 11, 3), date(2026, 11, 16), TODAY) == SchedulePhase.ACTIVE.value
    assert phase_of(SCHEDULED, date(2026, 11, 14), date(2026, 11, 14), TODAY) == SchedulePhase.ACTIVE.value
    assert phase_of(SCHEDULED, date(2026, 10, 20), date(2026, 11, 10), TODAY) == SchedulePhase.DONE.value
    assert phase_of(REPLACED, date(2026, 11, 15), date(2026, 11, 28), TODAY) == SchedulePhase.REPLACED.value


def test_overlaps_closed_interval_boundaries() -> None:
    assert overlaps(date(2026, 11, 3), date(2026, 11, 16), date(2026, 11, 15), date(2026, 11, 28))
    # 端点相接也算冲突：11-16 当天两边都占用
    assert overlaps(date(2026, 11, 3), date(2026, 11, 16), date(2026, 11, 16), date(2026, 11, 30))
    assert not overlaps(date(2026, 11, 3), date(2026, 11, 16), date(2026, 11, 17), date(2026, 11, 30))
    assert not overlaps(date(2026, 11, 17), date(2026, 11, 30), date(2026, 11, 3), date(2026, 11, 16))


# ------------------------------------------------------------------ 调整方案


def test_plan_schedule_first_publish_creates_all() -> None:
    desired = [row("S1", group="A1"), row("S2", group="H3")]
    plan = plan_schedule([], desired, TODAY)

    assert plan["keep"] == plan["unchanged"] == plan["replace"] == plan["release"] == []
    assert [r["slot_key"] for r in plan["create"]] == ["S1", "S2"]
    assert [r["slot_key"] for r in plan["final"]] == ["S1", "S2"]


def test_plan_schedule_rerun_same_publish_is_idempotent() -> None:
    """同一发布重跑：现有占用与期望完全一致 → 全部 unchanged，不重复占位。"""
    desired = [row("S1", group="A1"), row("S2", group="H3")]
    first = plan_schedule([], desired, TODAY)
    second = plan_schedule(first["final"], desired, TODAY)

    assert second["create"] == second["replace"] == second["release"] == second["keep"] == []
    assert [r["slot_key"] for r in second["unchanged"]] == ["S1", "S2"]
    assert len(second["final"]) == 2  # 没有重复占用
    # 第三次重跑依旧幂等
    third = plan_schedule(second["final"], desired, TODAY)
    assert third["create"] == third["replace"] == []
    assert len(third["final"]) == 2


def test_plan_schedule_adjust_replaces_only_pending() -> None:
    """调整方案：生效中的占用保持原样，只有待生效的占用被替换。"""
    active = row("S1", group="A1", eff=date(2026, 11, 3), end=date(2026, 11, 16))
    pending = row("S2", group="H3", eff=date(2026, 11, 15), end=date(2026, 11, 28))
    desired = [
        row("S1", group="A1", eff=date(2026, 11, 20), end=date(2026, 12, 3)),  # 已生效 → 忽略新日期
        row("S2", group="H3", eff=date(2026, 11, 20), end=date(2026, 12, 3)),  # 待生效 → 替换
    ]
    plan = plan_schedule([active, pending], desired, TODAY)

    assert [r["slot_key"] for r in plan["keep"]] == ["S1"]
    assert plan["keep"][0]["effective_date"] == date(2026, 11, 3)  # 已执行部分保持原样
    assert plan["create"] == plan["unchanged"] == []
    assert len(plan["replace"]) == 1
    assert plan["replace"][0]["old"]["slot_key"] == "S2"
    assert plan["replace"][0]["old"]["effective_date"] == date(2026, 11, 15)
    assert plan["replace"][0]["new"]["effective_date"] == date(2026, 11, 20)
    final_by_slot = {r["slot_key"]: r for r in plan["final"]}
    assert final_by_slot["S1"]["effective_date"] == date(2026, 11, 3)
    assert final_by_slot["S2"]["effective_date"] == date(2026, 11, 20)


def test_plan_schedule_done_occupancy_is_kept_as_history() -> None:
    """已结束的占用同样保持原样（已执行部分），不会被新窗口替换。"""
    done = row("S1", group="A1", eff=date(2026, 10, 20), end=date(2026, 11, 10))
    desired = [row("S1", group="A1", eff=date(2026, 11, 15), end=date(2026, 11, 28))]
    plan = plan_schedule([done], desired, TODAY)

    assert [r["slot_key"] for r in plan["keep"]] == ["S1"]
    assert plan["replace"] == plan["create"] == []
    assert plan["final"][0]["end_date"] == date(2026, 11, 10)


def test_plan_schedule_releases_pending_rows_of_offline_slots() -> None:
    """建议位下线（不在期望集）：其待生效占用被释放；已生效的仍保持原样。"""
    pending = row("S1", group="A1")
    active = row("S2", group="H3", eff=date(2026, 11, 3), end=date(2026, 11, 16))
    plan = plan_schedule([pending, active], [], TODAY)

    assert [r["slot_key"] for r in plan["release"]] == ["S1"]
    assert [r["slot_key"] for r in plan["keep"]] == ["S2"]
    assert [r["slot_key"] for r in plan["final"]] == ["S2"]


def test_plan_schedule_ignores_replaced_rows() -> None:
    """已替换的旧版占用不参与调整（保留可查，但不挡新占用）。"""
    stale = row("S1", group="A1", status=REPLACED)
    desired = [row("S1", group="A1")]
    plan = plan_schedule([stale], desired, TODAY)

    assert plan["keep"] == plan["unchanged"] == plan["replace"] == []
    assert [r["slot_key"] for r in plan["create"]] == ["S1"]


# ------------------------------------------------------------------ 冲突检测


def test_find_conflicts_cross_plan_overlap_on_same_position() -> None:
    """跨建议单：同一货架位日期区间相交 → 冲突组合，含相交窗口。"""
    mine = row("S1", plan="PLAN-W45", group="A1", eff=date(2026, 11, 15), end=date(2026, 11, 28))
    theirs = row("S9", plan="PLAN-W44", group="A1", eff=date(2026, 11, 3), end=date(2026, 11, 16))
    conflicts = find_conflicts([mine, theirs])

    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict["conflict_group"] == "A1"
    assert conflict["plans"] == ["PLAN-W44", "PLAN-W45"]
    assert conflict["overlap_start"] == date(2026, 11, 15)
    assert conflict["overlap_end"] == date(2026, 11, 16)
    assert {r["plan_code"] for r in conflict["rows"]} == {"PLAN-W44", "PLAN-W45"}


def test_find_conflicts_ignores_same_plan_and_disjoint_windows() -> None:
    same_plan = [
        row("S1", plan="PLAN-A", group="A1", eff=date(2026, 11, 3), end=date(2026, 11, 16)),
        row("S2", plan="PLAN-A", group="A1", eff=date(2026, 11, 10), end=date(2026, 11, 28)),
    ]
    assert find_conflicts(same_plan) == []

    disjoint = [
        row("S1", plan="PLAN-A", group="A1", eff=date(2026, 11, 3), end=date(2026, 11, 16)),
        row("S2", plan="PLAN-B", group="A1", eff=date(2026, 11, 17), end=date(2026, 11, 30)),
    ]
    assert find_conflicts(disjoint) == []

    different_groups = [
        row("S1", plan="PLAN-A", group="A1"),
        row("S2", plan="PLAN-B", group="H3"),
    ]
    assert find_conflicts(different_groups) == []


def test_find_conflicts_skips_replaced_and_filters_involve() -> None:
    mine = row("S1", plan="PLAN-W45", group="A1")
    stale = row("S9", plan="PLAN-W44", group="A1", status=REPLACED)
    assert find_conflicts([mine, stale]) == []

    other_pair = [
        row("S2", plan="PLAN-X", group="H3"),
        row("S3", plan="PLAN-Y", group="H3"),
    ]
    assert len(find_conflicts([mine, *other_pair])) == 1
    # involve 只保留涉及本计划的冲突
    assert find_conflicts([mine, *other_pair], involve="PLAN-W45") == []
    assert len(find_conflicts([mine, *other_pair], involve="PLAN-X")) == 1


def test_find_conflicts_deterministic_for_identical_inputs() -> None:
    rows = [
        row("S1", plan="PLAN-B", group="H3"),
        row("S2", plan="PLAN-A", group="A1", eff=date(2026, 11, 10), end=date(2026, 11, 20)),
        row("S3", plan="PLAN-C", group="A1", eff=date(2026, 11, 12), end=date(2026, 11, 22)),
        row("S4", plan="PLAN-D", group="H3", eff=date(2026, 11, 20), end=date(2026, 12, 1)),
    ]
    first = find_conflicts(rows)
    assert find_conflicts(rows) == first
    assert [(c["conflict_group"], c["plans"]) for c in first] == [
        ("A1", ["PLAN-A", "PLAN-C"]),
        ("H3", ["PLAN-B", "PLAN-D"]),
    ]


# ------------------------------------------------------------------ 可用位


def test_available_positions_excludes_positions_busy_in_window() -> None:
    groups = ["A1", "H3", "H4"]
    rows = [
        row("S1", plan="PLAN-A", group="A1", eff=date(2026, 11, 3), end=date(2026, 11, 16)),
        row("S2", plan="PLAN-B", group="H3", eff=date(2026, 12, 1), end=date(2026, 12, 14)),
        row("S3", plan="PLAN-C", group="H4", status=REPLACED),
    ]
    # 窗口 11-15 ~ 11-28：A1 被占（11-15/16 相交），H3 占用在 12 月不相交，H4 已替换不算
    assert available_positions(groups, rows, date(2026, 11, 15), date(2026, 11, 28)) == ["H3", "H4"]
    # 窗口移到 12 月：H3 被占，A1 空出
    assert available_positions(groups, rows, date(2026, 12, 1), date(2026, 12, 14)) == ["A1", "H4"]


# ------------------------------------------------------------------ 不变量


def test_plan_schedule_never_touches_executed_occupancy() -> None:
    """不变量：无论期望怎么变，已生效/已结束的占用都原样出现在最终结果里。"""
    executed = [
        row("S1", group="A1", eff=date(2026, 11, 3), end=date(2026, 11, 16)),   # 生效中
        row("S2", group="H3", eff=date(2026, 10, 20), end=date(2026, 11, 10)),  # 已结束
    ]
    variants = [
        [],
        [row("S1", group="A1", eff=date(2026, 12, 1), end=date(2026, 12, 14))],
        [row("S9", group="H4")],
        [row("S1", group="A1"), row("S2", group="H3"), row("S9", group="H4")],
    ]
    for desired in variants:
        plan = plan_schedule(executed, desired, TODAY)
        final_by_slot = {r["slot_key"]: r for r in plan["final"]}
        for original in executed:
            assert final_by_slot[original["slot_key"]] == original
        assert plan["release"] == []  # 已执行的占用永不进入释放列表


def test_plan_schedule_final_has_no_duplicate_slots() -> None:
    """不变量：最终结果里同一建议位至多一条占用（不重复占位）。"""
    existing = [row("S1", group="A1"), row("S2", group="H3", eff=date(2026, 11, 3), end=date(2026, 11, 16))]
    desired = [row("S1", group="A1"), row("S2", group="H3"), row("S3", group="H4")]
    plan = plan_schedule(existing, desired, TODAY)
    slot_keys = [r["slot_key"] for r in plan["final"]]
    assert len(slot_keys) == len(set(slot_keys))
    assert sorted(slot_keys) == ["S1", "S2", "S3"]
