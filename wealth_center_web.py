#!/usr/bin/env python3
"""财富流通中心 Web UI server."""

from __future__ import annotations

import json
import re
import subprocess
import threading
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "web"
STATE_FILE = BASE_DIR / "study_state.json"

TIME_FMT = "%Y-%m-%d %H:%M"
DATE_FMT = "%Y-%m-%d"
WEEKLY_GOAL_MINUTES = 600
WEEKLY_COURSE_GOAL = 2
DASHBOARD_DAY_WINDOW = 30
COURSE_TAG_LIMIT = 10

TASK_TYPES: dict[str, dict[str, Any]] = {
    "course": {
        "label": "课程学习",
        "calendar_tag": "课程",
        "xp_mult": 1.0,
        "wealth_mult": 1.0,
    },
    "review": {
        "label": "复习巩固",
        "calendar_tag": "复习",
        "xp_mult": 0.9,
        "wealth_mult": 0.9,
    },
    "skill": {
        "label": "技能拓展",
        "calendar_tag": "技能",
        "xp_mult": 1.1,
        "wealth_mult": 1.2,
    },
    "knowledge": {
        "label": "知识库搭建",
        "calendar_tag": "知识库",
        "xp_mult": 1.0,
        "wealth_mult": 1.1,
    },
    "homework": {
        "label": "做作业",
        "calendar_tag": "作业",
        "xp_mult": 0.95,
        "wealth_mult": 1.0,
    },
}

TASK_TYPE_ALIASES: dict[str, str] = {
    "course": "course",
    "课程": "course",
    "课程学习": "course",
    "review": "review",
    "复习": "review",
    "复习巩固": "review",
    "skill": "skill",
    "技能": "skill",
    "技能拓展": "skill",
    "knowledge": "knowledge",
    "knowledge_base": "knowledge",
    "knowledge-base": "knowledge",
    "knowledgebase": "knowledge",
    "kb": "knowledge",
    "知识库": "knowledge",
    "知识库搭建": "knowledge",
    "homework": "homework",
    "assignment": "homework",
    "assignments": "homework",
    "home_work": "homework",
    "home-work": "homework",
    "作业": "homework",
    "做作业": "homework",
}

STATE_LOCK = threading.Lock()


def now_local() -> datetime:
    return datetime.now()


def parse_time(raw: str) -> datetime:
    return datetime.strptime(raw.strip(), TIME_FMT)


def format_time(dt: datetime) -> str:
    return dt.strftime(TIME_FMT)


def get_minutes(start: datetime, end: datetime) -> int:
    return max(int((end - start).total_seconds() // 60), 10)


def normalize_task_type(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if not value:
        return "course"
    return TASK_TYPE_ALIASES.get(value, value)


def normalize_task_type_for_storage(raw: Any) -> str:
    task_type = normalize_task_type(raw)
    return task_type if task_type in TASK_TYPES else "course"


def canonical_tag_key(task_type: str, course_name: str, title: str) -> tuple[str, str, str]:
    return (
        normalize_task_type_for_storage(task_type),
        (course_name or "").strip().lower(),
        (title or "").strip().lower(),
    )


def tag_label(task_type: str, course_name: str, title: str) -> str:
    conf = TASK_TYPES.get(task_type, TASK_TYPES["course"])
    if course_name.strip():
        return f"{conf['label']} | {course_name.strip()} | {title.strip()}"
    return f"{conf['label']} | {title.strip()}"


def next_half_hour_slot(base: datetime | None = None) -> datetime:
    point = base or now_local()
    point = point.replace(second=0, microsecond=0)
    minute = point.minute
    if minute == 0 or minute == 30:
        return point + timedelta(minutes=30)
    if minute < 30:
        return point.replace(minute=30)
    return (point + timedelta(hours=1)).replace(minute=0)


def default_state() -> dict[str, Any]:
    return {
        "player": {
            "level": 1,
            "xp": 0,
            "wealth": 0,
            "streak": 0,
            "last_completed_date": None,
            "total_done": 0,
            "total_minutes": 0,
        },
        "next_id": 1,
        "next_tag_id": 1,
        "quests": [],
        "quick_tags": [],
    }


def migrate_state(state: dict[str, Any]) -> dict[str, Any]:
    player = state.setdefault("player", {})
    player.setdefault("level", 1)
    player.setdefault("xp", 0)
    if "wealth" not in player:
        player["wealth"] = player.get("coins", 0)
    player.setdefault("streak", 0)
    player.setdefault("last_completed_date", None)
    player.setdefault("total_done", 0)
    player.setdefault("total_minutes", 0)

    state.setdefault("next_id", 1)
    state.setdefault("next_tag_id", 1)
    state.setdefault("quests", [])
    state.setdefault("quick_tags", [])

    max_id = 0
    for q in state["quests"]:
        q.setdefault("id", 0)
        if q["id"] > max_id:
            max_id = q["id"]

        q.setdefault("status", "todo")
        q.setdefault("created_at", format_time(now_local()))
        q.setdefault("completed_at", None)
        q.setdefault("task_type", "course")
        q["task_type"] = normalize_task_type_for_storage(q.get("task_type", "course"))
        q.setdefault("course_name", "")
        q.setdefault("reward_xp", 0)
        q.setdefault("calendar_sync_status", "done")
        q.setdefault("calendar_sync_message", "")
        if "reward_wealth" not in q:
            q["reward_wealth"] = q.get("reward_coins", 0)

        if "duration_minutes" not in q:
            try:
                start = parse_time(q["start"])
                end = parse_time(q["end"])
                q["duration_minutes"] = get_minutes(start, end)
            except Exception:
                q["duration_minutes"] = 60

    if max_id >= state["next_id"]:
        state["next_id"] = max_id + 1

    if player["total_minutes"] == 0:
        done_minutes = sum(
            int(q.get("duration_minutes", 0))
            for q in state["quests"]
            if q.get("status") == "done"
        )
        if done_minutes:
            player["total_minutes"] = done_minutes

    if not state["quick_tags"]:
        for q in sorted(state["quests"], key=lambda x: x.get("created_at", "")):
            create_or_update_tag(
                state=state,
                task_type=str(q.get("task_type", "course")),
                course_name=str(q.get("course_name", "")),
                title=str(q.get("title", "")),
                duration_minutes=int(q.get("duration_minutes", 60)),
                event_time=str(q.get("created_at", format_time(now_local()))),
            )
    else:
        max_tag_id = 0
        for tag in state["quick_tags"]:
            tag.setdefault("id", 0)
            tag.setdefault("task_type", "course")
            tag["task_type"] = normalize_task_type_for_storage(tag.get("task_type", "course"))
            tag.setdefault("course_name", "")
            tag.setdefault("title", "")
            tag.setdefault("duration_minutes", 60)
            tag.setdefault("uses", 1)
            tag.setdefault("last_used_at", format_time(now_local()))
            if int(tag["id"]) > max_tag_id:
                max_tag_id = int(tag["id"])
        if max_tag_id >= state["next_tag_id"]:
            state["next_tag_id"] = max_tag_id + 1

    return state


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return default_state()
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return migrate_state(raw)
    except (json.JSONDecodeError, OSError):
        backup = STATE_FILE.with_suffix(".broken.json")
        try:
            STATE_FILE.rename(backup)
        except OSError:
            pass
        return default_state()


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def xp_to_next_level(level: int) -> int:
    return 100 + (level - 1) * 40


def apply_level_up(player: dict[str, Any]) -> None:
    while player["xp"] >= xp_to_next_level(player["level"]):
        player["xp"] -= xp_to_next_level(player["level"])
        player["level"] += 1


def update_streak(player: dict[str, Any], completed_at: datetime) -> None:
    today = completed_at.date()
    raw_last = player.get("last_completed_date")
    if raw_last is None:
        player["streak"] = 1
    else:
        last_day = datetime.strptime(raw_last, DATE_FMT).date()
        if last_day == today:
            pass
        elif last_day == today - timedelta(days=1):
            player["streak"] += 1
        else:
            player["streak"] = 1
    player["last_completed_date"] = today.strftime(DATE_FMT)


def rebuild_player_from_quests(state: dict[str, Any]) -> None:
    player = {
        "level": 1,
        "xp": 0,
        "wealth": 0,
        "streak": 0,
        "last_completed_date": None,
        "total_done": 0,
        "total_minutes": 0,
    }

    done_rows: list[tuple[datetime, dict[str, Any]]] = []
    for q in state.get("quests", []):
        if q.get("status") != "done":
            continue
        when = parse_quest_time(q.get("completed_at")) or parse_quest_time(q.get("end")) or now_local()
        done_rows.append((when, q))

    done_rows.sort(key=lambda x: x[0])
    for done_at, q in done_rows:
        minutes = int(q.get("duration_minutes", 0))
        if int(q.get("reward_xp", 0)) <= 0 or int(q.get("reward_wealth", 0)) <= 0:
            rx, rw = quest_reward(minutes, str(q.get("task_type", "course")))
            q["reward_xp"] = rx
            q["reward_wealth"] = rw

        player["xp"] += int(q.get("reward_xp", 0))
        player["wealth"] += int(q.get("reward_wealth", 0))
        player["total_done"] += 1
        player["total_minutes"] += minutes
        update_streak(player, done_at)
        apply_level_up(player)

    state["player"] = player


def quest_reward(minutes: int, task_type: str) -> tuple[int, int]:
    conf = TASK_TYPES.get(task_type, TASK_TYPES["course"])
    xp = max(20, int(minutes * 0.8 * conf["xp_mult"]))
    wealth = max(2, int((minutes / 15) * conf["wealth_mult"]))
    return xp, wealth


def escape_applescript_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def add_to_calendar(title: str, start: datetime, end: datetime) -> tuple[bool, str]:
    esc_title = escape_applescript_string(title)
    script = f"""
tell application "Calendar"
	set targetCalendar to first calendar
	set startTime to (current date)
	set year of startTime to {start.year}
	set month of startTime to {start.month}
	set day of startTime to {start.day}
	set hours of startTime to {start.hour}
	set minutes of startTime to {start.minute}
	set seconds of startTime to 0

	set endTime to (current date)
	set year of endTime to {end.year}
	set month of endTime to {end.month}
	set day of endTime to {end.day}
	set hours of endTime to {end.hour}
	set minutes of endTime to {end.minute}
	set seconds of endTime to 0
	make new event at end of events of targetCalendar with properties {{summary:"{esc_title}", start date:startTime, end date:endTime}}
end tell
return "OK"
"""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return False, f"无法调用 osascript: {exc}"

    if result.returncode == 0:
        return True, "OK"
    return False, result.stderr.strip() or "写入日历失败"


def build_calendar_title(task_type: str, course_name: str, title: str) -> str:
    # User requested clean titles in Calendar without "[课程]" style prefix.
    if course_name.strip():
        return f"{course_name.strip()} - {title.strip()}"
    return title.strip()


def _calendar_sync_job(quest_id: int) -> None:
    with STATE_LOCK:
        state = load_state()
        target = next((q for q in state["quests"] if int(q.get("id", 0)) == quest_id), None)
        if target is None:
            return

        start_raw = str(target.get("start", ""))
        end_raw = str(target.get("end", ""))
        task_type = str(target.get("task_type", "course"))
        course_name = str(target.get("course_name", ""))
        title = str(target.get("title", ""))
        target["calendar_sync_status"] = "syncing"
        target["calendar_sync_message"] = ""
        save_state(state)

    try:
        start = parse_time(start_raw)
        end = parse_time(end_raw)
        calendar_title = build_calendar_title(task_type, course_name, title)
        ok, msg = add_to_calendar(calendar_title, start, end)
    except Exception as exc:
        ok, msg = False, f"同步失败: {exc}"

    with STATE_LOCK:
        state = load_state()
        target = next((q for q in state["quests"] if int(q.get("id", 0)) == quest_id), None)
        if target is None:
            return
        target["calendar_sync_status"] = "done" if ok else "failed"
        target["calendar_sync_message"] = msg
        save_state(state)


def schedule_calendar_sync(quest_id: int) -> None:
    thread = threading.Thread(target=_calendar_sync_job, args=(quest_id,), daemon=True)
    thread.start()


def completed_quests(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [q for q in state["quests"] if q.get("status") == "done"]


def week_bounds(today: datetime) -> tuple[date, date]:
    monday = today.date() - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def parse_quest_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return parse_time(raw)
    except ValueError:
        return None


def stats_anchor_time(q: dict[str, Any]) -> datetime | None:
    # Keep dashboard time-based modules aligned with schedule views by anchoring to start time.
    return (
        parse_quest_time(q.get("start"))
        or parse_quest_time(q.get("end"))
        or parse_quest_time(q.get("completed_at"))
    )


def rolling_day_minutes(
    done_quests: list[dict[str, Any]],
    today: datetime,
    window_days: int = DASHBOARD_DAY_WINDOW,
) -> dict[str, int]:
    days: list[str] = []
    totals: dict[str, int] = {}
    for i in range(window_days - 1, -1, -1):
        day = (today.date() - timedelta(days=i)).strftime(DATE_FMT)
        days.append(day)
        totals[day] = 0

    for q in done_quests:
        q_time = stats_anchor_time(q)
        if not q_time:
            continue
        day = q_time.strftime(DATE_FMT)
        if day in totals:
            totals[day] += int(q.get("duration_minutes", 0))

    return {day: totals[day] for day in days}


def course_minutes_recent(
    done_quests: list[dict[str, Any]],
    today: datetime,
    window_days: int = DASHBOARD_DAY_WINDOW,
    limit: int = COURSE_TAG_LIMIT,
) -> list[dict[str, Any]]:
    start_date = today.date() - timedelta(days=window_days - 1)
    totals: dict[str, dict[str, Any]] = {}

    for q in done_quests:
        q_time = stats_anchor_time(q)
        if not q_time:
            continue
        q_date = q_time.date()
        if q_date < start_date or q_date > today.date():
            continue

        course_name = (q.get("course_name") or q.get("title") or "未命名课程").strip()
        key = course_name.lower()
        if key not in totals:
            totals[key] = {"course_name": course_name, "minutes": 0, "sessions": 0}

        totals[key]["minutes"] += int(q.get("duration_minutes", 0))
        totals[key]["sessions"] += 1

    rows = list(totals.values())
    rows.sort(key=lambda item: (int(item["minutes"]), int(item["sessions"])), reverse=True)
    return rows[:limit]


def type_minutes(done_quests: list[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = {"course": 0, "review": 0, "skill": 0, "knowledge": 0, "homework": 0}
    for q in done_quests:
        q_type = q.get("task_type", "course")
        totals[q_type] = totals.get(q_type, 0) + int(q.get("duration_minutes", 0))
    return totals


def build_week_metrics(state: dict[str, Any], today: datetime) -> dict[str, Any]:
    monday, sunday = week_bounds(today)
    week_done: list[dict[str, Any]] = []

    for q in completed_quests(state):
        q_time = stats_anchor_time(q)
        if q_time and monday <= q_time.date() <= sunday:
            week_done.append(q)

    weekly_minutes_total = sum(int(q.get("duration_minutes", 0)) for q in week_done)
    done_courses_keys: set[str] = set()
    done_courses: list[str] = []
    for q in week_done:
        if q.get("task_type") != "course":
            continue
        name = (q.get("course_name") or q.get("title") or "未命名课程").strip()
        key = name.lower()
        if key in done_courses_keys:
            continue
        done_courses_keys.add(key)
        done_courses.append(name)

    planned_course_tasks = 0
    for q in state["quests"]:
        if q.get("status") != "todo" or q.get("task_type") != "course":
            continue
        q_start = parse_quest_time(q.get("start"))
        if q_start and monday <= q_start.date() <= sunday:
            planned_course_tasks += 1

    return {
        "week_start": monday.strftime(DATE_FMT),
        "week_end": sunday.strftime(DATE_FMT),
        "days_left": max(0, (sunday - today.date()).days),
        "weekly_minutes": weekly_minutes_total,
        "weekly_goal_minutes": WEEKLY_GOAL_MINUTES,
        "course_goal": WEEKLY_COURSE_GOAL,
        "courses_done": len(done_courses),
        "done_courses": done_courses,
        "planned_course_tasks": planned_course_tasks,
    }


def build_week_outline(state: dict[str, Any], today: datetime) -> list[dict[str, Any]]:
    monday, sunday = week_bounds(today)
    weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    day_buckets: dict[str, list[dict[str, Any]]] = {}

    for i in range(7):
        day = (monday + timedelta(days=i)).strftime(DATE_FMT)
        day_buckets[day] = []

    for q in state.get("quests", []):
        start_dt = parse_quest_time(q.get("start"))
        end_dt = parse_quest_time(q.get("end"))
        if not start_dt:
            continue
        if not (monday <= start_dt.date() <= sunday):
            continue

        day_key = start_dt.strftime(DATE_FMT)
        conf = TASK_TYPES.get(str(q.get("task_type", "course")), TASK_TYPES["course"])
        day_buckets[day_key].append(
            {
                "id": int(q.get("id", 0)),
                "title": str(q.get("title", "")),
                "course_name": str(q.get("course_name", "")),
                "task_type": str(q.get("task_type", "course")),
                "task_type_label": conf["label"],
                "start": format_time(start_dt),
                "end": format_time(end_dt) if end_dt else "",
                "start_time": start_dt.strftime("%H:%M"),
                "end_time": end_dt.strftime("%H:%M") if end_dt else "",
                "duration_minutes": int(q.get("duration_minutes", 0)),
                "status": str(q.get("status", "todo")),
            }
        )

    outline = []
    for i in range(7):
        day_date = monday + timedelta(days=i)
        day_key = day_date.strftime(DATE_FMT)
        tasks = sorted(day_buckets[day_key], key=lambda t: t.get("start", ""))
        outline.append(
            {
                "date": day_key,
                "weekday": weekday_cn[i],
                "is_today": day_date == today.date(),
                "tasks": tasks,
            }
        )
    return outline


def weekly_reminders(metrics: dict[str, Any]) -> list[str]:
    reminders: list[str] = []
    remaining_courses = max(0, metrics["course_goal"] - metrics["courses_done"])
    remaining_minutes = max(0, metrics["weekly_goal_minutes"] - metrics["weekly_minutes"])
    days_left = int(metrics["days_left"])

    if remaining_courses == 0:
        reminders.append("本周课程目标已完成，继续保持。")
    elif days_left <= 2:
        reminders.append(f"本周还差 {remaining_courses} 门课程，优先安排课程学习。")
    else:
        reminders.append(
            f"课程目标进度 {metrics['courses_done']}/{metrics['course_goal']}，还差 {remaining_courses} 门。"
        )

    if remaining_minutes == 0:
        reminders.append("本周学习时长已达标。")
    elif days_left <= 2:
        reminders.append(f"本周学习时长还差 {remaining_minutes} 分钟，建议加一段复习或技能训练。")
    else:
        reminders.append(
            f"学习时长进度 {metrics['weekly_minutes']}/{metrics['weekly_goal_minutes']} 分钟。"
        )

    if remaining_courses > 0 and metrics["planned_course_tasks"] == 0:
        reminders.append("当前周内没有待完成课程任务，建议新增课程学习日历。")

    return reminders


def create_or_update_tag(
    state: dict[str, Any],
    task_type: str,
    course_name: str,
    title: str,
    duration_minutes: int,
    event_time: str,
) -> dict[str, Any]:
    task_type = normalize_task_type_for_storage(task_type)
    key = canonical_tag_key(task_type, course_name, title)
    for tag in state["quick_tags"]:
        tag_key = canonical_tag_key(
            str(tag.get("task_type", "course")),
            str(tag.get("course_name", "")),
            str(tag.get("title", "")),
        )
        if tag_key == key:
            tag["duration_minutes"] = max(10, int(duration_minutes))
            tag["uses"] = int(tag.get("uses", 0)) + 1
            tag["last_used_at"] = event_time
            return tag

    tag = {
        "id": state["next_tag_id"],
        "task_type": task_type,
        "course_name": course_name,
        "title": title,
        "duration_minutes": max(10, int(duration_minutes)),
        "uses": 1,
        "last_used_at": event_time,
    }
    state["next_tag_id"] += 1
    state["quick_tags"].append(tag)
    return tag


def create_quest_record(
    state: dict[str, Any],
    *,
    task_type: str,
    course_name: str,
    title: str,
    start: datetime,
    end: datetime,
    write_calendar: bool,
    touch_tag: bool = True,
) -> dict[str, Any]:
    task_type = normalize_task_type_for_storage(task_type)
    minutes = get_minutes(start, end)
    reward_xp, reward_wealth = quest_reward(minutes, task_type)
    quest_id = state["next_id"]
    state["next_id"] += 1

    quest = {
        "id": quest_id,
        "title": title,
        "course_name": course_name,
        "task_type": task_type,
        "start": format_time(start),
        "end": format_time(end),
        "duration_minutes": minutes,
        "reward_xp": reward_xp,
        "reward_wealth": reward_wealth,
        "status": "todo",
        "created_at": format_time(now_local()),
        "completed_at": None,
        "calendar_sync_status": "pending" if write_calendar else "skipped",
        "calendar_sync_message": "",
    }
    state["quests"].append(quest)

    if touch_tag:
        create_or_update_tag(
            state=state,
            task_type=task_type,
            course_name=course_name,
            title=title,
            duration_minutes=minutes,
            event_time=quest["created_at"],
        )

    return quest


def quest_label(q: dict[str, Any]) -> str:
    conf = TASK_TYPES.get(q.get("task_type", "course"), TASK_TYPES["course"])
    course = (q.get("course_name") or "").strip()
    if course:
        return f"{conf['label']} | {course} | {q['title']}"
    return f"{conf['label']} | {q['title']}"


def dashboard_payload(state: dict[str, Any]) -> dict[str, Any]:
    now = now_local()
    done = completed_quests(state)
    by_day = rolling_day_minutes(done, now, window_days=DASHBOARD_DAY_WINDOW)
    by_type = type_minutes(done)
    by_course = course_minutes_recent(done, now, window_days=DASHBOARD_DAY_WINDOW, limit=COURSE_TAG_LIMIT)
    weekly = build_week_metrics(state, now)
    weekly_outline = build_week_outline(state, now)

    quests_sorted = sorted(state["quests"], key=lambda q: q.get("start", ""), reverse=True)
    quest_rows = []
    for q in quests_sorted:
        quest_rows.append(
            {
                "id": q["id"],
                "title": q.get("title", ""),
                "label": quest_label(q),
                "task_type": q.get("task_type", "course"),
                "course_name": q.get("course_name", ""),
                "start": q.get("start", ""),
                "end": q.get("end", ""),
                "completed_at": q.get("completed_at", ""),
                "duration_minutes": int(q.get("duration_minutes", 0)),
                "status": q.get("status", "todo"),
                "reward_xp": int(q.get("reward_xp", 0)),
                "reward_wealth": int(q.get("reward_wealth", 0)),
                "calendar_sync_status": q.get("calendar_sync_status", "done"),
                "calendar_sync_message": q.get("calendar_sync_message", ""),
            }
        )

    tags_sorted = sorted(
        state.get("quick_tags", []),
        key=lambda t: (t.get("last_used_at", ""), int(t.get("uses", 0))),
        reverse=True,
    )
    tag_rows = []
    for t in tags_sorted:
        row = {
            "id": int(t.get("id", 0)),
            "task_type": str(t.get("task_type", "course")),
            "course_name": str(t.get("course_name", "")),
            "title": str(t.get("title", "")),
            "duration_minutes": int(t.get("duration_minutes", 60)),
            "uses": int(t.get("uses", 0)),
            "last_used_at": str(t.get("last_used_at", "")),
        }
        row["label"] = tag_label(
            row["task_type"],
            row["course_name"],
            row["title"],
        )
        tag_rows.append(row)

    player = state["player"]
    player_out = {
        "level": int(player.get("level", 1)),
        "xp": int(player.get("xp", 0)),
        "xp_target": xp_to_next_level(int(player.get("level", 1))),
        "wealth": int(player.get("wealth", 0)),
        "streak": int(player.get("streak", 0)),
        "total_done": int(player.get("total_done", 0)),
        "total_minutes": int(player.get("total_minutes", 0)),
    }

    return {
        "server_time": format_time(now),
        "player": player_out,
        "quests": quest_rows,
        "quick_tags": tag_rows,
        "charts": {
            "days": list(by_day.keys()),
            "day_minutes": [by_day[d] for d in by_day.keys()],
            "type_minutes": by_type,
            "course_minutes": by_course,
        },
        "weekly": weekly,
        "weekly_outline": weekly_outline,
        "reminders": weekly_reminders(weekly),
    }


class WealthCenterHandler(BaseHTTPRequestHandler):
    server_version = "WealthCenterHTTP/1.0"

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            raise ValueError("请求体必须是 JSON")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return
        if path == "/api/health":
            self._send_json(200, {"ok": True, "service": "wealth-center"})
            return
        if path == "/api/state":
            with STATE_LOCK:
                state = load_state()
            self._send_json(200, {"ok": True, "data": dashboard_payload(state)})
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/quests":
            self._handle_create_quest()
            return

        tag_match = re.fullmatch(r"/api/tags/(\d+)/create", path)
        if tag_match:
            self._handle_create_from_tag(int(tag_match.group(1)))
            return

        match = re.fullmatch(r"/api/quests/(\d+)/complete", path)
        if match:
            self._handle_complete_quest(int(match.group(1)))
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_PATCH(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        match = re.fullmatch(r"/api/quests/(\d+)", path)
        if match:
            self._handle_update_quest(int(match.group(1)))
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        match = re.fullmatch(r"/api/quests/(\d+)", path)
        if match:
            self._handle_delete_quest(int(match.group(1)))
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def _handle_create_quest(self) -> None:
        try:
            body = self._read_json_body()
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return

        title = str(body.get("title", "")).strip()
        course_name = str(body.get("course_name", "")).strip()
        task_type = normalize_task_type(body.get("task_type", "course"))
        start_raw = str(body.get("start", "")).strip()
        end_raw = str(body.get("end", "")).strip()
        write_calendar = bool(body.get("write_calendar", True))

        if not title:
            self._send_json(400, {"ok": False, "error": "任务标题不能为空"})
            return
        if task_type not in TASK_TYPES:
            self._send_json(
                400,
                {"ok": False, "error": "任务类型不合法，可选：课程学习 / 复习巩固 / 技能拓展 / 知识库搭建 / 做作业"},
            )
            return

        try:
            start = parse_time(start_raw)
            end = parse_time(end_raw)
        except ValueError:
            self._send_json(400, {"ok": False, "error": f"时间格式必须是 {TIME_FMT}"})
            return

        if end <= start:
            self._send_json(400, {"ok": False, "error": "结束时间必须晚于开始时间"})
            return

        with STATE_LOCK:
            state = load_state()
            quest = create_quest_record(
                state=state,
                task_type=task_type,
                course_name=course_name,
                title=title,
                start=start,
                end=end,
                write_calendar=write_calendar,
                touch_tag=True,
            )
            save_state(state)
            payload = dashboard_payload(state)
            quest_id = int(quest["id"])

        if write_calendar:
            schedule_calendar_sync(quest_id)

        self._send_json(
            201,
            {
                "ok": True,
                "message": "任务创建成功，日历正在后台同步",
                "quest_id": quest_id,
                "data": payload,
            },
        )

    def _handle_create_from_tag(self, tag_id: int) -> None:
        try:
            body = self._read_json_body()
        except ValueError:
            body = {}

        write_calendar = bool(body.get("write_calendar", True))
        start_raw = str(body.get("start", "")).strip()

        with STATE_LOCK:
            state = load_state()
            tag = next((t for t in state.get("quick_tags", []) if int(t.get("id", 0)) == tag_id), None)
            if tag is None:
                self._send_json(404, {"ok": False, "error": "找不到这个标签"})
                return

            if start_raw:
                try:
                    start = parse_time(start_raw)
                except ValueError:
                    self._send_json(400, {"ok": False, "error": f"时间格式必须是 {TIME_FMT}"})
                    return
            else:
                start = next_half_hour_slot()

            duration = max(10, int(tag.get("duration_minutes", 60)))
            end = start + timedelta(minutes=duration)

            quest = create_quest_record(
                state=state,
                task_type=normalize_task_type_for_storage(tag.get("task_type", "course")),
                course_name=str(tag.get("course_name", "")),
                title=str(tag.get("title", "")),
                start=start,
                end=end,
                write_calendar=write_calendar,
                touch_tag=True,
            )
            save_state(state)
            payload = dashboard_payload(state)
            quest_id = int(quest["id"])

        if write_calendar:
            schedule_calendar_sync(quest_id)

        self._send_json(
            201,
            {
                "ok": True,
                "message": "已根据标签创建复习任务",
                "quest_id": quest_id,
                "start": format_time(start),
                "end": format_time(end),
                "data": payload,
            },
        )

    def _handle_update_quest(self, quest_id: int) -> None:
        try:
            body = self._read_json_body()
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return

        with STATE_LOCK:
            state = load_state()
            target = next((q for q in state["quests"] if int(q.get("id", 0)) == quest_id), None)
            if target is None:
                self._send_json(404, {"ok": False, "error": "找不到任务"})
                return
            if target.get("status") == "done":
                self._send_json(400, {"ok": False, "error": "已完成任务不支持修改"})
                return

            title = str(body.get("title", target.get("title", ""))).strip()
            course_name = str(body.get("course_name", target.get("course_name", ""))).strip()
            target_task_type = normalize_task_type_for_storage(target.get("task_type", "course"))
            task_type = normalize_task_type(body.get("task_type", target_task_type))
            start_raw = str(body.get("start", target.get("start", ""))).strip()
            end_raw = str(body.get("end", target.get("end", ""))).strip()
            write_calendar = bool(body.get("write_calendar", False))

            if not title:
                self._send_json(400, {"ok": False, "error": "任务标题不能为空"})
                return
            if task_type not in TASK_TYPES:
                self._send_json(
                    400,
                    {"ok": False, "error": "任务类型不合法，可选：课程学习 / 复习巩固 / 技能拓展 / 知识库搭建 / 做作业"},
                )
                return
            try:
                start = parse_time(start_raw)
                end = parse_time(end_raw)
            except ValueError:
                self._send_json(400, {"ok": False, "error": f"时间格式必须是 {TIME_FMT}"})
                return
            if end <= start:
                self._send_json(400, {"ok": False, "error": "结束时间必须晚于开始时间"})
                return

            minutes = get_minutes(start, end)
            reward_xp, reward_wealth = quest_reward(minutes, task_type)

            target["title"] = title
            target["course_name"] = course_name
            target["task_type"] = task_type
            target["start"] = format_time(start)
            target["end"] = format_time(end)
            target["duration_minutes"] = minutes
            target["reward_xp"] = reward_xp
            target["reward_wealth"] = reward_wealth
            target["updated_at"] = format_time(now_local())
            if write_calendar:
                target["calendar_sync_status"] = "pending"
                target["calendar_sync_message"] = ""
            else:
                target["calendar_sync_message"] = "已修改（未改动已有日历事件）"

            create_or_update_tag(
                state=state,
                task_type=task_type,
                course_name=course_name,
                title=title,
                duration_minutes=minutes,
                event_time=target["updated_at"],
            )

            save_state(state)
            payload = dashboard_payload(state)

        if write_calendar:
            schedule_calendar_sync(quest_id)

        self._send_json(
            200,
            {
                "ok": True,
                "message": "任务已修改",
                "quest_id": quest_id,
                "data": payload,
            },
        )

    def _handle_delete_quest(self, quest_id: int) -> None:
        with STATE_LOCK:
            state = load_state()
            index = next(
                (idx for idx, q in enumerate(state["quests"]) if int(q.get("id", 0)) == quest_id),
                -1,
            )
            if index < 0:
                self._send_json(404, {"ok": False, "error": "找不到任务"})
                return

            removed = state["quests"].pop(index)
            if removed.get("status") == "done":
                rebuild_player_from_quests(state)
            save_state(state)
            payload = dashboard_payload(state)

        self._send_json(
            200,
            {
                "ok": True,
                "message": "任务已删除",
                "deleted": {
                    "id": int(removed.get("id", 0)),
                    "status": str(removed.get("status", "todo")),
                },
                "data": payload,
            },
        )

    def _handle_complete_quest(self, quest_id: int) -> None:
        with STATE_LOCK:
            state = load_state()
            target = next((q for q in state["quests"] if int(q.get("id", 0)) == quest_id), None)
            if target is None:
                self._send_json(404, {"ok": False, "error": "找不到任务"})
                return
            if target.get("status") == "done":
                self._send_json(400, {"ok": False, "error": "任务已经完成"})
                return

            target["status"] = "done"
            target["completed_at"] = format_time(now_local())

            minutes = int(target.get("duration_minutes", 0))
            if int(target.get("reward_xp", 0)) <= 0 or int(target.get("reward_wealth", 0)) <= 0:
                rx, rw = quest_reward(minutes, str(target.get("task_type", "course")))
                target["reward_xp"] = rx
                target["reward_wealth"] = rw

            player = state["player"]
            player["xp"] += int(target.get("reward_xp", 0))
            player["wealth"] += int(target.get("reward_wealth", 0))
            player["total_done"] += 1
            player["total_minutes"] += minutes
            update_streak(player, now_local())
            apply_level_up(player)

            save_state(state)
            payload = dashboard_payload(state)

        self._send_json(200, {"ok": True, "message": "任务已完成", "data": payload})

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def run(host: str = "127.0.0.1", port: int = 4318) -> None:
    if not STATIC_DIR.exists():
        raise SystemExit(f"Missing static dir: {STATIC_DIR}")
    server = ThreadingHTTPServer((host, port), WealthCenterHandler)
    print(f"Wealth Center running at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run()
