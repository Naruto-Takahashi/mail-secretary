"""秘書 AI との対話エンジン。

Discord チャット等からの自然言語メッセージを受け取り、
カレンダー・メール・Obsidian などの最新コンテキストを踏まえて返答を生成し、
必要に応じてアクション (予定追加、メモ記録など) を実行する。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from . import briefing, calendar, gmail
from .config import load


SYSTEM_PROMPT = """あなたは Naruto-Takahashi の有能で親切な個人秘書 AI (secretary) です。
Discord チャットを通じて、ユーザーのスケジュール管理、メール把握、タスク整理、アイデアの相談や雑談をサポートします。

【ユーザーのコンテキスト】
- 所属: 九州大学 工学部 電気情報工学科 4年 (B4) 櫻井研究室 (来春より修士進学)
- 研究: 人間計算可能パスワード (HCP) の Transformer / LLM 評価
- プロジェクト:
  - 福岡未踏 Solve (APOR.IA: 自治体設計書レビュー支援)
  - 株式会社オリズン (APOR.IA 関連ミーティング)
  - LR開発バイト (毎週水曜ミーティング・出社)
  - 家庭教師アルバイト (岡本さん、又吉さん)
- 外部脳: Obsidian Vault を活用しており、日誌やプロジェクト状況を記録している。

【現在の状況】
現在時刻: {current_time}
本日の予定:
{today_events}
Obsidian 今日の未完了タスク:
{today_tasks}
要対応の未処理メール:
{pending_mails}

【出力フォーマット】
必ず以下の JSON 形式のみで出力してください:
{{
  "reply": "ユーザーへの返信メッセージ (マークダウン形式)",
  "action": null または {{
    "type": "add_event | add_task | add_memo | search_mail",
    "params": {{...}}
  }}
}}

アクションのパラメータ詳細:
- add_event (カレンダーに予定登録):
  params: {{"title": "予定名", "start": "YYYY-MM-DDTHH:MM:SSまたはYYYY-MM-DD", "end": "YYYY-MM-DDTHH:MM:SS", "is_all_day": false, "category": "deadline|sakurai_lab|sakurai_rinko|fukuoka_mitou|orizon|lr_dev|job|other", "location": "Zoom等", "description": "メモ"}}
  ※ ユーザーから「カレンダーに入れて」「予定を追加して」と依頼された場合に実行します。
- add_task (Obsidian 今日の日誌にタスク追加):
  params: {{"task": "タスク内容"}}
  ※ 「タスクに追加して」「ToDoに入れて」と依頼された場合に実行します。
- add_memo (Obsidian 今日の日誌にメモ追記):
  params: {{"memo": "メモ内容"}}
  ※ 「メモして」「記録しておいて」と依頼された場合に実行します。
- search_mail (Gmailを検索):
  params: {{"query": "検索クエリ"}}
  ※ メールの詳細確認を依頼された場合に実行します。
- アクションが不要な通常会話・質問・相談は action を null にしてください。
"""


def get_current_context() -> dict[str, str]:
    now = datetime.now().astimezone()
    cur_time = f"{now:%Y-%m-%d (%a) %H:%M}"

    # 本日の予定
    events_str = "・なし"
    try:
        events = calendar.fetch_today_events()
        if events:
            events_str = "\n".join(f"・`{ev['time']}` {ev['summary']}" for ev in events)
    except Exception as e:
        events_str = f"・(取得エラー: {e})"

    # 本日のタスク
    tasks_str = "・なし"
    try:
        tasks = briefing.get_obsidian_tasks()
        if tasks:
            tasks_str = "\n".join(f"・{t}" for t in tasks)
    except Exception as e:
        tasks_str = f"・(取得エラー: {e})"

    # 要対応メール
    mails_str = "・なし"
    try:
        mails = briefing.get_pending_action_mails()
        if mails:
            mails_str = "\n".join(f"・[{m.get('kind', 'other')}] {m.get('from', '')}: {m.get('subject', '')[:30]}" for m in mails[:5])
    except Exception as e:
        mails_str = f"・(取得エラー: {e})"

    return {
        "current_time": cur_time,
        "today_events": events_str,
        "today_tasks": tasks_str,
        "pending_mails": mails_str,
    }


def execute_action(action: dict) -> str:
    """AI が要求したアクションを実行し、結果メッセージを返す。"""
    act_type = action.get("type")
    params = action.get("params", {})

    if act_type == "add_event":
        cal_svc = calendar.get_calendar_service()
        ev_data = {
            "title": params.get("title", "(タイトルなし)"),
            "start": params.get("start", ""),
            "end": params.get("end"),
            "is_all_day": params.get("is_all_day", False),
            "location": params.get("location", ""),
            "description": params.get("description", "Discord 経由での秘書登録"),
            "category": params.get("category", "other"),
        }
        mail_dummy = {"subject": "Discord Chat 指示", "from": "Discord", "threadId": ""}
        created = calendar.insert_calendar_event(ev_data, mail_dummy, cal=cal_svc)
        link = created.get("htmlLink", "")
        return f"\n\n📅 **Google カレンダーに登録しました**: [{ev_data['title']}](<{link}>)"

    elif act_type == "add_task":
        task_text = params.get("task", "").strip()
        if not task_text:
            return ""
        vault = Path("~/ghq/github.com/Naruto-Takahashi/obsidian-vault").expanduser()
        now = datetime.now()
        note_path = vault / "40-daily" / f"{now:%Y}" / f"{now:%m}" / f"{now:%Y-%m-%d}.md"
        if note_path.exists():
            content = note_path.read_text()
            if "## Tasks" in content:
                content = content.replace("## Tasks\n", f"## Tasks\n- [ ] {task_text}\n", 1)
            else:
                content += f"\n\n## Tasks\n- [ ] {task_text}\n"
            note_path.write_text(content)
            return f"\n\n📝 **外部脳にToDoを追加しました**: `{task_text}`"

    elif act_type == "add_memo":
        memo_text = params.get("memo", "").strip()
        if not memo_text:
            return ""
        try:
            subprocess.run(["ob", "memo", memo_text], capture_output=True, text=True, check=True)
            return f"\n\n📓 **外部脳にメモを追記しました**: `{memo_text}`"
        except Exception:
            pass

    elif act_type == "search_mail":
        query = params.get("query", "")
        svc = gmail.get_service()
        mids = gmail.list_ids(svc, query, limit=3)
        if mids:
            names = gmail.label_names(svc)
            mails = [gmail.get_mail(svc, mid, names) for mid in mids]
            res_lines = ["\n\n🔍 **メール検索結果**:"]
            for m in mails:
                res_lines.append(f"・**{m['from'][:25]}**: {m['subject'][:40]} ({m['date'][:16]})")
            return "\n".join(res_lines)
        else:
            return f"\n\n🔍 メールの検索結果は見つかりませんでした (`{query}`)"

    return ""


def reply(user_text: str, history: list[dict] | None = None) -> tuple[str, list[dict]]:
    """ユーザーの入力に対して返答と更新された履歴を返す。"""
    history = history or []
    ctx = get_current_context()
    sys_prompt = SYSTEM_PROMPT.format(**ctx)

    hist_lines = []
    for h in history[-6:]:  # 直近3往復
        role = "ユーザー" if h["role"] == "user" else "秘書"
        hist_lines.append(f"{role}: {h['content']}")
    hist_text = "\n".join(hist_lines)
    full_prompt = (
        f"{sys_prompt}\n\n"
        f"【直近の会話履歴】\n{hist_text}\n\n"
        f"ユーザーの発言: {user_text}\n"
        "秘書の返答 (上記フォーマットのJSONのみを出力してください):"
    )

    # AI バックエンドの呼び出し (環境変数 CHAT_AI_CMD、または claude / agy)
    ai_cmd_env = os.environ.get("CHAT_AI_CMD")
    if ai_cmd_env:
        cmd = ai_cmd_env.split() + [full_prompt]
    elif Path("/etc/profiles/per-user/nalt/bin/claude").exists():
        # Claude CLI が利用可能な場合は高速で安定した応答のために使用
        cmd = ["claude", "-p", full_prompt]
    else:
        cmd = os.environ.get("AGY_CMD", "agy").split() + [
            "-p", full_prompt,
            "--model", "gemini-3.8-flash-medium",
            "--sandbox",
            "--output-format", "json",
            "--print-timeout", "45s",
        ]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return "申し訳ありません、応答の生成がタイムアウトしました。もう一度お試しください。", history

    if r.returncode != 0:
        err_msg = r.stderr[:200] if r.stderr else r.stdout[:200]
        return f"応答の生成中にエラーが発生しました: {err_msg}", history

    raw_output = r.stdout
    try:
        # JSON 形式の抽出 (agy の output-format=json または claude の出力)
        if "response" in raw_output and "status" in raw_output:
            try:
                parsed = json.loads(raw_output)
                raw_output = parsed.get("response", raw_output)
            except Exception:
                pass

        m = re.search(r"\{.*\}", raw_output, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
            reply_msg = data.get("reply", "")
            action = data.get("action")
            if action and isinstance(action, dict):
                act_result = execute_action(action)
                reply_msg += act_result
        else:
            reply_msg = raw_output.strip()
    except Exception as e:
        reply_msg = f"{raw_output.strip()}"


    new_hist = history + [{"role": "user", "content": user_text}, {"role": "assistant", "content": reply_msg}]
    return reply_msg, new_hist[-10:]
