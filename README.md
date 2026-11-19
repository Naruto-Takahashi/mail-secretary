# secretary

個人の常駐型秘書: メールの自動整理・予定把握・タスク整理・Discord 通知。

## 概要
- **メール仕分け (`secretary run --apply`)**: Gmail の新着を AI (`agy` / Gemini) で重要度判定し、ラベル付け・受信トレイ整理・カレンダー予定自動登録・Discord 通知。
- **朝のデイリーブリーフィング (`secretary briefing --apply`)**: Google カレンダーの本日の予定 ＋ Obsidian 外部脳の未完了タスク ＋ 要対応メールを1通にまとめ、毎朝 Discord に投稿。

## コマンド
```
secretary auth                 初回認可 (Gmail・カレンダー読み書き)
secretary briefing [--apply]   朝のデイリーブリーフィング (予定・ToDo・要対応メール)
secretary run [--apply]        メールの定期実行 (判定・整理・カレンダー登録・通知)
secretary triage               直近のメールを判定して表示するだけ (カレンダー候補も表示)
secretary bot                  Discord 双方向対話 Bot の起動 (チャットでの予定確認・登録・相談)
secretary backfill [--apply]   既存メールへの一回限りのラベル付け
secretary filters plan|apply   Gmail フィルタの同期 (config/filters.toml)
secretary labels sync          ラベルの同期 (旧ラベルの改名・作成・色)
secretary restore-inbox        重要な GitHub 通知を受信トレイに戻す
```

## 既定の設定 (`config/`)
- `filters.toml` — Gmail フィルタ
- `triage.toml` — AI の判定基準・ラベル名・整理ルール
- `notify.toml` — 通知 (メンション・まとめ送りの時刻・取得範囲)

## 設定と秘密情報 (リポジトリの外)
同梱の `config/*.toml` は汎用の既定値。個人用の設定と秘密情報は、次の場所に置く (どちらも Git に入れない)。

```
$MAIL_SECRETARY_HOME  (既定: ~/.config/mail-secretary)
├── gmail/credentials.json, token.json     Gmail API の OAuth (`mail-secretary auth` で作る)
├── gmail/state.json, organize-log.jsonl   処理済みメール・未送信の要対応・整理の記録
├── discord/bot.env                        DISCORD_BOT_TOKEN, DISCORD_GUILD_ID, DISCORD_CHANNEL_ID, DISCORD_USER_ID
└── config/{triage,notify,filters}.toml    既定値への上書き (再帰的に重ねる。配列は置き換え)
```

`MAIL_SECRETARY_CONFIG_DIR` で上書き設定の場所だけ変えられる。上書きの例 (`config/triage.toml`):

```toml
hints = ["○○社からの請求メールは urgent にする。"]
[organize]
protected_labels = ["決済"]   # このラベルが付いたメールは AI 判定でも受信トレイから外さない
```

## Nix
```nix
inputs.mail-secretary.url = "github:Naruto-Takahashi/mail-secretary";
# home-manager モジュール:
#   imports = [ inputs.mail-secretary.homeManagerModules.default ];
#   services.mailSecretary = {
#     enable = true;                       # macOS は launchd、Linux は systemd timer
#     dataDir = "/path/to/secrets";        # MAIL_SECRETARY_HOME
#     settings.triage.hints = [ "…" ];     # 個人用の上書き (TOML を生成して配置)
#   };
```

## 前提
`agy` (Antigravity CLI) が認証済みで PATH にあること (`AGY_CMD` で差し替え可)。
