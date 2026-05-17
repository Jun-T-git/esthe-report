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
        # Pillow + markdown 同梱の Python 環境
        # - pillow: アイキャッチ画像生成
        # - markdown: 記事 HTML 変換 (tables 拡張あり)
        pythonEnv = pkgs.python312.withPackages (ps: with ps; [
          pillow
          markdown
        ]);
      in
      {
        devShells.default = pkgs.mkShell {
          packages = [
            pythonEnv                  # Python 3.12 + Pillow
            pkgs.sqlite                # sqlite3 CLI
            pkgs.sqlite-web            # http://localhost:8080 で DB 閲覧
            pkgs.noto-fonts-cjk-sans   # 日本語ゴシック (本文・サブ)
            pkgs.noto-fonts-cjk-serif  # 日本語明朝 (見出し・高級感)
          ];

          shellHook = ''
            # アイキャッチ画像で使う日本語フォントパスを env に
            export NOTO_CJK_SANS="${pkgs.noto-fonts-cjk-sans}/share/fonts/opentype/noto-cjk/NotoSansCJK-VF.otf.ttc"
            export NOTO_CJK_SERIF="${pkgs.noto-fonts-cjk-serif}/share/fonts/opentype/noto-cjk/NotoSerifCJK-VF.otf.ttc"
            # 旧名互換
            export NOTO_CJK_FONT="$NOTO_CJK_SANS"

            echo ""
            echo "✨ esthe-report dev shell"
            echo "  python:      $(python3 --version)"
            echo "  pillow:      $(python3 -c 'import PIL; print(PIL.__version__)')"
            echo "  sqlite:      $(sqlite3 --version | cut -d' ' -f1)"
            echo "  sqlite-web:  sqlite_web aroma_more.db --read-only --no-browser"
            echo "  font(sans):  $NOTO_CJK_SANS"
            echo "  font(serif): $NOTO_CJK_SERIF"
            echo ""
          '';
        };
      });
}
