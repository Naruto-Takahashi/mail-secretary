"""設定の読み込み。

設定は「リポジトリ同梱の汎用の既定値 (config/*.toml)」に、「個人用の上書き」を重ねて作る。
個人用の上書きと秘密情報はリポジトリの外に置く。

  MAIL_SECRETARY_HOME        秘密情報とデータの置き場 (既定: ~/.config/mail-secretary)
    gmail/credentials.json, gmail/token.json   Gmail API の OAuth
    gmail/state.json, gmail/organize-log.jsonl 処理済みメール・未送信の要対応・整理の記録
    discord/bot.env                            DISCORD_BOT_TOKEN / _GUILD_ID / _CHANNEL_ID / _USER_ID
  MAIL_SECRETARY_CONFIG_DIR  個人用の設定の上書き (既定: $MAIL_SECRETARY_HOME/config)
    triage.toml / notify.toml / filters.toml    既定値の上に再帰的に重ねる (配列は置き換え)
"""

from __future__ import annotations

import functools
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"  # 同梱の既定値

def _resolve_home() -> Path:
    if "SECRETARY_HOME" in os.environ:
        return Path(os.environ["SECRETARY_HOME"]).expanduser()
    if "MAIL_SECRETARY_HOME" in os.environ:
        return Path(os.environ["MAIL_SECRETARY_HOME"]).expanduser()
    p_sec = Path("~/.config/secretary").expanduser()
    p_mail = Path("~/.config/mail-secretary").expanduser()
    return p_sec if p_sec.exists() or not p_mail.exists() else p_mail


def _resolve_config_dir(home: Path) -> Path:
    if "SECRETARY_CONFIG_DIR" in os.environ:
        return Path(os.environ["SECRETARY_CONFIG_DIR"]).expanduser()
    if "MAIL_SECRETARY_CONFIG_DIR" in os.environ:
        return Path(os.environ["MAIL_SECRETARY_CONFIG_DIR"]).expanduser()
    return home / "config"


HOME = _resolve_home()
OVERRIDE_DIR = _resolve_config_dir(HOME)
SECRET_DIR = HOME / "gmail"
DISCORD_ENV = Path(os.environ.get("DISCORD_ENV", HOME / "discord" / "bot.env")).expanduser()

CREDENTIALS = SECRET_DIR / "credentials.json"
TOKEN = SECRET_DIR / "token.json"
STATE = SECRET_DIR / "state.json"
ORGANIZE_LOG = SECRET_DIR / "organize-log.jsonl"


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


@functools.cache
def load(name: str) -> dict:
    cfg = tomllib.loads((CONFIG_DIR / f"{name}.toml").read_text())
    override = OVERRIDE_DIR / f"{name}.toml"
    if override.exists():
        cfg = _merge(cfg, tomllib.loads(override.read_text()))
    return cfg


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env
