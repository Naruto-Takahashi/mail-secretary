from __future__ import annotations

import argparse

from . import filters, labels
from .backfill import cmd_backfill
from .gmail import get_service
from .notify import cmd_run
from .triage import LEVEL_JA, classify, is_sensitive


def cmd_auth(_a) -> None:
    from .calendar import get_calendar_service
    prof = get_service().users().getProfile(userId="me").execute()
    cal = get_calendar_service()
    cal_info = cal.calendars().get(calendarId="primary").execute()
    print(f"Gmail認可OK: {prof['emailAddress']}")
    print(f"カレンダー認可OK: {cal_info.get('summary', 'primary')}")


def cmd_triage(a) -> None:
    """判定結果を一覧表示するだけ (メールは変更しない。通知もしない)。"""
    from .gmail import fetch_recent

    mails = fetch_recent(get_service(), a.hours, a.limit)
    res = classify(mails, a.model)
    order = {k: i for i, k in enumerate(LEVEL_JA)}
    for m in sorted(mails, key=lambda m: order.get(res.get(m["id"], {}).get("level", "info"), 9)):
        r = res.get(m["id"])
        subj = "(認証コードの通知。件名は非表示)" if is_sensitive(m["subject"]) else m["subject"][:50]
        print(f"[{LEVEL_JA[r['level']] if r else '未判定'} / {r['kind'] if r else '-'}] {m['from'][:28]:<28} | {subj}")
        if r:
            print(f"        └ {r['summary']} ／ {r['reason']}")


def main() -> int:
    p = argparse.ArgumentParser(prog="secretary")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auth", help="初回認可").set_defaults(fn=cmd_auth)
    sub.add_parser("dump", help="既存フィルタの一覧").set_defaults(fn=filters.cmd_dump)

    fp = sub.add_parser("filters", help="フィルタ同期 (config/filters.toml)")
    fs = fp.add_subparsers(dest="sub", required=True)
    fs.add_parser("plan", help="差分の表示").set_defaults(fn=filters.cmd_plan)
    fa = fs.add_parser("apply", help="差分の適用")
    fa.add_argument("--yes", action="store_true")
    fa.set_defaults(fn=filters.cmd_apply)

    rp = sub.add_parser("restore-inbox", help="重要な GitHub 通知を受信トレイに戻す")
    rp.add_argument("--days", type=int, default=14)
    rp.add_argument("--include-read", action="store_true")
    rp.add_argument("--yes", action="store_true")
    rp.set_defaults(fn=filters.cmd_restore_inbox)

    lp = sub.add_parser("labels", help="ラベルの同期 (旧ラベルの改名・作成・色)")
    ls = lp.add_subparsers(dest="sub", required=True)
    lsync = ls.add_parser("sync", help="差分の表示。--yes で適用")
    lsync.add_argument("--yes", action="store_true")
    lsync.set_defaults(fn=labels.cmd_sync)

    tp = sub.add_parser("triage", help="直近のメールを判定して表示するだけ")
    tp.add_argument("--hours", type=int, default=48)
    tp.add_argument("--limit", type=int, default=30)
    tp.add_argument("--model", default=None)
    tp.set_defaults(fn=cmd_triage)

    up = sub.add_parser("run", help="定期実行の本体 (判定・整理・通知)。--apply なしは dry-run")
    up.add_argument("--apply", action="store_true", help="実際に Gmail を変更し、Discord に投稿し、状態を保存する")
    up.add_argument("--hours", type=int, default=None, help="遡る時間 (既定: config/notify.toml)")
    up.add_argument("--ignore-seen", action="store_true", help="処理済みのメールも対象にする")
    up.add_argument("--digest-now", action="store_true", help="時刻に関係なく要対応のまとめを今送る")
    up.set_defaults(fn=cmd_run)

    bp = sub.add_parser("backfill", help="既存メールへの一回限りのラベル付け (通知しない)。--apply なしは dry-run")
    bp.add_argument("--query", default=None, help="対象の Gmail 検索式 (既定: 受信トレイ + 種類ラベルが付いたメール)")
    bp.add_argument("--reset", action="store_true", help="処理済みの記録を消して最初からやり直す")
    bp.add_argument("--older-than-days", type=int, default=2, help="これより新しいメールは定期実行に任せる")
    bp.add_argument("--stale-days", type=int, default=None, help="これより古い緊急・要対応は参考に下げる (既定: 設定値)")
    bp.add_argument("--limit", type=int, default=100000, help="処理する最大件数 (dry-run の試し打ちに)")
    bp.add_argument("--model", default=None)
    bp.add_argument("--verbose", action="store_true")
    bp.add_argument("--apply", action="store_true", help="実際に Gmail へ反映する")
    bp.set_defaults(fn=cmd_backfill)

    br = sub.add_parser("briefing", help="朝のデイリーブリーフィング (予定・タスク・要対応メールのまとめ)。--apply なしは dry-run")
    br.add_argument("--apply", action="store_true", help="実際に Discord に投稿する")
    from .briefing import cmd_briefing
    br.set_defaults(fn=cmd_briefing)

    args = p.parse_args()
    args.fn(args)
    return 0
