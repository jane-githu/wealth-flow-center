#!/usr/bin/env python3
"""财富流通中心: A CLI game for scheduling study and turning time into wealth."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


GAME_NAME = "财富流通中心"
STATE_FILE = Path(__file__).resolve().parent / "study_state.json"
TIME_FMT = "%Y-%m-%d %H:%M"
DATE_FMT = "%Y-%m-%d"
WEEKLY_GOAL_MINUTES = 600
WEEKLY_COURSE_GOAL = 2

TASK_TYPES: dict[str, dict[str, Any]] = {
    "1": {
        "key": "course",
        "label": "课程学习",
        "calendar_tag": "课程",
        "xp_mult": 1.0,
        "wealth_mult": 1.0,
    },
    "2": {
        "key": "review",
        "label": "复习巩固",
        "calendar_tag": "复习",
        "xp_mult": 0.9,
        "wealth_mult": 0.9,
    },
    "3": {
        "key": "skill",
        "label": "技能拓展",
        "calendar_tag": "技能",
        "xp_mult": 1.1,
        "wealth_mult": 1.2,
    },
}
TASK_TYPE_BY_KEY = {v["key"]: v for v in TASK_TYPES.values()}


@dataclass
class QuestReward:
    xp: int
    wealth: int
    minutes: int


def now_local() -> datetime:
    return datetime.now()


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
        "quests": [],
    }


def parse_time(raw: str) -> datetime:
    return datetime.strptime(raw.strip(), TIME_FMT)


def format_time(dt: datetime) -> str:
    return dt.strftime(TIME_FMT)


def get_minutes(start: datetime, end: datetime) -> int:
    return max(int((end - start).total_seconds() // 60), 10)


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
    state.setdefault("quests", [])

    max_id = 0
    for q in state["quests"]:
        q.setdefault("id", 0)
        if q["id"] > max_id:
            max_id = q["id"]

        q.setdefault("status", "todo")
        q.setdefault("created_at", format_time(now_local()))
        q.setdefault("completed_at", None)
        q.setdefault("task_type", "course")
        q.setdefault("course_name", "")
        q.setdefault("reward_xp", 0)
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

    return state


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return default_state()
    try:
        raw_state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return migrate_state(raw_state)
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


def apply_level_up(player: dict[str, Any]) -> list[int]:
    leveled_up = []
    while player["xp"] >= xp_to_next_level(player["level"]):
        player["xp"] -= xp_to_next_level(player["level"])
        player["level"] += 1
        leveled_up.append(player["level"])
    return leveled_up


def update_streak(player: dict[str, Any], completed_at: datetime) -> None:
    today = completed_at.date()
    last = player.get("last_completed_date")
    if last is None:
        player["streak"] = 1
    else:
        last_day = datetime.strptime(last, DATE_FMT).date()
        if last_day == today:
            pass
        elif last_day == today - timedelta(days=1):
            player["streak"] += 1
        else:
            player["streak"] = 1
    player["last_completed_date"] = today.strftime(DATE_FMT)


def quest_reward(minutes: int, task_type: str) -> QuestReward:
    conf = TASK_TYPE_BY_KEY.get(task_type, TASK_TYPE_BY_KEY["course"])
    xp = max(20, int(minutes * 0.8 * conf["xp_mult"]))
    wealth = max(2, int((minutes / 15) * conf["wealth_mult"]))
    return QuestReward(xp=xp, wealth=wealth, minutes=minutes)


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
        return True, result.stdout.strip() or "OK"
    return False, result.stderr.strip() or "未知错误"


def bar(value: int, max_value: int, width: int = 28) -> str:
    if max_value <= 0:
        return "." * width
    filled = int((value / max_value) * width)
    filled = max(0, min(width, filled))
    return "#" * filled + "." * (width - filled)


def completed_quests(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [q for q in state["quests"] if q.get("status") == "done"]


def weekly_minutes(done_quests: list[dict[str, Any]], today: datetime) -> dict[str, int]:
    days = []
    totals: dict[str, int] = {}
    for i in range(6, -1, -1):
        day = (today.date() - timedelta(days=i)).strftime(DATE_FMT)
        days.append(day)
        totals[day] = 0

    for q in done_quests:
        raw = q.get("completed_at") or q.get("end")
        try:
            day = parse_time(raw).strftime(DATE_FMT)
        except Exception:
            continue
        if day in totals:
            totals[day] += int(q.get("duration_minutes", 0))

    return {day: totals[day] for day in days}


def type_minutes(done_quests: list[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = {"course": 0, "review": 0, "skill": 0}
    for q in done_quests:
        t = q.get("task_type", "course")
        totals[t] = totals.get(t, 0) + int(q.get("duration_minutes", 0))
    return totals


def week_bounds(today: datetime) -> tuple[datetime.date, datetime.date]:
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


def build_week_metrics(state: dict[str, Any], today: datetime) -> dict[str, Any]:
    monday, sunday = week_bounds(today)

    week_done: list[dict[str, Any]] = []
    for q in completed_quests(state):
        q_time = parse_quest_time(q.get("completed_at")) or parse_quest_time(q.get("end"))
        if q_time and monday <= q_time.date() <= sunday:
            week_done.append(q)

    weekly_minutes = sum(int(q.get("duration_minutes", 0)) for q in week_done)

    done_courses: list[str] = []
    done_keys: set[str] = set()
    for q in week_done:
        if q.get("task_type") != "course":
            continue
        name = (q.get("course_name") or q.get("title") or "未命名课程").strip()
        key = name.lower()
        if key in done_keys:
            continue
        done_keys.add(key)
        done_courses.append(name)

    planned_course_tasks = 0
    for q in state["quests"]:
        if q.get("status") != "todo" or q.get("task_type") != "course":
            continue
        q_start = parse_quest_time(q.get("start"))
        if q_start and monday <= q_start.date() <= sunday:
            planned_course_tasks += 1

    return {
        "week_start": monday,
        "week_end": sunday,
        "days_left": max(0, (sunday - today.date()).days),
        "weekly_minutes": weekly_minutes,
        "weekly_goal_minutes": WEEKLY_GOAL_MINUTES,
        "course_goal": WEEKLY_COURSE_GOAL,
        "courses_done": len(done_courses),
        "done_courses": done_courses,
        "planned_course_tasks": planned_course_tasks,
    }


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
        reminders.append("当前周内没有待完成课程任务，建议立即新增课程学习日历。")

    return reminders


def print_auto_weekly_hint(state: dict[str, Any]) -> None:
    metrics = build_week_metrics(state, now_local())
    remaining_courses = max(0, metrics["course_goal"] - metrics["courses_done"])
    remaining_minutes = max(0, metrics["weekly_goal_minutes"] - metrics["weekly_minutes"])

    print(
        f"本周追踪: 课程 {metrics['courses_done']}/{metrics['course_goal']} | "
        f"时长 {metrics['weekly_minutes']}/{metrics['weekly_goal_minutes']} 分钟"
    )
    if metrics["days_left"] <= 2 and (remaining_courses > 0 or remaining_minutes > 0):
        print(
            f"提醒: 距离周末约 {metrics['days_left'] + 1} 天，"
            f"还差 {remaining_courses} 门课程，{remaining_minutes} 分钟学习时长。"
        )


def print_header() -> None:
    print(f"\n=== {GAME_NAME} | 知识就是财富 ===")
    print("1) 新建学习任务 + 添加到苹果日历")
    print("2) 完成一个任务（领取财富）")
    print("3) 查看任务列表")
    print("4) 查看角色状态")
    print("5) 查看学习时间可视化")
    print("6) 查看本周目标提醒")
    print("7) 退出")


def choose_task_type() -> dict[str, Any]:
    print("学习类型：1) 课程学习  2) 复习巩固  3) 技能拓展")
    raw = input("选择类型（默认 1）: ").strip() or "1"
    return TASK_TYPES.get(raw, TASK_TYPES["1"])


def create_quest(state: dict[str, Any]) -> None:
    conf = choose_task_type()
    course_name = input("课程/技能名称（如 CS584、Python）: ").strip()
    title = input("任务标题: ").strip()
    if not title:
        print("标题不能为空。")
        return

    start_raw = input(f"开始时间（{TIME_FMT}）: ").strip()
    end_raw = input(f"结束时间（{TIME_FMT}）: ").strip()
    try:
        start = parse_time(start_raw)
        end = parse_time(end_raw)
    except ValueError:
        print("时间格式错误。示例：2026-04-14 22:00")
        return
    if end <= start:
        print("结束时间必须晚于开始时间。")
        return

    minutes = get_minutes(start, end)
    reward = quest_reward(minutes, conf["key"])

    prefix = f"[{conf['calendar_tag']}]"
    if course_name:
        calendar_title = f"{prefix} {course_name} - {title}"
    else:
        calendar_title = f"{prefix} {title}"
    ok, msg = add_to_calendar(calendar_title, start, end)
    if not ok:
        print(f"写入苹果日历失败：{msg}")
        return

    quest_id = state["next_id"]
    state["next_id"] += 1
    state["quests"].append(
        {
            "id": quest_id,
            "title": title,
            "course_name": course_name,
            "task_type": conf["key"],
            "start": format_time(start),
            "end": format_time(end),
            "duration_minutes": minutes,
            "reward_xp": reward.xp,
            "reward_wealth": reward.wealth,
            "status": "todo",
            "created_at": format_time(now_local()),
            "completed_at": None,
        }
    )
    save_state(state)
    print(f"任务创建成功（ID={quest_id}），并已写入苹果日历。")
    print(
        f"预计奖励：+{reward.xp} XP，+{reward.wealth} 财富值，学习时长 {reward.minutes} 分钟"
    )


def quest_label(q: dict[str, Any]) -> str:
    conf = TASK_TYPE_BY_KEY.get(q.get("task_type", "course"), TASK_TYPE_BY_KEY["course"])
    course = q.get("course_name", "").strip()
    if course:
        return f"{conf['label']} | {course} | {q['title']}"
    return f"{conf['label']} | {q['title']}"


def list_quests(state: dict[str, Any]) -> None:
    quests = state["quests"]
    if not quests:
        print("还没有任务。")
        return
    print("\n--- 任务列表 ---")
    for q in quests:
        status = "未完成" if q["status"] == "todo" else "已完成"
        minutes = int(q.get("duration_minutes", 0))
        print(
            f"ID {q['id']:>3} | {status} | {q['start']} -> {q['end']} ({minutes}m) | {quest_label(q)}"
        )


def complete_quest(state: dict[str, Any]) -> None:
    open_quests = [q for q in state["quests"] if q["status"] == "todo"]
    if not open_quests:
        print("没有可完成的任务。")
        return
    list_quests(state)

    raw = input("输入要完成的任务 ID: ").strip()
    if not raw.isdigit():
        print("ID 必须是数字。")
        return
    quest_id = int(raw)

    target = next((q for q in state["quests"] if q["id"] == quest_id), None)
    if target is None:
        print("找不到这个任务 ID。")
        return
    if target["status"] == "done":
        print("这个任务已经完成了。")
        return

    done_at = now_local()
    target["status"] = "done"
    target["completed_at"] = format_time(done_at)

    player = state["player"]
    player["xp"] += int(target.get("reward_xp", 0))
    player["wealth"] += int(target.get("reward_wealth", 0))
    player["total_done"] += 1
    player["total_minutes"] += int(target.get("duration_minutes", 0))
    update_streak(player, done_at)
    levels = apply_level_up(player)
    save_state(state)

    print(
        f"任务完成：+{target['reward_xp']} XP，+{target['reward_wealth']} 财富值，连续学习 {player['streak']} 天"
    )
    if levels:
        print(f"升级了！当前等级: {player['level']}")


def show_player(state: dict[str, Any]) -> None:
    p = state["player"]
    need = xp_to_next_level(p["level"])
    metrics = build_week_metrics(state, now_local())
    print("\n--- 角色状态 ---")
    print(f"等级: {p['level']}")
    print(f"经验: {p['xp']} / {need}")
    print(f"财富值: {p['wealth']}")
    print(f"连续学习: {p['streak']} 天")
    print(f"总完成任务: {p['total_done']}")
    print(f"总学习时长: {p['total_minutes']} 分钟")
    print(
        f"本周课程进度: {metrics['courses_done']}/{metrics['course_goal']} | "
        f"本周时长: {metrics['weekly_minutes']}/{metrics['weekly_goal_minutes']} 分钟"
    )


def show_visualization(state: dict[str, Any]) -> None:
    done = completed_quests(state)
    if not done:
        print("还没有已完成任务，暂时无法可视化。")
        return

    today = now_local()
    metrics = build_week_metrics(state, today)
    by_day = weekly_minutes(done, today)
    by_type = type_minutes(done)

    print("\n--- 最近 7 天学习时长（分钟）---")
    max_day = max(by_day.values()) if by_day else 0
    for day, minutes in by_day.items():
        label = day[5:]  # MM-DD
        print(f"{label} | {bar(minutes, max_day)} {minutes}")

    print("\n--- 学习类型总时长（分钟）---")
    labels = [
        ("course", "课程学习"),
        ("review", "复习巩固"),
        ("skill", "技能拓展"),
    ]
    max_type = max(by_type.values()) if by_type else 0
    for key, cn in labels:
        minutes = by_type.get(key, 0)
        print(f"{cn:>6} | {bar(minutes, max_type)} {minutes}")

    weekly_total = sum(by_day.values())
    goal = WEEKLY_GOAL_MINUTES
    print("\n--- 本周财富进度 ---")
    print(f"学习时长: {weekly_total} / {goal} 分钟")
    print(f"进度条  : {bar(min(weekly_total, goal), goal)}")
    print(
        f"课程门数: {metrics['courses_done']} / {metrics['course_goal']} 门 "
        f"({metrics['week_start']} ~ {metrics['week_end']})"
    )


def show_weekly_tracker(state: dict[str, Any]) -> None:
    metrics = build_week_metrics(state, now_local())
    reminders = weekly_reminders(metrics)

    print("\n--- 本周目标提醒 ---")
    print(f"周期: {metrics['week_start']} ~ {metrics['week_end']}")
    print(
        f"课程目标: {metrics['courses_done']}/{metrics['course_goal']} "
        f"| {bar(metrics['courses_done'], metrics['course_goal'], 20)}"
    )
    print(
        f"时长目标: {metrics['weekly_minutes']}/{metrics['weekly_goal_minutes']} 分钟 "
        f"| {bar(min(metrics['weekly_minutes'], metrics['weekly_goal_minutes']), metrics['weekly_goal_minutes'], 20)}"
    )
    if metrics["done_courses"]:
        print("已完成课程:", "、".join(metrics["done_courses"]))
    else:
        print("已完成课程: 暂无")
    print(f"本周待完成课程任务: {metrics['planned_course_tasks']} 个")
    print("\n提醒建议：")
    for msg in reminders:
        print(f"- {msg}")


def main() -> None:
    state = load_state()
    while True:
        print_header()
        print_auto_weekly_hint(state)
        choice = input("选择操作: ").strip()
        if choice == "1":
            create_quest(state)
        elif choice == "2":
            complete_quest(state)
        elif choice == "3":
            list_quests(state)
        elif choice == "4":
            show_player(state)
        elif choice == "5":
            show_visualization(state)
        elif choice == "6":
            show_weekly_tracker(state)
        elif choice == "7":
            print(f"已退出 {GAME_NAME}。")
            break
        else:
            print("无效选项，请输入 1-7。")


if __name__ == "__main__":
    main()
