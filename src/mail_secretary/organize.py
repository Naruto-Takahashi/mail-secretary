"""判定結果の Gmail への反映 (種類ラベル・重要度ラベルの付与と、受信トレイからの整理)。"""

from __future__ import annotations

import json
import sys
import time

from .config import ORGANIZE_LOG, load
from .gmail import batch_modify, ensure_label, label_ids

DAY_MS = 86400 * 1000


def label_of(cfg: dict, axis: str, key: str) -> str:
    return cfg[axis].get(key, {}).get("label", "")


def managed_labels(cfg: dict) -> set[str]:
    """この秘書が付け外しするラベル (種類・重要度・旧種類ラベル)。"""
    names = {v["label"] for v in cfg["levels"].values() if v["label"]}
    names |= {v["label"] for v in cfg["kinds"].values() if v["label"]}
    return names | set(cfg["legacy"]["old_kind_labels"])


def keep_in_inbox(cfg: dict, level: str, kind: str, ts: int, starred: bool) -> bool:
    org = cfg["organize"]
    if starred or level in ("urgent", "action"):
        return True
    age_days = (time.time() * 1000 - ts) / DAY_MS
    return level == "info" and kind in org["inbox_keep_kinds"] and age_days <= org["inbox_keep_days"]


def plan(mails: list[dict], res: dict[str, dict]) -> list[dict]:
    cfg = load("triage")
    managed = managed_labels(cfg)
    out = []
    for m in mails:
        r = res.get(m["id"])
        if not r:
            continue
        lv, kind = r["level"], r.get("kind", "other")
        add = [n for n in (label_of(cfg, "levels", lv), label_of(cfg, "kinds", kind)) if n]
        remove = sorted((managed & set(m["labels"])) - set(add))  # 送信元などで付いていた誤ったラベルを外す
        starred = "STARRED" in m["labels"]
        keep = keep_in_inbox(cfg, lv, kind, m["ts"], starred)
        out.append(
            {
                "id": m["id"], "threadId": m["threadId"], "level": lv, "kind": kind, "add": add, "remove": remove,
                "archive": "INBOX" in m["labels"] and not keep,
                "kept_by_star": starred and lv not in ("urgent", "action"),
            }
        )
    return out


def deduplicate_thread_priority(svc, thread_ids: set[str], cfg: dict, ids: dict[str, str]) -> None:
    """スレッド内で重要度ラベル (P0 / P1 / P2) が混在している場合、最上位だけ残して下位を外す。"""
    priority = [cfg["levels"][k]["label"] for k in ("urgent", "action", "info") if cfg["levels"][k].get("label")]
    p_label_ids = {ids[name]: name for name in priority if name in ids}
    if not p_label_ids:
        return

    for tid in thread_ids:
        try:
            th = svc.users().threads().get(userId="me", id=tid, format="minimal").execute()
        except Exception as e:
            print(f"[organize] thread get error ({tid}): {e}", file=sys.stderr)
            continue

        messages = th.get("messages", [])
        present_p = set()
        for m in messages:
            for lid in m.get("labelIds", []):
                if lid in p_label_ids:
                    present_p.add(p_label_ids[lid])

        if len(present_p) <= 1:
            continue

        keep = next(lbl for lbl in priority if lbl in present_p)
        rem_labels = present_p - {keep}
        rem_ids = {ids[lbl] for lbl in rem_labels if lbl in ids}

        to_fix = [m["id"] for m in messages if any(lid in rem_ids for lid in m.get("labelIds", []))]
        if to_fix:
            batch_modify(svc, to_fix, [], list(rem_ids))
            print(f"[organize] スレッド {tid}: 最上位 {keep} を残し、下位ラベル {sorted(rem_labels)} を {len(to_fix)} 通から削除", file=sys.stderr)


def apply(svc, items: list[dict]) -> None:
    cfg = load("triage")
    ids = label_ids(svc)
    colors = {v["label"]: v.get("color") for axis in ("levels", "kinds") for v in cfg[axis].values() if v["label"]}
    groups: dict[tuple, list[str]] = {}
    for p in items:
        groups.setdefault((tuple(p["add"]), tuple(p["remove"]), p["archive"]), []).append(p["id"])
    for (add, remove, archive), mids in groups.items():
        add_ids = [ensure_label(svc, n, ids, colors.get(n)) for n in add]
        rem_ids = [ids[n] for n in remove if n in ids]
        batch_modify(svc, mids, add_ids, rem_ids + (["INBOX"] if archive else []))
        with ORGANIZE_LOG.open("a") as f:
            for mid in mids:
                f.write(json.dumps({"t": int(time.time()), "id": mid, "add": list(add), "remove": list(remove), "archived": archive}) + "\n")
    if ORGANIZE_LOG.exists():
        ORGANIZE_LOG.chmod(0o600)

    thread_ids = {p["threadId"] for p in items if p.get("threadId")}
    if thread_ids:
        ids = label_ids(svc)
        deduplicate_thread_priority(svc, thread_ids, cfg, ids)
