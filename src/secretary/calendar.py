"""Google カレンダーからの予定取得。"""

from __future__ import annotations

from datetime import datetime, time, timezone
from googleapiclient.discovery import build
from .gmail import get_credentials


def get_calendar_service():
    creds = get_credentials()
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def fetch_today_events(cal=None) -> list[dict]:
    """今日の予定 (開始時刻順) を取得する。"""
    if cal is None:
        cal = get_calendar_service()

    # ローカルタイムゾーンで今日の開始・終了時刻を計算
    now = datetime.now().astimezone()
    tz = now.tzinfo
    today_start = datetime.combine(now.date(), time.min, tzinfo=tz)
    today_end = datetime.combine(now.date(), time.max, tzinfo=tz)

    res = (
        cal.events()
        .list(
            calendarId="primary",
            timeMin=today_start.isoformat(),
            timeMax=today_end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    items = res.get("items", [])
    events = []
    for it in items:
        # 辞退した予定はスキップ
        attendees = it.get("attendees", [])
        if any(a.get("self") and a.get("responseStatus") == "declined" for a in attendees):
            continue

        start = it.get("start", {})
        end = it.get("end", {})
        summary = it.get("summary", "(タイトルなし)")

        # 終日予定か時間指定予定か
        is_all_day = "date" in start
        time_str = "終日"
        if not is_all_day and "dateTime" in start:
            s_dt = datetime.fromisoformat(start["dateTime"])
            e_dt = datetime.fromisoformat(end.get("dateTime", start["dateTime"]))
            time_str = f"{s_dt:%H:%M}〜{e_dt:%H:%M}"

        events.append(
            {
                "id": it["id"],
                "summary": summary,
                "time": time_str,
                "is_all_day": is_all_day,
                "location": it.get("location", ""),
                "link": it.get("htmlLink", ""),
            }
        )
    return events
