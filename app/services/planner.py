from __future__ import annotations

from datetime import date, datetime, timedelta

from app.models import PlanTaskRequest, PlanTaskResponse, TimeBlock
from app.services.settings import load_planner_config


WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def plan_task_slot(payload: PlanTaskRequest) -> PlanTaskResponse:
    used_blocks = list(payload.existing_events)
    used_blocks.extend(_fixed_blocks_for_day(payload.planning_date))
    merged = _merge_blocks(used_blocks)

    cfg = load_planner_config()
    day_start_value = payload.day_start or cfg.get("day_window", {}).get("start", "05:00")
    day_end_value = payload.day_end or cfg.get("day_window", {}).get("end", "22:00")

    day_start = _at_time(payload.planning_date, day_start_value)
    day_end = _at_time(payload.planning_date, day_end_value)
    duration = timedelta(minutes=payload.duration_minutes)

    cursor = day_start
    for block in merged:
        if cursor + duration <= block.start:
            return PlanTaskResponse(
                role=payload.role,
                task_title=payload.task_title,
                planned_start=cursor,
                planned_end=cursor + duration,
                status="planned",
                used_blocks=merged,
            )
        if cursor < block.end:
            cursor = block.end

    if cursor + duration <= day_end:
        return PlanTaskResponse(
            role=payload.role,
            task_title=payload.task_title,
            planned_start=cursor,
            planned_end=cursor + duration,
            status="planned",
            used_blocks=merged,
        )

    return PlanTaskResponse(
        role=payload.role,
        task_title=payload.task_title,
        planned_start=None,
        planned_end=None,
        status="unplanned",
        reason="No free slot in requested day window.",
        used_blocks=merged,
    )


def _fixed_blocks_for_day(day: date) -> list[TimeBlock]:
    config = load_planner_config()
    blocks: list[TimeBlock] = []

    weekday = WEEKDAY_KEYS[day.weekday()]
    for rule in config.get("fixed_block_rules", []):
        active_days = {d.lower() for d in rule.get("days", [])}
        if active_days and weekday not in active_days:
            continue

        start = _at_time(day, rule["start"])
        end = _at_time(day, rule["end"])
        label = rule.get("label", "Fixed block")
        blocks.append(TimeBlock(start=start, end=end, label=label))

        commute_before = int(rule.get("commute_before_minutes", 0))
        if commute_before > 0:
            blocks.append(
                TimeBlock(
                    start=start - timedelta(minutes=commute_before),
                    end=start,
                    label=rule.get("commute_before_label", "Commute"),
                )
            )

        commute_after = int(rule.get("commute_after_minutes", 0))
        if commute_after > 0:
            blocks.append(
                TimeBlock(
                    start=end,
                    end=end + timedelta(minutes=commute_after),
                    label=rule.get("commute_after_label", "Commute"),
                )
            )

    return blocks


def _merge_blocks(blocks: list[TimeBlock]) -> list[TimeBlock]:
    if not blocks:
        return []
    sorted_blocks = sorted(blocks, key=lambda b: b.start)
    merged: list[TimeBlock] = [sorted_blocks[0]]

    for block in sorted_blocks[1:]:
        last = merged[-1]
        if block.start <= last.end:
            merged[-1] = TimeBlock(
                start=last.start,
                end=max(last.end, block.end),
                label=f"{last.label}; {block.label}".strip("; "),
            )
        else:
            merged.append(block)
    return merged


def _at_time(day: date, value: str) -> datetime:
    hour, minute = value.split(":")
    return datetime(day.year, day.month, day.day, int(hour), int(minute))
