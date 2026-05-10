"""
note記事の下書きを自動生成（毎週土曜投稿向け）

データ参照ルール:
  今週の確定データ  = 前日（金曜）のスナップショット
  過去4週トレンド   = 各週金曜のスナップショットを遡って比較
  来週の先行予約    = 本日（土曜）スナップショットの7日先
  前日予約まで確定  = 当日の同日予約は含まない前提
"""

import json
from datetime import datetime, timedelta, date
from pathlib import Path
from db import get_conn
from score import score_snapshot

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"


# ====================================================================
# レビューデータ
# ====================================================================
def load_reviews(path=None):
    path = path or DATA_DIR / "all_reviews.json"
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        reviews = raw.get("reviews", raw) if isinstance(raw, dict) else raw
        by_staff = {}
        for r in reviews:
            name = r.get("staffname", "").strip()
            if name:
                by_staff.setdefault(name, []).append(r)
        return by_staff
    except FileNotFoundError:
        return {}


def pick_review(reviews):
    if not reviews:
        return None
    best = max(reviews, key=lambda r: len(r.get("message", "")))
    msg = best.get("message", "")
    if len(msg) > 60:
        msg = msg[:57] + "…"
    age = best.get("age", "")
    return f"「{msg}」（{age}代）" if age else f"「{msg}」"


# ====================================================================
# スナップショット検索
# ====================================================================
def find_snapshot_before(target_date: date):
    """指定日以前で最新のスナップショットを返す"""
    conn = get_conn()
    row = conn.execute("""
        SELECT id, collected_at, period_start, period_end
        FROM snapshots
        WHERE date(collected_at) <= ?
        ORDER BY collected_at DESC LIMIT 1
    """, (target_date.isoformat(),)).fetchone()
    conn.close()
    return row


def find_latest_snapshot():
    conn = get_conn()
    row = conn.execute("""
        SELECT id, collected_at, period_start, period_end
        FROM snapshots ORDER BY collected_at DESC LIMIT 1
    """).fetchone()
    conn.close()
    return row


def count_snapshots():
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    conn.close()
    return n


# ====================================================================
# 来週の先行予約データ
# ====================================================================
def get_next_week_preview(snap_id: int, next_mon: date, next_sun: date) -> list:
    """来週月〜日の各スタッフの先行予約枠を返す（すでに埋まっている分）"""
    conn = get_conn()
    rows = conn.execute("""
        SELECT ss.staff_name, ss.staff_no,
               SUM(ds.booked) as booked,
               SUM(ds.capacity) as capacity
        FROM daily_stats ds
        JOIN staff_stats ss ON ds.staff_stat_id = ss.id
        WHERE ss.snapshot_id = ?
          AND ds.date BETWEEN ? AND ?
        GROUP BY ss.staff_no, ss.staff_name
        HAVING booked > 0
        ORDER BY booked DESC
    """, (snap_id, next_mon.isoformat(), next_sun.isoformat())).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ====================================================================
# 4週間トレンド
# ====================================================================
def build_trend_table(today: date) -> list:
    """
    過去4週分の金曜スナップショットを比較して
    各スタッフの週次スコア推移を返す
    """
    weeks = []
    for w in range(4):
        # w=0: 先週金曜, w=1: 2週前金曜, ...
        days_to_friday = (today.weekday() + 1) % 7 + 1  # 土曜から見た前金曜の差
        friday = today - timedelta(days=days_to_friday + 7 * w)
        snap = find_snapshot_before(friday)
        if snap:
            scored = score_snapshot(snap["id"])
            rank_map = {s["staff_name"]: (i + 1, s["score"], s["sellout_rate"])
                        for i, s in enumerate(scored)}
            weeks.append({"friday": friday.isoformat(), "snap_id": snap["id"],
                          "ranks": rank_map})
        else:
            weeks.append({"friday": friday.isoformat(), "snap_id": None, "ranks": {}})
    return weeks  # weeks[0] = 最新週、weeks[3] = 4週前


def trend_arrow(scores: list) -> str:
    """スコアリストから傾向を矢印で返す（新しい順）"""
    valid = [s for s in scores if s is not None]
    if len(valid) < 2:
        return "―"
    diff = valid[0] - valid[-1]
    if diff >= 10:
        return "🔥大幅上昇"
    elif diff >= 4:
        return "↑上昇"
    elif diff <= -10:
        return "↓大幅下降"
    elif diff <= -4:
        return "↓下降"
    else:
        return "→横ばい"


# ====================================================================
# タイトル生成
# ====================================================================
def _month_week_label(d: date) -> str:
    """「5月第2週」形式の文字列を返す"""
    week_of_month = (d.day - 1) // 7 + 1
    return f"{d.month}月第{week_of_month}週"


def _make_title(active: list, snap_count: int, month_week: str, monday: date = None) -> str:
    """固定シリーズ名＋その週の月曜日付"""
    date_str = monday.strftime("%Y/%m/%d") if monday else month_week
    return f"【週間ランキング】データでわかるアロマモア セラピスト人気度分析 {date_str}週"


# ====================================================================
# メイン生成
# ====================================================================
def generate(target_saturday: date = None) -> str:
    today = target_saturday or date.today()
    assert today.weekday() == 5 or target_saturday is not None, \
        "土曜日以外での実行は target_saturday を明示してください"

    # 日付計算
    yesterday = today - timedelta(days=1)          # 金曜（今週確定データ基準日）
    week_start = today - timedelta(days=6)          # 日曜（今週の始まり）
    next_mon = today + timedelta(days=2)            # 来週月曜
    next_sun = today + timedelta(days=8)            # 来週日曜

    # スナップショット取得（前日が理想、なければ当日まで許容）
    this_week_snap = find_snapshot_before(yesterday) or find_snapshot_before(today)
    next_week_snap = find_latest_snapshot()
    snap_count = count_snapshots()

    if not this_week_snap:
        return "データが不足しています。collect.py を実行してください。"

    # スコアリング
    scored = score_snapshot(this_week_snap["id"])
    active = [s for s in scored if s["sellout_rate"] > 0]
    if not active:
        return "有効なスタッフデータがありません。"

    reviews_by_staff = load_reviews()

    # 4週間トレンド
    trend_weeks = build_trend_table(today)

    # 来週先行予約
    next_week_preview = []
    if next_week_snap:
        next_week_preview = get_next_week_preview(
            next_week_snap["id"], next_mon, next_sun)

    # 穴場スタッフ（中位×出勤少なめ）
    third = len(active) // 3
    bargains = sorted(active[third: third * 2],
                      key=lambda x: x["working_days"])[:3]

    # ====================================================================
    # 記事本文
    # ====================================================================
    lines = []
    week_label = f"第{snap_count}週" if snap_count > 1 else "初回"
    pub_date = today.strftime("%Y年%-m月%-d日")
    month_week = _month_week_label(today)
    # 土曜投稿 → 翌週月曜（+2日）が分析対象週の開始日
    monday = today + timedelta(days=2)

    title = _make_title(active, snap_count, month_week, monday=monday)

    # ── ヘッダー ──────────────────────────────────────────
    lines += [
        f"# {title}",
        "",
        f"> **集計基準日**：{yesterday.isoformat()}（前日までの予約を反映）  ",
        f"> **分析期間**：{this_week_snap['period_start']} 〜 {this_week_snap['period_end']}  ",
        f"> **分析スタッフ数**：{len(active)}名　蓄積データ：{snap_count}週分",
        "",
        "---",
        "",
    ]

    # ── 無料パート ──────────────────────────────────────
    lines += [
        "## 🔓 無料パート",
        "",
        f"今週は**{len(active)}名**を独自スコアでランキングしました。",
        "スコアは「完売率・出勤頻度・予約数・週次トレンド」の4指標を加重合成したものです。",
        "",
        f"今週の1位は完売率 **{active[0]['sellout_rate']:.1f}%**、",
        f"スコア **{active[0]['score']:.1f}**でした。",
        f"（{len(active)}名中{sum(1 for s in active if s['sellout_rate'] >= 50)}名が完売率50%超え）",
        "",
        "### 参考公開：11〜15位",
        "",
        "トップ10・16位以降は有料パートに掲載しています。",
        "「自分が気になっているあの人が何位か」を確かめてください。",
        "",
        "| 順位 | セラピスト | 完売率 | 今週の出勤 |",
        "|------|-----------|--------|-----------|",
    ]
    for rank, s in enumerate(active[10:15], 11):
        lines.append(
            f"| {rank}位 | {s['staff_name']} | {s['sellout_rate']:.1f}% | {s['working_days']}日 |"
        )

    lines += [
        "",
        "> トップ10は予約が取りにくいスタッフ、16位以降には「穴場」が潜んでいます。",
        "> 詳細は有料パートで。",
        "",
        "---",
        "",
    ]

    # ── 有料パート ──────────────────────────────────────
    lines += [
        "## 🔒 有料パート",
        "",
        "> **収録内容**",
        "> 1. 今週の全スタッフスコアランキング（数値・シフト付き）",
        "> 2. 過去4週間トレンド（誰が上昇中か・下降中か）",
        "> 3. 来週の先行予約状況（早めに動くべきスタッフ）",
        "> 4. 穴場スタッフ（今すぐ予約が取れる狙い目）",
        "> 5. 予測精度の検証（先週の予測は当たったか）",
        "",
        "---",
        "",
    ]

    # 1. 全スタッフランキング
    lines += [
        "### 📊 今週の全スタッフ スコアランキング",
        "",
        f"集計基準：{yesterday.isoformat()}時点（前日までの予約を反映）",
        "",
        "| 順位 | セラピスト | スコア | 完売率 | 出勤日数 | 予約枠数 | 週次変化 | 今日のシフト |",
        "|------|-----------|--------|--------|---------|---------|---------|------------|",
    ]
    for rank, s in enumerate(active, 1):
        trend_str = f"+{s['trend']:.1f}pt" if s["trend"] >= 0 else f"{s['trend']:.1f}pt"
        shift = (f"{s['today_shift_start']}〜{s['today_shift_end']}"
                 if s.get("today_shift_start") else "本日休み")
        lines.append(
            f"| {rank} | {s['staff_name']} | {s['score']:.1f} | "
            f"{s['sellout_rate']:.1f}% | {s['working_days']}日 | "
            f"{s['booked_slots']}枠 | {trend_str} | {shift} |"
        )

    # 2. 4週間トレンド
    lines += [
        "",
        "### 📈 過去4週間トレンド",
        "",
    ]

    available_weeks = [w for w in trend_weeks if w["snap_id"]]
    if len(available_weeks) < 2:
        lines += [
            f"※ 現在{snap_count}週分のデータを蓄積中です。",
            "トレンド分析は2週目以降から有効になります。",
            f"来週号からは前週比の変化が表示されます。",
            "",
        ]
    else:
        # 上位20名のトレンド表
        top20_names = [s["staff_name"] for s in active[:20]]
        week_labels = [f"{w['friday'][5:]}時点" for w in available_weeks]

        lines += [
            f"対象：スコア上位20名　期間：直近{len(available_weeks)}週",
            "",
            "| セラピスト | " + " | ".join(week_labels) + " | 傾向 |",
            "|-----------|" + "|".join(["---"] * len(available_weeks)) + "|-----|",
        ]
        for name in top20_names:
            week_scores = []
            for w in available_weeks:
                data = w["ranks"].get(name)
                week_scores.append(data[1] if data else None)
            score_cells = " | ".join(
                f"{s:.1f}" if s is not None else "―" for s in week_scores
            )
            arrow = trend_arrow(week_scores)
            lines.append(f"| {name} | {score_cells} | {arrow} |")

        # 急上昇・急落ピックアップ
        risers, fallers = [], []
        for name in [s["staff_name"] for s in active]:
            scores = []
            for w in available_weeks:
                d = w["ranks"].get(name)
                scores.append(d[1] if d else None)
            valid = [x for x in scores if x is not None]
            if len(valid) >= 2:
                diff = valid[0] - valid[-1]
                if diff >= 8:
                    risers.append((name, diff, valid[0]))
                elif diff <= -8:
                    fallers.append((name, diff, valid[0]))

        if risers:
            lines += ["", "**急上昇スタッフ（直近トレンド+8pt以上）**", ""]
            for name, diff, score in sorted(risers, key=lambda x: -x[1])[:5]:
                rev = pick_review(reviews_by_staff.get(name, []))
                lines.append(f"- **{name}**　+{diff:.1f}pt → スコア{score:.1f}")
                if rev:
                    lines.append(f"  > {rev}")
            lines.append("")

        if fallers:
            lines += ["**注意：急落スタッフ（直近トレンド-8pt以上）**", ""]
            for name, diff, score in sorted(fallers, key=lambda x: x[1])[:3]:
                lines.append(f"- {name}　{diff:.1f}pt → スコア{score:.1f}")
            lines.append("")

    # 3. 来週先行予約
    lines += [
        "### 🗓️ 来週の先行予約状況",
        f"（{next_mon.strftime('%m/%d')}〜{next_sun.strftime('%m/%d')}）",
        "",
    ]
    if next_week_preview:
        lines += [
            "来週分としてすでに予約が入っているスタッフです。",
            "人気スタッフほど週末には埋まり始めます。",
            "",
            "| セラピスト | 先行予約枠 | 備考 |",
            "|-----------|---------|------|",
        ]
        for r in next_week_preview[:10]:
            rate = (r["booked"] / r["capacity"] * 100) if r["capacity"] else 0
            comment = "🔥 要注意" if rate >= 30 else ("↑ 動き速い" if rate >= 15 else "")
            lines.append(
                f"| {r['staff_name']} | {r['booked']}枠 | {comment} |"
            )
        lines += [
            "",
            "> ※ 先行予約は土曜時点のデータです。週明けに急増するスタッフも存在します。",
            "",
        ]
    else:
        lines += ["来週分のデータはまだ収集されていません。", ""]

    # 4. 穴場スタッフ
    lines += [
        "### 💎 今週の穴場スタッフ",
        "",
        "スコアは中位帯だが出勤頻度が低く、**予約が取りやすい**スタッフです。",
        "「知る人ぞ知る」タイプで、口コミ件数が少ない分だけ競争率も低めです。",
        "",
    ]
    for s in bargains:
        rev = pick_review(reviews_by_staff.get(s["staff_name"], []))
        shift = (f"{s['today_shift_start']}〜{s['today_shift_end']}"
                 if s.get("today_shift_start") else "本日休み")
        lines += [
            f"#### {s['staff_name']}",
            f"スコア {s['score']:.1f}　完売率 {s['sellout_rate']:.1f}%　出勤{s['working_days']}日",
            f"今日のシフト：{shift}",
        ]
        if rev:
            lines += [f"口コミ：{rev}", ""]
        else:
            lines.append("")

    # 5. 予測精度検証（3週目以降）
    lines += ["### 🔮 先週の予測検証", ""]
    if snap_count < 3:
        lines += [
            f"※ 現在{snap_count}週分のデータです。",
            "予測精度の検証は3週目以降から掲載します。",
            "",
        ]
    else:
        lines += [
            "先週号で「早期完売が見込まれる」と予告したスタッフの実際の結果です。",
            "",
            "（※ 次週以降、実績データと照合して自動生成されます）",
            "",
        ]

    # フッター
    lines += [
        "---",
        "",
        f"*集計基準日：{yesterday.isoformat()}　蓄積スナップショット数：{snap_count}週分*",
        "*本記事は公開情報をもとにした個人による分析です。*",
    ]

    return "\n".join(lines)


if __name__ == "__main__":
    # 土曜日以外でもテスト実行できるよう target_saturday を明示
    today = date.today()
    # 直近土曜を求める（今日が土曜ならそのまま、そうでなければ次の土曜）
    days_until_sat = (5 - today.weekday()) % 7
    target = today if days_until_sat == 0 else today + timedelta(days=days_until_sat)

    article = generate(target_saturday=target)
    OUTPUT_DIR.mkdir(exist_ok=True)
    out = OUTPUT_DIR / "article_draft.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write(article)
    print(f"生成完了: {out}")
    print("\n--- プレビュー（先頭70行）---")
    for line in article.split("\n")[:70]:
        print(line)
