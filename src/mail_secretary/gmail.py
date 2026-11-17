"""Gmail API の薄いラッパー。"""

from __future__ import annotations

import base64
import re
import time
from html import unescape

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .config import CREDENTIALS, TOKEN, load

SCOPES = [
    "https://www.googleapis.com/auth/gmail.settings.basic",  # フィルタ
    "https://www.googleapis.com/auth/gmail.modify",  # ラベル・メッセージ
]


def get_service():
    creds = None
    if TOKEN.exists():
        creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            creds = InstalledAppFlow.from_client_secrets_file(CREDENTIALS, SCOPES).run_local_server(port=0)
        TOKEN.write_text(creds.to_json())
        TOKEN.chmod(0o600)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def label_names(svc) -> dict[str, str]:
    """labelId -> 名前"""
    labels = svc.users().labels().list(userId="me").execute().get("labels", [])
    return {label["id"]: label["name"] for label in labels}


def label_ids(svc) -> dict[str, str]:
    """名前 -> labelId"""
    return {n: i for i, n in label_names(svc).items()}


def color_body(color: list[str] | None) -> dict:
    """設定の [背景, 文字] を Gmail のラベル色に変換する。"""
    return {"color": {"backgroundColor": color[0], "textColor": color[1]}} if color else {}


def ensure_label(svc, name: str, ids: dict[str, str], color: list[str] | None = None) -> str:
    if name not in ids:
        body = {"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show", **color_body(color)}
        ids[name] = svc.users().labels().create(userId="me", body=body).execute()["id"]
    return ids[name]


def header(msg: dict, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def list_ids(svc, q: str, limit: int) -> list[str]:
    ids: list[str] = []
    token = None
    while len(ids) < limit:
        r = svc.users().messages().list(userId="me", q=q, maxResults=min(100, limit), pageToken=token).execute()
        ids += [m["id"] for m in r.get("messages", [])]
        token = r.get("nextPageToken")
        if not token:
            break
    return ids[:limit]


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def _walk(part: dict):
    yield part
    for p in part.get("parts") or []:
        yield from _walk(p)


def body_text(payload: dict, limit: int) -> str:
    """本文の先頭 limit 字 (プレーンテキスト)。URL と長い数字 (カード番号・電話番号等) は伏せる。"""
    if limit <= 0:
        return ""
    plain = html = ""
    for p in _walk(payload):
        data = p.get("body", {}).get("data")
        if not data:
            continue
        mt = p.get("mimeType", "")
        if mt == "text/plain" and not plain:
            plain = _decode(data)
        elif mt == "text/html" and not html:
            html = _decode(data)
    text = plain or re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", html))
    text = unescape(text)
    text = re.sub(r"https?://\S+", "[URL]", text)
    text = re.sub(r"\d(?:[ -]?\d){7,}", "[数字]", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def get_mail(svc, mid: str, names: dict[str, str]) -> dict:
    """1通分の情報 (件名・差出人・本文の先頭・ラベル)。"""
    m = svc.users().messages().get(userId="me", id=mid, format="full").execute()
    raw = m.get("labelIds", [])
    return {
        "id": mid,
        "threadId": m.get("threadId", mid),
        "ts": int(m["internalDate"]),
        "from": header(m, "From"),
        "subject": header(m, "Subject"),
        "date": header(m, "Date"),
        "snippet": (m.get("snippet") or "")[:200],
        "body": body_text(m.get("payload", {}), load("triage")["body_chars"]),
        "categories": [i for i in raw if i.startswith("CATEGORY_")],
        "labels": [names.get(i, i) for i in raw if not i.startswith(("CATEGORY_", "UNREAD", "IMPORTANT"))],
    }


def fetch_recent(svc, hours: int, limit: int) -> list[dict]:
    """直近 hours 時間のメール(新しい順)。"""
    names = label_names(svc)
    days = max(1, -(-hours // 24))
    q = f"newer_than:{days}d -in:trash -in:spam -in:sent -in:drafts"
    cutoff_ms = (time.time() - hours * 3600) * 1000
    mails = (get_mail(svc, mid, names) for mid in list_ids(svc, q, limit))
    return [m for m in mails if m["ts"] >= cutoff_ms]


def batch_modify(svc, ids: list[str], add: list[str], remove: list[str]) -> None:
    body: dict = {}
    if add:
        body["addLabelIds"] = add
    if remove:
        body["removeLabelIds"] = remove
    if not body:
        return
    for i in range(0, len(ids), 1000):
        svc.users().messages().batchModify(userId="me", body={**body, "ids": ids[i : i + 1000]}).execute()
