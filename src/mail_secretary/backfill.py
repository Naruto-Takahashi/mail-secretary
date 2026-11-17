"""既存メールへの一回限りのラベル付け (`mail-secretary backfill`)。

定期実行 (`run`) との違い:
- Discord には通知しない。state.json にも触れない。
- すでに重要度ラベルが付いたメールは対象外。バッチごとに Gmail へ反映するので、途中で止めても続きから再開できる。
- プロモーション等はルールだけで「無視」にし、AI を呼ばない。
- 古いメール (stale_days より前) の緊急・要対応は、意味がないので「参考」に下げる。
"""

from __future__ import annotations

import sys
import time
from collections import Counter

from . import organize
from .config import load
from .gmail import get_mail, get_service, label_names, list_ids
from .triage import LEVEL_JA, classify, is_sensitive

DAY_MS = 86400 * 1000


def decide(mails: list[dict], cfg: dict, stale_days: int, model: str | None) -> dict[str, dict]:
    bf = cfg["backfill"]
    ignore_cats = set(bf["rule_ignore_categories"])
    ignore_labels = set(bf["rule_ignore_labels"])
    res: dict[str, dict] = {}
    ai = []
    for m in mails:
        if (ignore_cats & set(m["categories"])) or (ignore_labels & set(m["labels"])):
            res[m["id"]] = {"id": m["id"], "level": "ignore", "summary": "(ルール判定)", "reason": "プロモーション等"}
        else:
            ai.append(m)
    if ai:
        res.update(classify(ai, model))
    now = time.time() * 1000
    for m in mails:
        r = res.get(m["id"])
        if r and r["level"] in ("urgent", "action") and now - m["ts"] > stale_days * DAY_MS:
            res[m["id"]] = {**r, "level": "info", "reason": "古いため参考に降格(" + r["reason"] + ")"}
    return res


def cmd_backfill(args) -> None:
    cfg = load("triage")
    stale_days = args.stale_days if args.stale_days is not None else cfg["backfill"]["stale_days"]
    svc = get_service()
    names = label_names(svc)
    done = " ".join(f'-label:"{n}"' for n in cfg["labels"].values() if n)
    q = f"{args.query} -in:trash -in:spam -in:sent -in:drafts older_than:{args.older_than_days}d {done}"
    ids = list_ids(svc, q, args.limit)
    mode = "適用" if args.apply else "dry-run (変更しません)"
    print(f"対象 {len(ids)} 通 / {mode}\n検索: {q}", file=sys.stderr)

    size = cfg["batch_size"]
    stats: Counter = Counter()
    fails = 0
    t0 = time.time()
    for i in range(0, len(ids), size):
        chunk = [get_mail(svc, mid, names) for mid in ids[i : i + size]]
        try:
            res = decide(chunk, cfg, stale_days, args.model)
        except Exception as e:  # AI 失敗は飛ばして続行 (未ラベルのまま残るので、再実行で拾える)
            fails += 1
            print(f"  ! バッチ {i // size + 1} を飛ばしました: {e}", file=sys.stderr)
            if fails >= 3:
                sys.exit("AI の失敗が続いたため中断します。再実行すると続きから処理します。")
            continue
        fails = 0
        items = organize.plan(chunk, res)
        if args.apply:
            organize.apply(svc, items)
        for p in items:
            stats[p["level"]] += 1
            stats["archived"] += p["archive"]
            stats["protected"] += p["protected"]
        if args.verbose or not args.apply:
            for m in chunk:
                r = res.get(m["id"])
                if r:
                    print(f"[{LEVEL_JA[r['level']]}] {m['from'][:28]:<28} | "
                          f"{'(認証コード)' if is_sensitive(m['subject']) else m['subject'][:46]}")
        done_n = min(i + size, len(ids))
        el = time.time() - t0
        eta = el / done_n * (len(ids) - done_n) if done_n else 0
        print(f"  進捗 {done_n}/{len(ids)} ({el/60:.1f}分経過 / 残り約{eta/60:.0f}分)", file=sys.stderr)

    print("== 集計 ==", file=sys.stderr)
    for lv, ja in LEVEL_JA.items():
        print(f"  {ja}: {stats[lv]}", file=sys.stderr)
    print(f"  受信トレイから外す: {stats['archived']} / 保護で残す: {stats['protected']}"
          + ("" if args.apply else "  (dry-run)"), file=sys.stderr)
