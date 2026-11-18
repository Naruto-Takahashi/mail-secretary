"""朝のデイリーブリーフィング生成 (`secretary briefing`)。

Google カレンダーの予定 + Obsidian 外部脳のタスク + 要対応メールを1通にまとめ、Discord に通知する。
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

from . import calendar, discord, notify
from .config import HOME, STATE, load


def get_obsidian_tasks(today_str: str | None = None) -> list[str]:
    """Obsidian Vault の今日の日誌 (40-daily/YYYY/MM/YYYY-MM-DD.md) から未完了タスクを取得する。"""
    vault = Path("~/ghq/github.com/Naruto-Takahashi/obsidian-vault").expanduser()
    if not vault.exists():
        return []

    now = datetime.now()
    today_str = today_str or f"{now:%Y-%m-%d}"
    year_str, month_str = today_str.split("-")[:2]
    note_path = vault / "40-daily" / year_str / month_str / f"{today_str}.md"

    if not note_path.exists():
        return []

    tasks = []
    in_tasks_section = False
    for line in note_path.read_text().splitlines():
        if line.startswith("## "):
            in_tasks_section = line.strip() == "## Tasks"
            continue
        if in_tasks_section:
            # - [ ] タスク内容
            m = re.match(r"^\s*-\s*\[ \]\s*(.+)$", line)
            if m:
                task_text = m.group(1).strip()
                if task_text:
                    tasks.append(task_text)
    return tasks


def get_pending_action_mails() -> list[dict]:
    """state.json から要対応 (pending) のメール一覧を取得する。"""
    st = notify.load_state()
    pend = st.get("pending", [])
    if not pend:
        return []
    return notify.group_by_thread(pend)


def build_briefing_message(now: datetime | None = None) -> str:
    now = now or datetime.now().astimezone()
    date_str = f"{now:%Y-%m-%d} ({['月','火','水','木','金','土','日'][now.weekday()]})"

    sections = [f"☀️ **今日のブリーフィング** ({date_str})"]

    # 1. カレンダーの予定
    try:
        events = calendar.fetch_today_events()
        if events:
            lines = ["📅 **本日の予定**"]
            for ev in events:
                loc = f" ({ev['location']})" if ev['location'] else ""
                lines.append(f"・`{ev['time']}` **{ev['summary']}**{loc}")
            sections.append("\n".join(lines))
        else:
            sections.append("📅 **本日の予定**: 終日の予定はありません")
    except Exception as e:
        print(f"[briefing] カレンダー取得エラー: {e}", file=sys.stderr)
        sections.append(f"📅 **本日の予定**: (取得エラー: {e})")

    # 2. Obsidian 外部脳のタスク
    tasks = get_obsidian_tasks()
    if tasks:
        lines = ["📝 **本日のToDo (Obsidian)**"]
        for t in tasks:
            lines.append(f"・{t}")
        sections.append("\n".join(lines))
    else:
        sections.append("📝 **本日のToDo**: 未完了タスクはありません")

    # 3. 要対応メール
    mails = get_pending_action_mails()
    if mails:
        lines = [f"📬 **要対応のメール ({len(mails)}件)**"]
        for m in mails:
            sender = notify.short_from(m.get("from", ""))
            subj = m.get("subject", "")[:35]
            lines.append(f"・[{m.get('kind', 'other')}] **{sender}**: {subj}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def cmd_briefing(args) -> None:
    msg = build_briefing_message()
    print(msg)
    if getattr(args, "apply", False):
        print("\nDiscord に投稿します...", file=sys.stderr)
        discord.post(msg, ping=False)
        print("投稿完了", file=sys.stderr)
    else:
        print("\n(dry-run: --apply で実際に Discord に送信されます)", file=sys.stderr)
