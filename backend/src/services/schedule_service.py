"""排期占用 DB 层：发布前可核对的排期（预览）+ 原子切换（应用）。

口径与 schedule_core 完全一致（纯函数），本模块只负责：
  读库 → 组装 dict → 调 core → 序列化（preview / overview）
  或 → 落库（apply：替换待生效占用、插入新占用，全程单事务，失败回滚保留旧版排期）

不变量兜底：planogram_schedules 上「每建议位最多一条有效占用」部分唯一索引，
同一发布重跑命中同 schedule_key 即 unchanged，不会重复占用货架位。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from src.constants import error_codes as codes
from src.constants.enums import ScheduleStatus, SlotStatus
from src.models.planogram_schedule import PlanogramSchedule
from src.models.planogram_slot import PlanogramSlot
from src.models.rule_candidate import RuleCandidate
from src.services import basket_service, fixtures_loader
from src.services.planogram_core import ZONE_ORDER
from src.services.schedule_core import (
    available_positions,
    find_conflicts,
    overlaps,
    plan_schedule,
    with_phase,
)
from src.utils.errors import AppError

DEFAULT_WINDOW_DAYS = 14  # 默认档期：生效日期起 14 天


def _today() -> date:
    return datetime.now(timezone.utc).date()


def resolve_window(effective_date: date | None, end_date: date | None) -> tuple[date, date] | None:
    """显式排期窗口；两个都缺省返回 None（逐建议位沿用现有占用窗口，重跑即幂等）。"""
    if effective_date is None and end_date is None:
        return None
    eff = effective_date or (_today() + timedelta(days=1))
    end = end_date or (eff + timedelta(days=DEFAULT_WINDOW_DAYS - 1))
    if end < eff:
        raise AppError(codes.VALIDATION_ERROR, "结束日期不能早于生效日期", f"{eff} > {end}")
    return eff, end


# ---------------------------------------------------------------- 序列化 ----


def _to_dict(row: PlanogramSchedule) -> dict:
    return {
        "id": str(row.id),
        "schedule_key": row.schedule_key,
        "plan_code": row.plan_code,
        "slot_id": str(row.slot_id),
        "slot_key": row.slot_key,
        "conflict_group": row.conflict_group,
        "zone": row.zone,
        "shelf_label": row.shelf_label,
        "rule_label": row.rule_label,
        "effective_date": row.effective_date,
        "end_date": row.end_date,
        "status": row.status,
    }


def _serialize(row: dict, today: date) -> dict:
    phased = with_phase(row, today)
    return {
        **phased,
        "effective_date": row["effective_date"].isoformat(),
        "end_date": row["end_date"].isoformat(),
    }


def _serialize_conflict(conflict: dict, today: date) -> dict:
    return {
        "conflict_group": conflict["conflict_group"],
        "zone": conflict["zone"],
        "plans": conflict["plans"],
        "rows": [_serialize(row, today) for row in conflict["rows"]],
        "overlap_start": conflict["overlap_start"].isoformat(),
        "overlap_end": conflict["overlap_end"].isoformat(),
    }


# ---------------------------------------------------------------- 读取 ----


def _live_rows(db: Session, plan_code: str | None = None) -> list[PlanogramSchedule]:
    stmt = select(PlanogramSchedule).where(PlanogramSchedule.status == ScheduleStatus.SCHEDULED.value)
    if plan_code is not None:
        stmt = stmt.where(PlanogramSchedule.plan_code == plan_code)
    return list(db.execute(stmt.order_by(PlanogramSchedule.plan_code, PlanogramSchedule.slot_key)).scalars())


def _desired_rows(
    db: Session,
    slots: list[PlanogramSlot],
    existing_by_slot: dict[str, dict],
    window: tuple[date, date] | None,
    today: date,
) -> list[dict]:
    """每个在位建议位期望的一条占用：显式窗口优先，否则沿用现有占用，再否则默认明天起 14 天。"""
    names, _ = basket_service.sku_maps(db)
    rules = {
        row.id: row
        for row in db.execute(
            select(RuleCandidate).where(RuleCandidate.id.in_({slot.rule_id for slot in slots}))
        ).scalars()
    }
    default_eff = today + timedelta(days=1)
    default_end = default_eff + timedelta(days=DEFAULT_WINDOW_DAYS - 1)
    desired: list[dict] = []
    for slot in sorted(slots, key=lambda item: item.position_index):
        if slot.status == SlotStatus.ARCHIVED.value:
            continue
        existing = existing_by_slot.get(slot.slot_key)
        if window is not None:
            eff, end = window
        elif existing is not None:
            eff, end = existing["effective_date"], existing["end_date"]
        else:
            eff, end = default_eff, default_end
        rule = rules.get(slot.rule_id)
        rule_label = ""
        if rule is not None:
            rule_label = (
                f"{' + '.join(names.get(code, code) for code in rule.antecedent)} → "
                f"{' + '.join(names.get(code, code) for code in rule.consequent)}"
            )
        desired.append(
            {
                "id": None,
                "schedule_key": "",
                "plan_code": slot.plan_code,
                "slot_id": str(slot.id),
                "slot_key": slot.slot_key,
                "conflict_group": slot.conflict_group,
                "zone": slot.zone,
                "shelf_label": slot.shelf_label,
                "rule_label": rule_label,
                "effective_date": eff,
                "end_date": end,
                "status": ScheduleStatus.SCHEDULED.value,
            }
        )
    return desired


def _compute(
    db: Session,
    plan_code: str,
    slots: list[PlanogramSlot],
    effective_date: date | None,
    end_date: date | None,
) -> dict:
    """预览/应用共用的计算：调整方案 + 跨建议单冲突 + 可用位（不落库）。"""
    today = _today()
    window = resolve_window(effective_date, end_date)
    existing = [_to_dict(row) for row in _live_rows(db, plan_code)]
    existing_by_slot = {row["slot_key"]: row for row in existing}
    desired = _desired_rows(db, slots, existing_by_slot, window, today)
    plan = plan_schedule(existing, desired, today)

    others = [_to_dict(row) for row in _live_rows(db) if row.plan_code != plan_code]
    conflicts = find_conflicts(plan["final"] + others, involve=plan_code)

    avail_window = window or (today + timedelta(days=1), today + timedelta(days=DEFAULT_WINDOW_DAYS))
    groups = _position_groups([*plan["final"], *others])
    available = available_positions(groups, others, avail_window[0], avail_window[1])
    return {
        "today": today,
        "window": window,
        "avail_window": avail_window,
        "plan": plan,
        "conflicts": conflicts,
        "available": available,
    }


def _position_groups(rows: list[dict]) -> list[str]:
    """货架位全集：固定分区序 + 数据里出现过的其他组（如历史占位）。"""
    groups = list(ZONE_ORDER)
    for row in rows:
        group = row["conflict_group"]
        if group and group not in groups:
            groups.append(group)
    return groups


def _payload(plan_code: str, computed: dict) -> dict:
    today = computed["today"]
    plan = computed["plan"]
    window = computed["window"]
    return {
        "plan_code": plan_code,
        "today": today.isoformat(),
        "window": {
            "effective_date": window[0].isoformat() if window else None,
            "end_date": window[1].isoformat() if window else None,
        },
        "ok": not computed["conflicts"],
        "keep": [_serialize(row, today) for row in plan["keep"]],
        "unchanged": [_serialize(row, today) for row in plan["unchanged"]],
        "replace": [
            {"old": _serialize(item["old"], today), "new": _serialize(item["new"], today)}
            for item in plan["replace"]
        ],
        "create": [_serialize(row, today) for row in plan["create"]],
        "release": [_serialize(row, today) for row in plan["release"]],
        "conflicts": [_serialize_conflict(item, today) for item in computed["conflicts"]],
        "available": computed["available"],
        "final": [_serialize(row, today) for row in plan["final"]],
        "counts": {
            "keep": len(plan["keep"]),
            "unchanged": len(plan["unchanged"]),
            "replaced": len(plan["replace"]),
            "created": len(plan["create"]),
            "released": len(plan["release"]),
            "conflicts": len(computed["conflicts"]),
        },
    }


def preview_schedule(
    db: Session,
    plan_code: str,
    effective_date: date | None,
    end_date: date | None,
) -> dict:
    """发布前核对：只读不落库，返回调整方案 / 冲突组合 / 可用位 / 最终结果。"""
    slots = list(
        db.execute(select(PlanogramSlot).where(PlanogramSlot.plan_code == plan_code)).scalars()
    )
    if not slots:
        raise AppError(codes.NOT_FOUND, "建议单不存在", f"plan_code={plan_code}")
    computed = _compute(db, plan_code, slots, effective_date, end_date)
    return _payload(plan_code, computed)


# ---------------------------------------------------------------- 写入 ----


def _schedule_key(db: Session, slot_key: str, effective_date: date) -> str:
    """幂等自然键；同键（含已替换旧版）已存在时追加序号，保证全局唯一。"""
    base = f"{slot_key}@{effective_date.isoformat()}"
    taken = db.execute(
        select(func.count())
        .select_from(PlanogramSchedule)
        .where(
            or_(
                PlanogramSchedule.schedule_key == base,
                PlanogramSchedule.schedule_key.like(f"{base}#%"),
            )
        )
    ).scalar_one()
    return base if taken == 0 else f"{base}#{taken + 1}"


def apply_schedule(
    db: Session,
    plan_code: str,
    slots: list[PlanogramSlot],
    effective_date: date | None,
    end_date: date | None,
) -> dict:
    """原子切换排期：只替换尚未生效的占用；冲突或日期非法 → 抛错，调用方回滚保留旧版。

    不写自己的 commit：由 publish_plan 在同一事务里统一提交 / 回滚。
    """
    today = _today()
    computed = _compute(db, plan_code, slots, effective_date, end_date)
    plan = computed["plan"]

    fresh = [row for row in plan["create"]] + [item["new"] for item in plan["replace"]]
    past = [row for row in fresh if row["effective_date"] < today]
    if past:
        raise AppError(
            codes.VALIDATION_ERROR,
            "生效日期不能早于今天",
            f"plan_code={plan_code} slots={len(past)}",
        )
    if computed["conflicts"]:
        first = computed["conflicts"][0]
        raise AppError(
            codes.CONFLICT,
            f"排期冲突：货架位 {first['conflict_group']} 在 "
            f"{first['overlap_start'].isoformat()} ~ {first['overlap_end'].isoformat()} "
            f"已被 {'、'.join(p for p in first['plans'] if p != plan_code)} 占用",
            f"plan_code={plan_code} conflicts={len(computed['conflicts'])}",
            extra={"conflicts": _payload(plan_code, computed)["conflicts"]},
        )

    for stale in [item["old"] for item in plan["replace"]] + plan["release"]:
        row = db.get(PlanogramSchedule, stale["id"])
        if row is not None:
            row.status = ScheduleStatus.REPLACED.value
    db.flush()  # 先落替换，再插新占用，部分唯一索引任一时刻只见一条有效占用

    for row in fresh:
        db.add(
            PlanogramSchedule(
                schedule_key=_schedule_key(db, row["slot_key"], row["effective_date"]),
                plan_code=plan_code,
                slot_id=row["slot_id"],
                slot_key=row["slot_key"],
                conflict_group=row["conflict_group"],
                zone=row["zone"],
                shelf_label=row["shelf_label"],
                rule_label=row["rule_label"],
                effective_date=row["effective_date"],
                end_date=row["end_date"],
                status=ScheduleStatus.SCHEDULED.value,
            )
        )
    db.flush()
    return _payload(plan_code, computed)


# ---------------------------------------------------------------- 总览 ----


def overview(db: Session, date_from: date | None, date_to: date | None) -> dict:
    """排期总览：每个货架位的占用时间线（跨建议单）、冲突组合、窗口内可用位。"""
    today = _today()
    win_from = date_from or today
    win_to = date_to or (win_from + timedelta(days=29))
    if win_to < win_from:
        raise AppError(codes.VALIDATION_ERROR, "结束日期不能早于开始日期", f"{win_from} > {win_to}")

    rows = [_to_dict(row) for row in _live_rows(db)]
    conflicts = find_conflicts(rows)
    conflicted = {item["conflict_group"] for item in conflicts}
    directory = fixtures_loader.zone_directory()

    groups = _position_groups(rows)
    positions = []
    for group in groups:
        occupancies = [
            _serialize(row, today)
            for row in rows
            if row["conflict_group"] == group
            and overlaps(row["effective_date"], row["end_date"], win_from, win_to)
        ]
        occupancies.sort(key=lambda item: (item["effective_date"], item["plan_code"]))
        state = "available" if not occupancies else ("conflict" if group in conflicted else "occupied")
        positions.append(
            {
                "conflict_group": group,
                "zone": directory.get(group, group),
                "state": state,
                "occupancies": occupancies,
            }
        )
    return {
        "today": today.isoformat(),
        "window": {"from": win_from.isoformat(), "to": win_to.isoformat()},
        "positions": positions,
        "conflicts": [_serialize_conflict(item, today) for item in conflicts],
        "available": available_positions(groups, rows, win_from, win_to),
        "summary": {
            "positions": len(groups),
            "available": len(available_positions(groups, rows, win_from, win_to)),
            "occupied": sum(1 for item in positions if item["state"] == "occupied"),
            "conflicts": len(conflicts),
        },
    }


def plan_schedule_map(db: Session, plan_code: str) -> dict[str, dict]:
    """建议单各建议位的当前有效占用（get_plan 展示用）：slot_key → 序列化行。"""
    today = _today()
    return {
        row.slot_key: _serialize(_to_dict(row), today)
        for row in _live_rows(db, plan_code)
    }
