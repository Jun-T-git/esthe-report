{
  description = "esthe-report dev environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
      in
      {
        devShells.default = pkgs.mkShell {
          packages = [
            pkgs.python312     # CI と同じ Python 3.12
            pkgs.sqlite        # sqlite3 CLI
            pkgs.sqlite-web    # http://localhost:8080 で DB 閲覧
          ];

          shellHook = ''
            echo ""
            echo "✨ esthe-report dev shell"
            echo "  python:      $(python3 --version)"
            echo "  sqlite:      $(sqlite3 --version | cut -d' ' -f1)"
            echo "  sqlite-web:  sqlite_web aroma_more.db --read-only --no-browser"
            echo ""
          '';
        };
      });
}
