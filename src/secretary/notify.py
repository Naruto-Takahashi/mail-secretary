"""定期実行の本体 (`mail-secretary run`)。

1 回の実行 = 新着の取得 → AI 判定 → Gmail 整理 → 緊急の即時通知 → (時刻が来ていれば) 要対応のまとめ通知。
`--apply` を付けない限り、メールの変更・Discord への投稿・状態の保存は一切しない (dry-run)。
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta

from . import calendar, discord, organize
from .config import STATE, load
from .gmail import fetch_recent, get_service
from .triage import LEVEL_JA, LEVELS, classify

ICON = {"urgent": "🚨", "action": "📌"}
_account = ""  # Gmail へのリンクの authuser。実行時にプロフィールから取得する


# ----------------------------------------------------------------- state ---

def load_state() -> dict:
    try:
        s = json.loads(STATE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        s = {}
    return {
        "seen": s.get("seen", []),
        "pending": s.get("pending", []),
        "last_digest": s.get("last_digest"),
        "calendar_added": s.get("calendar_added", []),
    }


def save_state(s: dict) -> None:
    s["seen"] = s["seen"][-3000:]
    s["calendar_added"] = s.get("calendar_added", [])[-1000:]
    STATE.write_text(json.dumps(s, ensure_ascii=False))
    STATE.chmod(0o600)


# ------------------------------------------------------------ formatting ---

def short_from(f: str) -> str:
    m = re.match(r'\s*"?([^"<]+?)"?\s*<', f)
    return (m.group(1) if m else f).strip()[:30]


def link(thread_id: str) -> str:
    return f"https://mail.google.com/mail/?authuser={_account}#all/{thread_id}"


def group_by_thread(items: list[dict]) -> list[dict]:
    """同一スレッドは1件にまとめる。最も高い重要度を採用し、要約は最新(先頭)のものを使う。items は新しい順。"""
    groups: dict[str, list[dict]] = {}
    for it in items:
        groups.setdefault(it["threadId"], []).append(it)
    out = []
    for g in groups.values():
        best = min(LEVELS.index(x["level"]) for x in g)
        out.append({**g[0], "level": LEVELS[best], "count": len(g)})
    return sorted(out, key=lambda x: LEVELS.index(x["level"]))


def render(items: list[dict]) -> list[str]:
    lines = []
    for it in group_by_thread(items):
        more = f" ／ 同スレッド{it['count']}通" if it["count"] > 1 else ""
        kind = load("triage")["kinds"].get(it.get("kind", "other"), {}).get("label", "")
        lines.append(
            f"{ICON[it['level']]} **{LEVEL_JA[it['level']]}**{' ・' + kind if kind else ''} {discord.md_escape(short_from(it['from']))}\n"
            f"　{discord.md_escape(it['subject'][:60])}\n"
            f"　└ {discord.md_escape(it['summary'])}{more} ([開く](<{link(it['threadId'])}>))"
        )
    out, cur = [], ""
    for ln in lines:
        if cur and len(cur) + len(ln) + 2 > 1800:
            out.append(cur)
            cur = ""
        cur += ("\n\n" if cur else "") + ln
    return out + ([cur] if cur else [])


def to_item(m: dict, r: dict) -> dict:
    return {"id": m["id"], "threadId": m["threadId"], "from": m["from"], "subject": m["subject"],
            "date": m["date"], "level": r["level"], "kind": r.get("kind", "other"), "summary": r["summary"]}


# ---------------------------------------------------------------- digest ---

def due_digest(now: datetime, times: list[str], last: str | None) -> datetime | None:
    """直近の予定時刻 (<= now) が、前回のまとめより新しければそれを返す。"""
    cands = []
    for t in times:
        h, m = map(int, t.split(":"))
        d = now.replace(hour=h, minute=m, second=0, microsecond=0)
        cands += [d, d - timedelta(days=1)]
    latest = max(c for c in cands if c <= now)
    if last is None or datetime.fromisoformat(last) < latest:
        return latest
    return None


# ------------------------------------------------------------------- run ---

def post_all(bodies: list[str], head: str, ping: bool, apply: bool) -> None:
    for k, body in enumerate([head] + bodies):
        if apply:
            discord.post(body, ping=(ping and k == 0))
        else:
            print("-----(dry-run: 送信しません)-----\n" + body)


def cmd_run(args) -> None:
    cfg_n, cfg_t = load("notify"), load("triage")
    run_cfg = cfg_n["run"]
    global _account
    svc = get_service()
    _account = svc.users().getProfile(userId="me").execute()["emailAddress"]
    state = load_state()
    seen = set(state["seen"])

    mails = fetch_recent(svc, args.hours or run_cfg["lookback_hours"], run_cfg["max_mails"])
    new = mails if args.ignore_seen else [m for m in mails if m["id"] not in seen]
    print(f"[{datetime.now():%F %T}] 新着 {len(new)} 通", file=sys.stderr)

    if new:
        res = classify(new)
        items = organize.plan(new, res)
        n_arch = sum(p["archive"] for p in items)
        n_prot = sum(p["kept_by_star"] for p in items)
        print(f"整理: ラベル付与 {sum(bool(p['add']) for p in items)} 通 / 受信トレイから外す {n_arch} 通 / 保護で残す {n_prot} 通"
              + ("" if args.apply else " (dry-run)"), file=sys.stderr)
        if args.apply:
            organize.apply(svc, items)

        # カレンダー登録対象・日程調整候補の検出
        confirmed_events = []
        tentative_events = []
        already_added = set(state.get("calendar_added", []))
        for m in new:
            r = res.get(m["id"])
            if not r:
                continue
            ev = r.get("calendar_event")
            if ev and isinstance(ev, dict) and ev.get("title"):
                status = ev.get("status", "confirmed")
                if status == "tentative":
                    tentative_events.append((m, ev))
                elif ev.get("start") and m["id"] not in already_added:
                    confirmed_events.append((m, ev))

        # 1. 確定予定: Google カレンダーへ自動登録 & 通知
        if confirmed_events:
            cal_lines = ["📅 **カレンダーに予定を登録しました**" if args.apply else "📅 **カレンダー登録の候補 (確定・dry-run)**"]
            cal_svc = None
            if args.apply:
                try:
                    cal_svc = calendar.get_calendar_service()
                except Exception as e:
                    print(f"[calendar] カレンダーサービス取得エラー: {e}", file=sys.stderr)

            for m, ev in confirmed_events:
                title = ev.get("title")
                st = ev.get("start")
                loc = f" ({ev['location']})" if ev.get("location") else ""
                desc = f"\n　└ {ev['description']}" if ev.get("description") else ""
                cal_lines.append(f"・`{st}` **{title}**{loc}{desc}\n　([メールを開く](<{link(m['threadId'])}>))")

                if args.apply and cal_svc:
                    try:
                        calendar.insert_calendar_event(ev, m, cal=cal_svc)
                        state["calendar_added"].append(m["id"])
                        print(f"[calendar] 登録完了: {title} ({st})", file=sys.stderr)
                    except Exception as e:
                        print(f"[calendar] 登録失敗 ({title}): {e}", file=sys.stderr)

            cal_msg = "\n".join(cal_lines)
            if args.apply:
                discord.post(cal_msg, ping=False)
            else:
                print("-----(dry-run: 確定カレンダー予定)-----\n" + cal_msg)

        # 2. 未確定・調整中予定: カレンダーには入れず、Discord でユーザーに確認を促す
        if tentative_events:
            tent_lines = ["🤔 **日程調整の確認が必要です (未確定の予定)**"]
            for m, ev in tentative_events:
                title = ev.get("title")
                loc = f" ({ev['location']})" if ev.get("location") else ""
                cands = ev.get("candidates") or []
                cands_str = f"\n　・候補日時: {', '.join(f'`{c}`' for c in cands)}" if cands else ""
                desc = f"\n　└ {ev['description']}" if ev.get("description") else ""
                tent_lines.append(
                    f"・**{title}**{loc}{cands_str}{desc}\n"
                    f"　└ 対応: 都合の良い日程を確認・返信してください ([メールを開く](<{link(m['threadId'])}>))"
                )

            tent_msg = "\n".join(tent_lines)
            if args.apply:
                discord.post(tent_msg, ping=True)
            else:
                print("-----(dry-run: 日程調整の確認)-----\n" + tent_msg)

        judged = [to_item(m, res[m["id"]]) for m in new if m["id"] in res]
        urgent = [x for x in judged if x["level"] == "urgent"]
        digest_levels = set(cfg_n["digest"]["levels"])
        later = [x for x in judged if x["level"] in digest_levels]

        if urgent:
            n_threads = len(group_by_thread(urgent))
            post_all(render(urgent), f"🚨 **緊急** {n_threads}件", cfg_n["urgent"]["ping"], args.apply)
        if args.apply:
            known = {x["threadId"] for x in state["pending"]}
            state["pending"] += [x for x in later if x["id"] not in {p["id"] for p in state["pending"]}]
            state["seen"] += [m["id"] for m in new]
            save_state(state)
        else:
            print(f"(要対応 {len(later)} 通は次のまとめ通知に回します)", file=sys.stderr)
            state["pending"] = state["pending"] + later  # dry-run 内での表示用。保存はしない

    # --- まとめ通知 (要対応) ---
    now = datetime.now().astimezone()
    sched = due_digest(now, cfg_n["digest"]["times"], state["last_digest"])
    if sched or args.digest_now:
        pend = state["pending"]
        if pend:
            n_threads = len(group_by_thread(pend))
            post_all(render(pend), f"🗓 **要対応のまとめ** {n_threads}件", cfg_n["digest"]["ping"], args.apply)
        else:
            print("まとめ通知: 要対応なし", file=sys.stderr)
        if args.apply:
            state["pending"] = []
            state["last_digest"] = (sched or now).isoformat()
            save_state(state)
