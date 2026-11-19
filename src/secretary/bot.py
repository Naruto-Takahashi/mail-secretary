"""Discord 双方向対話 Bot (`secretary bot`)。

Discord からのメッセージを受信し、AI (Gemini) がコンテキストを踏まえて返答・指示を実行する。
"""

from __future__ import annotations

import asyncio
import sys
from . import chat
from .config import DISCORD_ENV, load_env

import discord


class SecretaryBot(discord.Client):
    def __init__(self, allowed_user_id: str, channel_id: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.allowed_user_id = str(allowed_user_id)
        self.channel_id = int(channel_id) if channel_id else None
        self.conversations: dict[int, list[dict]] = {}  # channel_id -> history

    async def on_ready(self):
        print(f"[secretary-bot] ログイン成功: {self.user} (ID: {self.user.id})", file=sys.stderr)
        print(f"[secretary-bot] 待機中... (許可ユーザー: {self.allowed_user_id})", file=sys.stderr)

    async def on_message(self, message: discord.Message):
        # Bot 自身のメッセージは無視
        if message.author == self.user:
            return

        # 許可されたユーザーのみ
        if str(message.author.id) != self.allowed_user_id:
            return

        # 対象チャンネル、またはメンション、または DM のみ反応
        is_dm = isinstance(message.channel, discord.DMChannel)
        is_target_channel = self.channel_id and message.channel.id == self.channel_id
        is_mentioned = self.user in message.mentions

        if not (is_dm or is_target_channel or is_mentioned):
            return

        # メッセージ本文から Bot へのメンションを除去
        content = message.content
        if self.user:
            content = content.replace(f"<@{self.user.id}>", "").replace(f"<@!{self.user.id}>", "").strip()

        if not content:
            return

        print(f"[secretary-bot] 受信: '{content}' (from {message.author})", file=sys.stderr)

        async with message.channel.typing():
            # 履歴の取得
            history = self.conversations.get(message.channel.id, [])
            
            # ブロッキングな agy 呼び出しを非同期スレッドで実行
            loop = asyncio.get_running_loop()
            reply_text, new_history = await loop.run_in_executor(
                None, chat.reply, content, history
            )
            self.conversations[message.channel.id] = new_history

        # 2000文字制限の対策
        if len(reply_text) <= 2000:
            await message.reply(reply_text, mention_author=False)
        else:
            chunks = [reply_text[i : i + 1900] for i in range(0, len(reply_text), 1900)]
            for chunk in chunks:
                await message.channel.send(chunk)


def run_bot() -> None:
    env = load_env(DISCORD_ENV)
    token = env.get("DISCORD_BOT_TOKEN")
    user_id = env.get("DISCORD_USER_ID")
    channel_id = env.get("DISCORD_CHANNEL_ID")

    if not token:
        print("[secretary-bot] エラー: DISCORD_BOT_TOKEN が設定されていません。", file=sys.stderr)
        sys.exit(1)

    intents = discord.Intents.default()
    intents.message_content = True  # メッセージ本文取得用
    intents.dm_messages = True

    client = SecretaryBot(allowed_user_id=user_id, channel_id=channel_id, intents=intents)
    client.run(token)


def cmd_bot(_args) -> None:
    """CLI コマンドハンドラ"""
    run_bot()
