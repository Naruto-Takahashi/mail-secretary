"""判定結果の Gmail への反映 (種類ラベル・重要度ラベルの付与と、受信トレイからの整理)。"""

from __future__ import annotations

import json
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
                "id": m["id"], "level": lv, "kind": kind, "add": add, "remove": remove,
                "archive": "INBOX" in m["labels"] and not keep,
                "kept_by_star": starred and lv not in ("urgent", "action"),
            }
        )
    return out


def apply(svc, items: list[dict]) -> None:
    ids = label_ids(svc)
    colors = {v["label"]: v.get("color") for axis in ("levels", "kinds") for v in load("triage")[axis].values() if v["label"]}
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
