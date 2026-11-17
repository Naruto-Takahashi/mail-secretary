"""ラベルの同期: 旧ラベルの改名、種類・重要度ラベルの作成と色の設定 (`mail-secretary labels sync`)。"""

from __future__ import annotations

from .config import load
from .gmail import color_body, get_service


def wanted() -> dict[str, list[str] | None]:
    """設定にある (種類・重要度の) ラベル名 -> 色。"""
    cfg = load("triage")
    out: dict[str, list[str] | None] = {}
    for axis in ("levels", "kinds"):
        for v in cfg[axis].values():
            if v["label"]:
                out[v["label"]] = v.get("color")
    return out


def cmd_sync(args) -> None:
    svc = get_service()
    cfg = load("triage")
    api = svc.users().labels()
    cur = {l["name"]: l for l in api.list(userId="me").execute().get("labels", [])}
    todo = []  # (説明, 実行関数)

    for old, new in cfg["legacy"]["rename"].items():
        if old not in cur:
            continue
        if new in cur:
            print(f"要確認: 「{new}」が既にあるため「{old}」を改名できません")
            continue
        todo.append((f"改名: {old} -> {new}", lambda i=cur[old]["id"], n=new: api.patch(userId="me", id=i, body={"name": n}).execute()))
        cur[new] = {**cur.pop(old), "name": new}

    for name, color in wanted().items():
        body = {"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show", **color_body(color)}
        if name not in cur:
            todo.append((f"作成: {name} {color or ''}", lambda b=body: api.create(userId="me", body=b).execute()))
        elif color and cur[name].get("color", {}) != color_body(color)["color"]:
            todo.append((f"色: {name} -> {color}", lambda i=cur[name]["id"], b=color_body(color): api.patch(userId="me", id=i, body=b).execute()))

    if not todo:
        print("差分なし")
    for desc, fn in todo:
        print(("実行: " if args.yes else "予定: ") + desc)
        if args.yes:
            fn()
    if todo and not args.yes:
        print("(dry-run: --yes で実行)")
