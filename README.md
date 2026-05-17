# esthe-report

アロマモア（メンズエステ）のセラピスト人気度を予約データから独自分析し、note有料記事として週次配信するための自動化システム。

## 概要

公式予約サイトの空き状況APIを毎日収集・蓄積し、完売率・出勤頻度・予約数・週次トレンドの4指標を合成した独自スコアでセラピストをランキングする。毎日記事下書きを自動生成する（同名ファイルは上書き）。

```
毎日8時（JST）
  collect.py      → 生スロットデータをDBに保存
  aggregate.py    → 日別＋週別集計（完売率等）を計算
  generate_article.py → note記事下書きを output/ に生成
```

## ディレクトリ構造

```
esthe-report/
├── src/
│   ├── db.py                 # DBスキーマ・接続
│   ├── collect.py            # 生データ収集
│   ├── aggregate.py          # 日別・週別集計
│   ├── score.py              # スコアリングロジック
│   └── generate_article.py  # 記事下書き生成
├── data/
│   └── all_reviews.json      # 口コミデータ（定期更新要）
├── output/
│   └── article_*.md         # 生成された記事下書き（毎日更新・対象週で上書き）
├── .github/workflows/
│   └── daily.yml             # GitHub Actions スケジュール実行
├── aroma_more.db             # SQLiteデータベース（自動コミット）
├── run_daily.sh              # ローカル手動実行用
└── requirements.txt          # 依存なし（Python標準ライブラリのみ）
```

## セットアップ

### 必要環境

- 推奨: [Nix](https://nixos.org/) (flakes 有効)
- 任意: [direnv](https://direnv.net/) + [nix-direnv](https://github.com/nix-community/nix-direnv)
- 本番(GitHub Actions)では Python 3.12 標準ライブラリのみで動作。Nix 環境では sqlite-web も同梱。

### Nix で開発環境構築（推奨）

#### 1. Nix をインストール（未導入なら）

[Determinate Systems の installer](https://determinate.systems/posts/determinate-nix-installer) が手軽です:

```bash
curl --proto '=https' --tlsv1.2 -sSf -L https://install.determinate.systems/nix | sh -s -- install
```

#### 2. devShell に入る

```bash
git clone git@github.com:Jun-T-git/esthe-report.git
cd esthe-report

# direnv ユーザー
direnv allow      # 以後ディレクトリに入るだけで自動有効化

# direnv を使わない場合
nix develop       # devShell に入る
```

入ると次が使えます:
- `python3` (3.12)
- `sqlite3` (CLI)
- `sqlite_web aroma_more.db --read-only --no-browser` → http://localhost:8080 で DB 閲覧

#### 3. ローカル実行例

```bash
# DB 初期化（既存DBがある場合は不要）
python3 src/db.py

# 手動で1回 収集 → 集計 → 記事生成
python3 src/collect.py
python3 src/aggregate.py --mode=daily
python3 src/aggregate.py --mode=weekly
python3 src/generate_article.py   # output/article_free_*.md と article_paid_*.md
```

### Nix を使わない場合

Python 3.10+ があれば標準ライブラリだけで動きます（記事生成・集計はOK）。
DBブラウザを使いたい場合のみ `pip install sqlite-web` が必要。

## 自動実行（GitHub Actions）

`.github/workflows/daily.yml` に定義済み。リポジトリをpushすれば有効になる。

| タイミング | 内容 |
|-----------|------|
| 毎日 23:00 UTC（= 翌8:00 JST） | 収集 + 日別集計 + 週別集計 + 記事生成 |

実行後、`aroma_more.db` と `output/article_draft.md` の変更が自動コミットされる。

### 手動実行

GitHub → Actions タブ → `日次データ収集・集計` → **Run workflow**

## データベース構造

`aroma_more.db`（SQLite）の3層構造。

| テーブル | 層 | 内容 |
|---------|---|------|
| `slot_records` | 生データ | 15分刻みの予約ステータス（0=空き・1=出勤なし・2=予約済） |
| `staff_snapshots` | 生データ | スタッフ基本情報の日別スナップショット |
| `daily_aggregates` | 集計 | スタッフ×日付の完売率・予約枠数 |
| `weekly_summaries` | 集計 | スタッフ×週の人気スコア・トレンド |

生データ（`slot_records`）を保持しているため、集計ロジックを変更した場合は `aggregate.py --mode=all` で全期間を再計算できる。

## スコアリング

`src/score.py` で定義。4指標の加重合成（0〜100）。

| 指標 | 重み | 説明 |
|------|------|------|
| 完売率 | 40% | 予約済み枠 / 出勤可能枠 |
| 出勤頻度 | 20% | 週の出勤日数（最大7日） |
| 予約絶対数 | 20% | 週の総予約枠数（正規化） |
| 週次トレンド | 20% | 前週比の完売率変化 |

重みを変えたい場合は `score.py` の `calc_score()` を修正後、`aggregate.py --mode=all` で再集計。

## 記事生成

毎日 `output/article_free_YYYYMMDD.md` と `output/article_paid_YYYYMMDD.md`（対象週の月曜日付）が自動生成される。同じ対象週の間は同名ファイルが上書きされる。

| セクション | 公開範囲 | 内容 |
|-----------|---------|------|
| 無料パート | 全員 | 11〜15位のみ公開・1位の完売率をティーザー |
| 有料パート | 購読者 | 全スコアランキング・4週トレンド・来週先行予約・穴場スタッフ・予測検証 |

タイトル形式：`【週間ランキング】データでわかるアロマモア セラピスト人気度分析 YYYY/MM/DD週`

## 定期メンテナンス

### 口コミデータの更新

`data/all_reviews.json` は静的ファイル。口コミが増えたら再取得してコミットする。

```bash
# 口コミ取得APIエンドポイント
# https://grow-appt.com/reserve/api/review/list?sid=u15Vr2S7zV
```

### 集計ロジックの変更

スコアの重みや完売率の計算方法を変えた場合：

```bash
python3 src/aggregate.py --mode=all   # 全期間を再計算
```

### ログ確認

ローカル実行時は `logs/YYYYMMDD.log` に出力。  
GitHub Actions はリポジトリの Actions タブで確認。
