"""排期占用纯函数：按生效/结束日期占用货架位，跨建议单冲突检测。

只吃 list[dict] / datetime.date，不 import Session / 模型 / 数据库配置，便于单元测试。
行（row）统一为 dict，字段约定：
  slot_key / plan_code / conflict_group / effective_date(date) / end_date(date) / status
  其余展示字段（zone / shelf_label / rule_label …）原样透传。

口径（与 schedule_service 完全一致，后者只负责读写库）：
  阶段推导：replaced 落库状态优先；生效日期 > 今天 → 待生效；结束日期 < 今天 → 已结束；其余 → 生效中
  调整语义：已生效/已结束的占用保持原样（keep）；待生效占用才可被替换（replace）或释放（release）
  幂等：同 slot_key 且生效/结束日期都相同的待生效占用 → 原样保留（unchanged），不重复占位
  冲突：同一货架位（conflict_group）上，不同建议单的占用日期区间相交（闭区间）→ 冲突组合
"""
from __future__ import annotations

from datetime import date

from src.constants.enums import SchedulePhase, ScheduleStatus


def overlaps(a_start: date, a_end: date, b_start: date, b_end: date) -> bool:
    """闭区间相交判定：端点相接（如 11-16 与 11-16）也算占用冲突。"""
    return a_start <= b_end and b_start <= a_end


def phase_of(status: str, effective_date: date, end_date: date, today: date) -> str:
    """由 落库状态 + 日期窗口 推导展示阶段（不落库）。"""
    if status == ScheduleStatus.REPLACED.value:
        return SchedulePhase.REPLACED.value
    if effective_date > today:
        return SchedulePhase.PENDING.value
    if end_date < today:
        return SchedulePhase.DONE.value
    return SchedulePhase.ACTIVE.value


def with_phase(row: dict, today: date) -> dict:
    """返回附带 phase 字段的浅拷贝（展示用）。"""
    return {
        **row,
        "phase": phase_of(row["status"], row["effective_date"], row["end_date"], today),
    }


def plan_schedule(existing: list[dict], desired: list[dict], today: date) -> dict:
    """把一份建议单的「现有有效占用」调成「期望占用」，只动尚未生效的部分。

    existing: 本计划当前 status=scheduled 的占用行（可空）
    desired:  本计划全部在位建议位期望的占用行（每建议位一条）
    返回 {keep, unchanged, replace, create, release, final}：
      keep      已生效/已结束 → 保持原样（期望行被忽略）
      unchanged 待生效且日期一致 → 幂等命中，原样保留
      replace   待生效但日期变化 → [{old, new}]，old 置 replaced，new 落库
      create    无现有占用 → 新增
      release   建议位已下线（不在 desired）的待生效占用 → 置 replaced 释放货架位
      final     应用后本计划的有效占用（keep + unchanged + replace.new + create）
    """
    kept_by_slot: dict[str, dict] = {}
    pending_by_slot: dict[str, dict] = {}
    for row in existing:
        if row["status"] != ScheduleStatus.SCHEDULED.value:
            continue
        phase = phase_of(row["status"], row["effective_date"], row["end_date"], today)
        if phase == SchedulePhase.PENDING.value:
            pending_by_slot[row["slot_key"]] = row
        else:
            kept_by_slot[row["slot_key"]] = row

    # 已生效/已结束的占用无条件保持原样（与期望集无关，绝不进入替换/释放）
    keep: list[dict] = list(kept_by_slot.values())
    unchanged: list[dict] = []
    replace: list[dict] = []
    create: list[dict] = []
    desired_slots: set[str] = set()
    for want in desired:
        slot_key = want["slot_key"]
        desired_slots.add(slot_key)
        if slot_key in kept_by_slot:
            continue  # 已生效：期望行被忽略，占用保持原样
        current = pending_by_slot.get(slot_key)
        if current is None:
            create.append(want)
        elif (
            current["effective_date"] == want["effective_date"]
            and current["end_date"] == want["end_date"]
        ):
            unchanged.append(current)
        else:
            replace.append({"old": current, "new": want})

    release = [row for slot_key, row in pending_by_slot.items() if slot_key not in desired_slots]
    final = keep + unchanged + [item["new"] for item in replace] + create
    return {
        "keep": keep,
        "unchanged": unchanged,
        "replace": replace,
        "create": create,
        "release": release,
        "final": final,
    }


def find_conflicts(rows: list[dict], involve: str | None = None) -> list[dict]:
    """跨建议单冲突组合：同一货架位上不同 plan 的有效占用日期区间相交。

    rows: 参与检查的有效占用行（status != scheduled 的行会被忽略）
    involve: 只返回涉及该 plan_code 的冲突（发布前核对用）；None 返回全部（排期总览用）
    返回按 (货架位, 建议单对) 排序的冲突列表，每条含相交窗口 [overlap_start, overlap_end]。
    """
    by_group: dict[str, list[dict]] = {}
    for row in rows:
        if row["status"] != ScheduleStatus.SCHEDULED.value:
            continue
        by_group.setdefault(row["conflict_group"], []).append(row)

    conflicts: list[dict] = []
    for group in sorted(by_group):
        members = sorted(
            by_group[group],
            key=lambda r: (r["effective_date"], r["end_date"], r["plan_code"], r["slot_key"]),
        )
        for index, left in enumerate(members):
            for right in members[index + 1 :]:
                if left["plan_code"] == right["plan_code"]:
                    continue
                if not overlaps(
                    left["effective_date"], left["end_date"],
                    right["effective_date"], right["end_date"],
                ):
                    continue
                plans = sorted({left["plan_code"], right["plan_code"]})
                if involve is not None and involve not in plans:
                    continue
                conflicts.append(
                    {
                        "conflict_group": group,
                        "zone": left.get("zone") or right.get("zone") or "",
                        "plans": plans,
                        "rows": sorted(
                            [left, right], key=lambda r: (r["plan_code"], r["slot_key"])
                        ),
                        "overlap_start": max(left["effective_date"], right["effective_date"]),
                        "overlap_end": min(left["end_date"], right["end_date"]),
                    }
                )
    return conflicts


def available_positions(groups: list[str], rows: list[dict], win_from: date, win_to: date) -> list[str]:
    """窗口内可用位：groups 里没有任何有效占用与 [win_from, win_to] 相交的货架位。"""
    busy = {
        row["conflict_group"]
        for row in rows
        if row["status"] == ScheduleStatus.SCHEDULED.value
        and overlaps(row["effective_date"], row["end_date"], win_from, win_to)
    }
    return [group for group in groups if group not in busy]
