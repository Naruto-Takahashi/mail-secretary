# mail-secretary

Gmail の新着を AI (Antigravity CLI `agy` / Gemini) で重要度判定し、ラベル付け・受信トレイの整理・Discord 通知を行う個人用の秘書。

## しくみ (`mail-secretary run --apply` を 10 分おきに実行)
1. 新着メール (件名・差出人・スニペット) を取得し、未処理のものだけを AI に渡して重要度 4 段階 (緊急 / 要対応 / 参考 / 無視) を判定する。認証コード等は AI に送らない。
2. 重要度ラベル (`重要度/1_緊急` 等) を付け、参考・無視は受信トレイから外す (保護ラベルが付いたものとスター付きは残す)。
3. 緊急はすぐ Discord にメンション付きで通知。要対応はためておき、朝・夕にまとめて通知する。

`--apply` を付けない `run` は dry-run (メールの変更・投稿・状態の保存をしない)。

## コマンド
```
mail-secretary auth                 初回認可 (ブラウザが開く)
mail-secretary filters plan|apply   Gmail フィルタの同期 (config/filters.toml)
mail-secretary triage               直近のメールを判定して表示するだけ
mail-secretary run [--apply]        判定・整理・通知
mail-secretary restore-inbox        重要な GitHub 通知を受信トレイに戻す
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
