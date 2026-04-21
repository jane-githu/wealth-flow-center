#!/usr/bin/env python3
"""Desktop floating study reminder for 财富流通中心."""

from __future__ import annotations

import argparse
import json
import tkinter as tk
from datetime import datetime
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


DEFAULT_API_URL = "http://127.0.0.1:4318/api/state"
DEFAULT_POLL_SECONDS = 15
TIME_FMT = "%Y-%m-%d %H:%M"


def parse_api_time(raw: Any) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    for fmt in (TIME_FMT, "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)

    parts: list[str] = []
    if days > 0:
        parts.append(f"{days}天")
    if hours > 0 or days > 0:
        parts.append(f"{hours}小时")
    parts.append(f"{mins}分")
    parts.append(f"{secs}秒")
    return "".join(parts)


class DesktopStudyOverlay:
    def __init__(self, api_url: str, poll_seconds: int) -> None:
        self.api_url = api_url
        self.poll_ms = max(5000, poll_seconds * 1000)
        self.latest_state: dict[str, Any] | None = None
        self.last_fetch_ok = False

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.96)
        self.root.configure(bg="#2b5f58")

        self._drag_dx = 0
        self._drag_dy = 0

        self._build_ui()
        self._place_window()
        self._bind_drag()
        self.root.bind("<Escape>", lambda _e: self.root.destroy())

        self._poll_state()
        self._tick()

    def _build_ui(self) -> None:
        shell = tk.Frame(self.root, bg="#2b5f58", bd=0, padx=1, pady=1)
        shell.pack(fill="both", expand=True)

        card = tk.Frame(shell, bg="#fffaf0", padx=10, pady=8)
        card.pack(fill="both", expand=True)
        self.shell = shell
        self.card = card

        header = tk.Frame(card, bg="#fffaf0")
        header.pack(fill="x")
        self.header = header

        self.title_label = tk.Label(
            header,
            text="学习桌面提醒",
            bg="#fffaf0",
            fg="#244255",
            font=("PingFang SC", 12, "bold"),
        )
        self.title_label.pack(side="left")

        close_btn = tk.Button(
            header,
            text="×",
            command=self.root.destroy,
            bg="#fffaf0",
            fg="#51697a",
            activebackground="#fff0e9",
            activeforeground="#bb4a4a",
            font=("PingFang SC", 12, "bold"),
            relief="flat",
            bd=0,
            padx=6,
            pady=0,
            cursor="hand2",
        )
        close_btn.pack(side="right")

        self.remain_var = tk.StringVar(value="-")
        self.idle_var = tk.StringVar(value="-")
        self.task_var = tk.StringVar(value="当前：等待数据")
        self.next_var = tk.StringVar(value="下次学习：等待数据")

        kv_row = tk.Frame(card, bg="#fffaf0")
        kv_row.pack(fill="x", pady=(8, 4))

        self._build_kv_box(kv_row, "本次学习剩余", self.remain_var).pack(
            side="left", fill="x", expand=True, padx=(0, 5)
        )
        self._build_kv_box(kv_row, "未学习时长", self.idle_var).pack(
            side="left", fill="x", expand=True, padx=(5, 0)
        )

        task_label = tk.Label(
            card,
            textvariable=self.task_var,
            bg="#fffaf0",
            fg="#2c4558",
            font=("PingFang SC", 11),
            anchor="w",
            justify="left",
            wraplength=292,
        )
        task_label.pack(fill="x", pady=(2, 2))

        next_label = tk.Label(
            card,
            textvariable=self.next_var,
            bg="#fffaf0",
            fg="#5a6d7c",
            font=("PingFang SC", 10),
            anchor="w",
            justify="left",
            wraplength=292,
        )
        next_label.pack(fill="x")

    def _build_kv_box(self, parent: tk.Widget, key: str, value_var: tk.StringVar) -> tk.Frame:
        box = tk.Frame(parent, bg="#ffffff", bd=1, relief="solid", padx=7, pady=5)
        tk.Label(
            box,
            text=key,
            bg="#ffffff",
            fg="#657686",
            font=("PingFang SC", 9),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            box,
            textvariable=value_var,
            bg="#ffffff",
            fg="#1f3c4f",
            font=("PingFang SC", 13, "bold"),
            anchor="w",
        ).pack(fill="x")
        return box

    def _place_window(self) -> None:
        self.root.update_idletasks()
        width = 320
        height = 158
        screen_w = self.root.winfo_screenwidth()
        self.root.geometry(f"{width}x{height}+{screen_w - width - 24}+70")

    def _bind_drag(self) -> None:
        targets = [self.root, self.header, self.title_label]
        for widget in targets:
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._on_drag)

    def _start_drag(self, event: tk.Event[Any]) -> None:
        self._drag_dx = event.x_root - self.root.winfo_x()
        self._drag_dy = event.y_root - self.root.winfo_y()

    def _on_drag(self, event: tk.Event[Any]) -> None:
        x = event.x_root - self._drag_dx
        y = event.y_root - self._drag_dy
        self.root.geometry(f"+{x}+{y}")

    def _fetch_state(self) -> dict[str, Any]:
        req = Request(self.api_url, headers={"Cache-Control": "no-cache"})
        with urlopen(req, timeout=3) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if not payload.get("ok"):
            raise RuntimeError(str(payload.get("error", "状态接口返回失败")))
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("状态数据格式异常")
        return data

    def _find_running_quest(self, quests: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
        rows: list[tuple[datetime, dict[str, Any]]] = []
        for q in quests:
            if str(q.get("status", "")) == "done":
                continue
            start = parse_api_time(q.get("start"))
            end = parse_api_time(q.get("end"))
            if not start or not end:
                continue
            if start <= now <= end:
                rows.append((end, q))
        rows.sort(key=lambda row: row[0])
        return rows[0][1] if rows else None

    def _find_next_quest(self, quests: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
        rows: list[tuple[datetime, dict[str, Any]]] = []
        for q in quests:
            if str(q.get("status", "")) == "done":
                continue
            start = parse_api_time(q.get("start"))
            if not start or start <= now:
                continue
            rows.append((start, q))
        rows.sort(key=lambda row: row[0])
        return rows[0][1] if rows else None

    def _last_study_time(self, quests: list[dict[str, Any]]) -> datetime | None:
        latest: datetime | None = None
        for q in quests:
            if str(q.get("status", "")) != "done":
                continue
            point = parse_api_time(q.get("completed_at") or q.get("end") or q.get("start"))
            if not point:
                continue
            if latest is None or point > latest:
                latest = point
        return latest

    def _set_mode(self, running: bool) -> None:
        if running:
            self.root.configure(bg="#1f745f")
            self.shell.configure(bg="#1f745f")
        else:
            self.root.configure(bg="#7f5527")
            self.shell.configure(bg="#7f5527")

    def _render_from_state(self) -> None:
        if not self.latest_state:
            self._set_mode(False)
            self.remain_var.set("-")
            self.idle_var.set("暂无记录")
            self.task_var.set("当前：等待连接学习服务")
            self.next_var.set("下次学习：请先启动财富流通中心服务")
            return

        quests = list(self.latest_state.get("quests") or [])
        now = datetime.now()
        running = self._find_running_quest(quests, now)
        next_quest = self._find_next_quest(quests, now)

        if running:
            self._set_mode(True)
            end = parse_api_time(running.get("end"))
            remain = format_duration((end - now).total_seconds()) if end else "-"
            self.remain_var.set(remain)
            self.idle_var.set("进行中")
            self.task_var.set(f"当前：{str(running.get('title', '学习中')).strip()}")
        else:
            self._set_mode(False)
            self.remain_var.set("-")
            self.task_var.set("当前：未在学习")
            last_point = self._last_study_time(quests)
            if last_point is None:
                self.idle_var.set("暂无记录")
            else:
                self.idle_var.set(format_duration((now - last_point).total_seconds()))

        if next_quest:
            next_start = parse_api_time(next_quest.get("start"))
            wait = format_duration((next_start - now).total_seconds()) if next_start else "-"
            title = str(next_quest.get("title", "未命名任务")).strip()
            self.next_var.set(f"下次学习：{wait} 后 · {title}")
        else:
            self.next_var.set("下次学习：暂无待学习任务")

        if not self.last_fetch_ok:
            self.next_var.set(self.next_var.get() + "（离线缓存）")

    def _poll_state(self) -> None:
        try:
            self.latest_state = self._fetch_state()
            self.last_fetch_ok = True
        except (URLError, TimeoutError, RuntimeError, json.JSONDecodeError):
            self.last_fetch_ok = False

        self._render_from_state()
        self.root.after(self.poll_ms, self._poll_state)

    def _tick(self) -> None:
        self._render_from_state()
        self.root.after(1000, self._tick)

    def run(self) -> None:
        self.root.mainloop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="财富流通中心桌面悬浮学习提醒")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help=f"状态接口地址，默认 {DEFAULT_API_URL}")
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS, help="接口轮询秒数，默认 15")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = DesktopStudyOverlay(api_url=args.api_url, poll_seconds=int(args.poll_seconds))
    app.run()


if __name__ == "__main__":
    main()
