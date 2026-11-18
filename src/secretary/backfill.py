"""既存メールへの一回限りのラベル付け (`mail-secretary backfill`)。

定期実行 (`run`) との違い:
- Discord には通知しない。state.json にも触れない。
- バッチごとに Gmail へ反映し、処理済みの id を記録するので、途中で止めても続きから再開できる (--reset で記録を消す)。
- プロモーション等はルールだけで「無視・広告 (SNS)」にし、AI を呼ばない。
- 古いメール (stale_days より前) の P0・P1 は、意味がないので P2 に下げる。
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter

from . import organize
from .config import SECRET_DIR, load
from .gmail import get_mail, get_service, label_names, list_ids
from .triage import LEVEL_JA, classify, is_sensitive

DAY_MS = 86400 * 1000
DONE = SECRET_DIR / "backfill-done.json"


def decide(mails: list[dict], cfg: dict, stale_days: int, model: str | None) -> dict[str, dict]:
    bf = cfg["backfill"]
    ignore_cats = set(bf["rule_ignore_categories"])
    ignore_labels = set(bf["rule_ignore_labels"])
    res: dict[str, dict] = {}
    ai = []
    for m in mails:
        if (ignore_cats & set(m["categories"])) or (ignore_labels & set(m["labels"])):
            kind = "social" if "CATEGORY_SOCIAL" in m["categories"] else "promo"
            res[m["id"]] = {"id": m["id"], "level": "ignore", "kind": kind, "summary": "(ルール判定)", "reason": "プロモーション等"}
        else:
            ai.append(m)
    if ai:
        res.update(classify(ai, model))
    now = time.time() * 1000
    for m in mails:
        r = res.get(m["id"])
        if r and r["level"] in ("urgent", "action") and now - m["ts"] > stale_days * DAY_MS:
            res[m["id"]] = {**r, "level": "info", "reason": "古いため P2 に降格(" + r["reason"] + ")"}
    return res


def scope_query(args, cfg: dict) -> str:
    if args.query:
        return args.query
    # 既定: 受信トレイ + 種類ラベルが付いているメール (旧ラベルを含む)
    names = {v["label"] for v in cfg["kinds"].values() if v["label"]} | set(cfg["legacy"]["old_kind_labels"])
    return "(in:inbox OR " + " OR ".join(f'label:"{n}"' for n in sorted(names)) + ")"


def cmd_backfill(args) -> None:
    cfg = load("triage")
    stale_days = args.stale_days if args.stale_days is not None else cfg["backfill"]["stale_days"]
    svc = get_service()
    names = label_names(svc)
    if args.reset and DONE.exists():
        DONE.unlink()
    done = set(json.loads(DONE.read_text())) if DONE.exists() else set()
    q = f"{scope_query(args, cfg)} -in:trash -in:spam -in:sent -in:drafts older_than:{args.older_than_days}d"
    ids = [i for i in list_ids(svc, q, args.limit) if i not in done]
    mode = "適用" if args.apply else "dry-run (変更しません)"
    print(f"対象 {len(ids)} 通 (処理済み {len(done)} 通を除く) / {mode}\n検索: {q}", file=sys.stderr)

    size = cfg["batch_size"]
    stats: Counter = Counter()
    fails = 0
    t0 = time.time()
    for i in range(0, len(ids), size):
        chunk = [get_mail(svc, mid, names) for mid in ids[i : i + size]]
        try:
            res = decide(chunk, cfg, stale_days, args.model)
        except Exception as e:  # AI 失敗は飛ばして続行 (処理済みに記録しないので、再実行で拾える)
            fails += 1
            print(f"  ! バッチ {i // size + 1} を飛ばしました: {e}", file=sys.stderr)
            if fails >= 3:
                sys.exit("AI の失敗が続いたため中断します。再実行すると続きから処理します。")
            continue
        fails = 0
        items = organize.plan(chunk, res)
        if args.apply:
            organize.apply(svc, items)
            done |= {m["id"] for m in chunk if m["id"] in res}
            DONE.write_text(json.dumps(sorted(done)))
            DONE.chmod(0o600)
        by_id = {p["id"]: p for p in items}
        for p in items:
            stats[p["level"]] += 1
            stats["kind:" + p["kind"]] += 1
            stats["archived"] += p["archive"]
            stats["starred"] += p["kept_by_star"]
        if args.verbose or not args.apply:
            for m in chunk:
                r, p = res.get(m["id"]), by_id.get(m["id"])
                if r:
                    subj = "(認証コード)" if is_sensitive(m["subject"]) else m["subject"][:42]
                    mark = "→外す" if p["archive"] else "残す"
                    print(f"[{LEVEL_JA[r['level']][:2]} {r['kind']:<9}] {mark} {m['from'][:22]:<22} | {subj}")
        n = min(i + size, len(ids))
        el = time.time() - t0
        eta = el / n * (len(ids) - n) if n else 0
        print(f"  進捗 {n}/{len(ids)} ({el/60:.1f}分経過 / 残り約{eta/60:.0f}分)", file=sys.stderr)

    print("== 集計 ==", file=sys.stderr)
    for lv, ja in LEVEL_JA.items():
        print(f"  {ja}: {stats[lv]}", file=sys.stderr)
    kinds = {k[5:]: v for k, v in stats.items() if k.startswith("kind:")}
    print("  種類:", dict(sorted(kinds.items(), key=lambda x: -x[1])), file=sys.stderr)
    print(f"  受信トレイから外す: {stats['archived']} / スターで残す: {stats['starred']}" + ("" if args.apply else "  (dry-run)"), file=sys.stderr)
