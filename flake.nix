{
  description = "mail-secretary: メールの自動整理・AI 重要度判定・Discord 通知";

  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "aarch64-darwin"
        "x86_64-darwin"
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAll (pkgs: rec {
        mail-secretary = pkgs.writeShellApplication {
          name = "mail-secretary";
          runtimeInputs = [
            pkgs.uv
            pkgs.python3
          ];
          # ソースは読み取り専用の Nix ストアに置かれるため、仮想環境はキャッシュ側に作る。
          text = ''
            export PYTHONPATH="${self}/src''${PYTHONPATH:+:$PYTHONPATH}"
            export UV_PROJECT_ENVIRONMENT="''${XDG_CACHE_HOME:-$HOME/.cache}/mail-secretary/venv"
            export UV_PYTHON="${pkgs.python3}/bin/python3"
            export UV_PYTHON_DOWNLOADS=never
            exec uv run --quiet --frozen --project ${self} python -m mail_secretary "$@"
          '';
        };
        default = mail-secretary;
      });

      # home-manager モジュール (launchd / systemd の定期実行を宣言する)
      homeManagerModules.default = import ./nix/hm-module.nix self;
    };
}
