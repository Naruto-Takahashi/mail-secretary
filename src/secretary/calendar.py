"""Google カレンダーからの予定取得。"""

from __future__ import annotations

from datetime import datetime, time, timezone
from googleapiclient.discovery import build
from .config import load
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


def insert_calendar_event(
    event_data: dict,
    mail: dict,
    cal=None,
) -> dict:
    """メールから抽出した予定を Google カレンダーに登録する。

    event_data:
      title: str
      start: str (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)
      end: str (optional)
      is_all_day: bool
      location: str (optional)
    """
    if cal is None:
        cal = get_calendar_service()

    title = event_data.get("title", "(タイトルなし)")
    is_all_day = event_data.get("is_all_day", False)
    start_str = event_data.get("start", "")
    end_str = event_data.get("end") or start_str
    location = event_data.get("location", "")

    # 本文・説明
    mail_link = f"https://mail.google.com/mail/?authuser=me#all/{mail.get('threadId', mail.get('id', ''))}"
    description_lines = [
        f"【秘書自動登録】メールから抽出された予定です。",
        f"件名: {mail.get('subject', '')}",
        f"送信元: {mail.get('from', '')}",
        f"スレッド: {mail_link}",
    ]
    if event_data.get("description"):
        description_lines.insert(1, f"詳細: {event_data['description']}")

    body: dict = {
        "summary": title,
        "description": "\n".join(description_lines),
        "extendedProperties": {
            "private": {
                "source": "secretary",
                "mail_id": mail.get("id", ""),
                "thread_id": mail.get("threadId", ""),
            }
        },
    }
    if location:
        body["location"] = location

    # カレンダーの配色設定 (colorId)
    category = event_data.get("category", "other")
    colors_cfg = load("triage").get("calendar", {}).get("colors", {})
    color_id = colors_cfg.get(category, colors_cfg.get("default"))
    if color_id:
        body["colorId"] = str(color_id)


    if is_all_day:
        # 日付のみ (YYYY-MM-DD)
        # Google Calendar の終日予定 end は翌日排他 (exclusive)
        # 単日終日の場合は end を翌日にする
        s_date = start_str.split("T")[0]
        e_date = end_str.split("T")[0]
        if s_date == e_date:
            try:
                from datetime import date, timedelta
                d = date.fromisoformat(s_date)
                e_date = (d + timedelta(days=1)).isoformat()
            except ValueError:
                pass
        body["start"] = {"date": s_date}
        body["end"] = {"date": e_date}
    else:
        # 日時 (ISO 8601)
        # タイムゾーン情報がない場合はローカルタイムゾーンを付与
        local_tz = datetime.now().astimezone().tzinfo
        try:
            if "T" in start_str:
                s_dt = datetime.fromisoformat(start_str)
            else:
                s_dt = datetime.fromisoformat(f"{start_str}T09:00:00")
            if s_dt.tzinfo is None:
                s_dt = s_dt.replace(tzinfo=local_tz)

            if end_str and "T" in end_str:
                e_dt = datetime.fromisoformat(end_str)
            else:
                from datetime import timedelta
                e_dt = s_dt + timedelta(hours=1)
            if e_dt.tzinfo is None:
                e_dt = e_dt.replace(tzinfo=local_tz)

            body["start"] = {"dateTime": s_dt.isoformat()}
            body["end"] = {"dateTime": e_dt.isoformat()}
        except Exception:
            # パースに失敗した場合は終日予定としてフォールバック
            s_date = start_str.split("T")[0]
            body["start"] = {"date": s_date}
            body["end"] = {"date": s_date}

    created = cal.events().insert(calendarId="primary", body=body).execute()
    return created

