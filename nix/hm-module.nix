# home-manager モジュール: secretary を常駐・定期実行する。
#   - メール仕分け (run --apply): 定期実行 (既定 10分間隔)
#   - Discord 対話 Bot (bot): 常駐起動 (KeepAlive)
#   - デイリーブリーフィング (briefing --apply): 毎朝指定時刻 (既定 7:30) に定期実行
#   macOS: launchd.agents / Linux: systemd.user (service + timer)
self:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.secretary;
  pkg = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
  path = lib.concatStringsSep ":" (cfg.extraPath ++ [ "/usr/bin" "/bin" ]);
  toml = pkgs.formats.toml { };
  configDir = "${config.xdg.configHome}/mail-secretary/config";
  env = {
    MAIL_SECRETARY_HOME = cfg.dataDir;
    MAIL_SECRETARY_CONFIG_DIR = configDir;
  };
  logDir = "${config.home.homeDirectory}/Library/Logs";
in
{
  imports = [
    (lib.mkAliasOptionModule [ "services" "mailSecretary" ] [ "services" "secretary" ])
  ];

  options.services.secretary = {
    enable = lib.mkEnableOption "secretary (メールの判定・整理・カレンダー登録・Discord Bot・朝ブリーフィング)";

    dataDir = lib.mkOption {
      type = lib.types.str;
      default = "${config.xdg.configHome}/mail-secretary";
      description = "秘密情報とデータの置き場 (MAIL_SECRETARY_HOME)。gmail/ と discord/ を含む。Git 管理外の場所を指定する。";
    };

    settings = lib.mkOption {
      type = lib.types.attrsOf toml.type;
      default = { };
      example = lib.literalExpression ''
        {
          triage.hints = [ "○○社からの請求メールは urgent にする。" ];
          triage.organize.protected_labels = [ "決済" ];
        }
      '';
      description = "個人用の設定。config/<名前>.toml (triage / notify / filters) への上書きとして配置される。";
    };

    interval = lib.mkOption {
      type = lib.types.int;
      default = 600;
      description = "メール仕分け (run --apply) の実行間隔 (秒)。";
    };

    extraPath = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "/etc/profiles/per-user/${config.home.username}/bin" ];
      description = "agy など、Nix の外で入れた CLI を探すための PATH。launchd / systemd は最小の PATH で動くため必要。";
    };

    bot = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = "Discord 双方向対話 Bot (secretary bot) を常駐起動するかどうか。";
      };
    };

    briefing = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = "朝のデイリーブリーフィング (secretary briefing --apply) を定期実行するかどうか。";
      };
      hour = lib.mkOption {
        type = lib.types.int;
        default = 7;
        description = "ブリーフィング実行時刻 (時)。";
      };
      minute = lib.mkOption {
        type = lib.types.int;
        default = 30;
        description = "ブリーフィング実行時刻 (分)。";
      };
    };
  };

  config = lib.mkIf cfg.enable (
    lib.mkMerge [
      {
        home.packages = [ pkg ];
        home.sessionVariables = env;
        xdg.configFile = lib.mapAttrs' (
          name: value:
          lib.nameValuePair "mail-secretary/config/${name}.toml" { source = toml.generate "${name}.toml" value; }
        ) cfg.settings;
      }

      (lib.mkIf pkgs.stdenv.hostPlatform.isDarwin {
        # 1. メール仕分け (run --apply)
        launchd.agents.secretary-run = {
          enable = true;
          config = {
            ProgramArguments = [
              "${pkg}/bin/secretary"
              "run"
              "--apply"
            ];
            StartInterval = cfg.interval;
            RunAtLoad = true;
            ProcessType = "Background";
            EnvironmentVariables = env // { PATH = path; };
            StandardOutPath = "${logDir}/secretary-run.log";
            StandardErrorPath = "${logDir}/secretary-run.log";
          };
        };

        # 2. Discord 対話 Bot (bot) 常駐
        launchd.agents.secretary-bot = lib.mkIf cfg.bot.enable {
          enable = true;
          config = {
            ProgramArguments = [
              "${pkg}/bin/secretary"
              "bot"
            ];
            KeepAlive = true;
            RunAtLoad = true;
            ProcessType = "Interactive";
            EnvironmentVariables = env // { PATH = path; };
            StandardOutPath = "${logDir}/secretary-bot.log";
            StandardErrorPath = "${logDir}/secretary-bot.log";
          };
        };

        # 3. 朝のデイリーブリーフィング (briefing --apply)
        launchd.agents.secretary-briefing = lib.mkIf cfg.briefing.enable {
          enable = true;
          config = {
            ProgramArguments = [
              "${pkg}/bin/secretary"
              "briefing"
              "--apply"
            ];
            StartCalendarInterval = [
              {
                Hour = cfg.briefing.hour;
                Minute = cfg.briefing.minute;
              }
            ];
            ProcessType = "Background";
            EnvironmentVariables = env // { PATH = path; };
            StandardOutPath = "${logDir}/secretary-briefing.log";
            StandardErrorPath = "${logDir}/secretary-briefing.log";
          };
        };
      })

      (lib.mkIf pkgs.stdenv.hostPlatform.isLinux {
        # 1. メール仕分け (run --apply)
        systemd.user.services.secretary-run = {
          Unit.Description = "secretary run (メールの判定・整理・通知)";
          Service = {
            Type = "oneshot";
            ExecStart = "${pkg}/bin/secretary run --apply";
            Environment = [ "PATH=${path}" ] ++ lib.mapAttrsToList (k: v: "${k}=${v}") env;
          };
        };
        systemd.user.timers.secretary-run = {
          Unit.Description = "secretary run の定期実行";
          Timer = {
            OnBootSec = "2min";
            OnUnitActiveSec = "${toString cfg.interval}s";
          };
          Install.WantedBy = [ "timers.target" ];
        };

        # 2. Discord 対話 Bot (bot) 常駐
        systemd.user.services.secretary-bot = lib.mkIf cfg.bot.enable {
          Unit.Description = "secretary bot (Discord 双方向対話 Bot)";
          Service = {
            Type = "simple";
            ExecStart = "${pkg}/bin/secretary bot";
            Restart = "always";
            RestartSec = "10s";
            Environment = [ "PATH=${path}" ] ++ lib.mapAttrsToList (k: v: "${k}=${v}") env;
          };
          Install.WantedBy = [ "default.target" ];
        };

        # 3. 朝のデイリーブリーフィング (briefing --apply)
        systemd.user.services.secretary-briefing = lib.mkIf cfg.briefing.enable {
          Unit.Description = "secretary briefing (朝のデイリーブリーフィング)";
          Service = {
            Type = "oneshot";
            ExecStart = "${pkg}/bin/secretary briefing --apply";
            Environment = [ "PATH=${path}" ] ++ lib.mapAttrsToList (k: v: "${k}=${v}") env;
          };
        };
        systemd.user.timers.secretary-briefing = lib.mkIf cfg.briefing.enable {
          Unit.Description = "secretary briefing の定期実行";
          Timer = {
            OnCalendar = "*-*-* ${lib.strings.fixedWidthNumber 2 cfg.briefing.hour}:${lib.strings.fixedWidthNumber 2 cfg.briefing.minute}:00";
            Persistent = true;
          };
          Install.WantedBy = [ "timers.target" ];
        };
      })
    ]
  );
}
