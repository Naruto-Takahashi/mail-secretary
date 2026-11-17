# home-manager モジュール: mail-secretary を定期実行する。
#   macOS: launchd.agents / Linux: systemd.user (service + timer)
self:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.mailSecretary;
  pkg = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
  path = lib.concatStringsSep ":" (cfg.extraPath ++ [ "/usr/bin" "/bin" ]);
  toml = pkgs.formats.toml { };
  configDir = "${config.xdg.configHome}/mail-secretary/config";
  env = {
    MAIL_SECRETARY_HOME = cfg.dataDir;
    MAIL_SECRETARY_CONFIG_DIR = configDir;
  };
  logFile = "${config.home.homeDirectory}/Library/Logs/mail-secretary.log";
in
{
  options.services.mailSecretary = {
    enable = lib.mkEnableOption "mail-secretary の定期実行 (メールの判定・整理・Discord 通知)";

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
      description = "実行間隔 (秒)。";
    };

    extraPath = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "/etc/profiles/per-user/${config.home.username}/bin" ];
      description = "agy など、Nix の外で入れた CLI を探すための PATH。launchd / systemd は最小の PATH で動くため必要。";
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
        launchd.agents.mail-secretary = {
          enable = true;
          config = {
            ProgramArguments = [
              "${pkg}/bin/mail-secretary"
              "run"
              "--apply"
            ];
            StartInterval = cfg.interval;
            RunAtLoad = true;
            ProcessType = "Background";
            EnvironmentVariables = env // { PATH = path; };
            StandardOutPath = logFile;
            StandardErrorPath = logFile;
          };
        };
      })

      (lib.mkIf pkgs.stdenv.hostPlatform.isLinux {
        systemd.user.services.mail-secretary = {
          Unit.Description = "mail-secretary (メールの判定・整理・通知)";
          Service = {
            Type = "oneshot";
            ExecStart = "${pkg}/bin/mail-secretary run --apply";
            Environment = [ "PATH=${path}" ] ++ lib.mapAttrsToList (k: v: "${k}=${v}") env;
          };
        };
        systemd.user.timers.mail-secretary = {
          Unit.Description = "mail-secretary の定期実行";
          Timer = {
            OnBootSec = "2min";
            OnUnitActiveSec = "${toString cfg.interval}s";
          };
          Install.WantedBy = [ "timers.target" ];
        };
      })
    ]
  );
}
