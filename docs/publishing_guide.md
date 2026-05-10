# 週次記事投稿 運用手順書

毎週土曜日に自動生成される記事を確認・編集・投稿するための手順書。

---

## 毎週の作業フロー

```
土曜 23:00 UTC（日曜 8:00 JST）
  ↓ GitHub Actions が自動実行
  ↓ collect.py → aggregate.py → generate_article.py
  ↓ output/article_free_YYYYMMDD.md
  ↓ output/article_paid_YYYYMMDD.md
  ↓ aroma_more.db を含めてリポジトリに自動コミット

日曜昼ごろ（好きなタイミングで）
  ↓ 管理者が記事を確認・編集
  ↓ 有料記事を先に投稿（URLを取得）
  ↓ 無料記事内のリンクを差し替え
  ↓ 無料記事を投稿
```

---

## Step 1：記事ファイルの取得

### GitHub から直接取得する場合
1. [https://github.com/Jun-T-git/esthe-report](https://github.com/Jun-T-git/esthe-report) を開く
2. `output/` フォルダを開く
3. `article_paid_YYYYMMDD.md` と `article_free_YYYYMMDD.md` を開き、Raw ボタンからコピー

### ローカルに clone している場合
```bash
cd /path/to/esthe-report
git pull
ls output/  # 最新ファイルを確認
```

ファイル名の `YYYYMMDD` は**その週の月曜日の日付**（例：5/11週なら `20260511`）。

---

## Step 2：記事の確認・チェックリスト

投稿前に以下を必ず確認する。

### 数値の妥当性チェック
- [ ] 完売率トップが **50%を超えていないか**（APIの異常取得の可能性）
- [ ] 分析スタッフ数が **50名以上**あるか（少ない場合は収集失敗の可能性）
- [ ] 蓄積データ数（`N週分`）が**前週より増えているか**

### 内容チェック
- [ ] タイトルの日付が正しいか（例：`2026/05/11週`）
- [ ] 11〜15位の名前が実在するセラピスト名か（文字化けなど）
- [ ] 「集計基準日」が**今週の金曜か土曜の日付**か（未来日付になっていたら要確認）

### 有料記事固有チェック
- [ ] 穴場スタッフ（💎）に実際のシフト情報が入っているか
- [ ] トレンドテーブルの週数が増えているか（データ蓄積につれ増える）

---

## Step 3：必ず手動で修正する箇所

### ① 無料記事の有料記事リンク（毎週必須）

無料記事の末尾に以下のプレースホルダーがある：

```
👉 **[有料記事を読む]（リンクをここに貼る）**
```

有料記事を先に投稿してURLを取得し、このプレースホルダーを実際のURLに差し替える：

```
👉 **[有料記事を読む](https://note.com/xxx/n/xxxxxxxx)**
```

### ② 「先週の予測検証」セクション（3週目以降）

有料記事に以下のセクションがある：

```
（※ 次週以降、実績データと照合して自動生成されます）
```

3週目以降は自動生成されるが、**コメントや所感を加えると記事の価値が上がる**。
例：「予告通り○○さんが急上昇し先週比+8pt。来週も要注目。」

### ③ タイトルや導入文の微調整（任意）

自動生成テキストはあくまで下書き。読みやすさや語調を調整して問題ない。

---

## Step 4：投稿

### 有料記事を先に投稿
1. `article_paid_YYYYMMDD.md` の内容をコピー
2. note（または採用プラットフォーム）で新規記事作成
3. 貼り付け → 有料設定 → 公開
4. 公開後のURLをコピーしておく

### 無料記事を投稿
1. `article_free_YYYYMMDD.md` を開く
2. `（リンクをここに貼る）` を有料記事のURLに差し替え
3. note で新規記事作成 → 貼り付け → 無料設定 → 公開

---

## Step 5：投稿後の記録（推奨）

| 週 | 投稿日 | 有料記事URL | 無料記事URL | 備考 |
|----|--------|-------------|-------------|------|
| 2026/05/11週 | | | | 初回投稿 |

`docs/publish_log.md` などに記録しておくと予測検証セクションで役立つ。

---

## 異常時の対応

### Actions が実行されなかった（記事ファイルが更新されていない）
1. GitHub → Actions タブ → `日次データ収集・集計` を確認
2. エラーがあればログを確認
3. 手動実行：`Run workflow` ボタンをクリック
4. それでも失敗する場合はローカルで実行：
   ```bash
   git pull
   python3 src/collect.py
   python3 src/aggregate.py --mode=weekly
   python3 src/generate_article.py
   ```

### 数値が明らかにおかしい（完売率100%が多数など）
- APIが一時的に異常なデータを返した可能性がある
- その週のDBデータを確認：
  ```bash
  python3 -c "
  from src.db import get_conn
  conn = get_conn()
  rows = conn.execute('SELECT * FROM snapshots ORDER BY id DESC LIMIT 3').fetchall()
  for r in rows: print(dict(r))
  "
  ```
- 異常なスナップショットは投稿を見送るか、数値を手動で補正する

### GitHub Actions の自動実行が止まった
- リポジトリに60日以上コミットがないと GitHub がスケジュール実行を停止する
- 再有効化：Actions タブ → `日次データ収集・集計` → `Enable workflow`

---

## 参考：自動生成されるファイル一覧

| ファイル | 生成タイミング | 内容 |
|---------|--------------|------|
| `output/article_free_YYYYMMDD.md` | 毎週土曜 | 無料記事下書き（リンクのプレースホルダーあり） |
| `output/article_paid_YYYYMMDD.md` | 毎週土曜 | 有料記事下書き（完全版） |
| `aroma_more.db` | 毎日 | 蓄積データ（SQLite） |

`YYYYMMDD` = その週の月曜日の日付。
