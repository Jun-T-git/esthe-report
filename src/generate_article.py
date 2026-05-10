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
from score import score_snapshot, calc_score

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"

MASK = "████████"


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
    weeks = []
    for w in range(4):
        days_to_friday = (today.weekday() + 1) % 7 + 1
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
    return weeks


def trend_arrow(scores: list) -> str:
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
# 月間ランキング
# ====================================================================
def get_monthly_scores(n_weeks: int = 4) -> list:
    """過去n週のスナップショットからスタッフごとの平均スコアを返す"""
    conn = get_conn()
    snaps = conn.execute(
        "SELECT id FROM snapshots ORDER BY id DESC LIMIT ?", (n_weeks,)
    ).fetchall()
    snap_ids = [s["id"] for s in snaps]
    if not snap_ids:
        conn.close()
        return []

    placeholders = ",".join("?" * len(snap_ids))
    rows = conn.execute(f"""
        SELECT staff_no, staff_name,
               AVG(sellout_rate)  AS avg_sellout,
               SUM(booked_slots)  AS total_booked,
               COUNT(*)           AS weeks_appeared
        FROM staff_stats
        WHERE snapshot_id IN ({placeholders})
        GROUP BY staff_no, staff_name
        HAVING total_booked > 0
        ORDER BY avg_sellout DESC
    """, snap_ids).fetchall()
    conn.close()

    max_booked = max((r["total_booked"] for r in rows), default=1) or 1
    result = []
    for r in rows:
        score = calc_score(
            sellout_rate=r["avg_sellout"],
            working_days=min(r["weeks_appeared"] * 5, 7),
            booked_slots=r["total_booked"],
            max_booked=max_booked,
        )
        result.append({
            "staff_name": r["staff_name"],
            "avg_sellout": round(r["avg_sellout"], 1),
            "total_booked": r["total_booked"],
            "weeks_appeared": r["weeks_appeared"],
            "score": score,
        })
    result.sort(key=lambda x: x["score"], reverse=True)
    return result


# ====================================================================
# タイトル生成
# ====================================================================
def _month_week_label(d: date) -> str:
    week_of_month = (d.day - 1) // 7 + 1
    return f"{d.month}月第{week_of_month}週"


def _make_title(active: list, snap_count: int, month_week: str, monday: date = None) -> str:
    date_str = monday.strftime("%Y/%m/%d") if monday else month_week
    return f"【週間ランキング】データでわかるアロマモア セラピスト人気度分析 {date_str}週"


# ====================================================================
# 共通データ準備
# ====================================================================
def _build_context(target_saturday: date = None) -> dict:
    today = target_saturday or date.today()
    assert today.weekday() == 5 or target_saturday is not None, \
        "土曜日以外での実行は target_saturday を明示してください"

    yesterday = today - timedelta(days=1)
    next_mon = today + timedelta(days=2)
    next_sun = today + timedelta(days=8)
    monday = today + timedelta(days=2)

    this_week_snap = find_snapshot_before(yesterday) or find_snapshot_before(today)
    next_week_snap = find_latest_snapshot()
    snap_count = count_snapshots()

    if not this_week_snap:
        return None

    scored = score_snapshot(this_week_snap["id"])
    active = [s for s in scored if s["sellout_rate"] > 0]
    if not active:
        return None

    third = len(active) // 3
    bargains = sorted(active[third: third * 2], key=lambda x: x["working_days"])[:3]

    next_week_preview = []
    if next_week_snap:
        next_week_preview = get_next_week_preview(next_week_snap["id"], next_mon, next_sun)

    return {
        "today": today,
        "yesterday": yesterday,
        "monday": monday,
        "this_week_snap": this_week_snap,
        "snap_count": snap_count,
        "active": active,
        "rank_by_booked":  sorted(active, key=lambda x: x["booked_slots"], reverse=True),
        "rank_by_sellout": sorted(active, key=lambda x: x["sellout_rate"], reverse=True),
        "rank_by_score":   active,  # score_snapshot が既にスコア降順
        "monthly":         get_monthly_scores(n_weeks=4),
        "reviews_by_staff": load_reviews(),
        "trend_weeks":     build_trend_table(today),
        "next_week_preview": next_week_preview,
        "bargains":        bargains,
        "title": _make_title(active, snap_count, _month_week_label(today), monday=monday),
    }


def _header(ctx: dict) -> list:
    return [
        f"# {ctx['title']}",
        "",
        f"> **集計基準日**：{ctx['yesterday'].isoformat()}（前日までの予約を反映）  ",
        f"> **分析期間**：{ctx['this_week_snap']['period_start']} 〜 {ctx['this_week_snap']['period_end']}  ",
        f"> **分析スタッフ数**：{len(ctx['active'])}名　蓄積データ：{ctx['snap_count']}週分",
        "",
        "---",
        "",
    ]


# ====================================================================
# 無料記事
# ====================================================================
def generate_free(ctx: dict) -> str:
    lines = _header(ctx)
    active     = ctx["active"]
    yesterday  = ctx["yesterday"]
    snap_count = ctx["snap_count"]
    monthly    = ctx["monthly"]

    lines += [
        f"今週は**{len(active)}名**を4つの指標でランキングしました。",
        "**11〜15位は全項目公開**、それ以外は名前をマスクしています。詳細は有料記事をご覧ください。",
        "",
    ]

    # 1. 週間完売数 TOP10
    lines += [
        "## 📦 週間完売数ランキング TOP10",
        "",
        "今週、最も多くの予約枠が埋まったセラピストのランキングです。",
        "",
        "| 順位 | セラピスト | 予約枠数 |",
        "|------|-----------|---------|",
    ]
    for rank, s in enumerate(ctx["rank_by_booked"][:10], 1):
        lines.append(f"| {rank}位 | {MASK} | {s['booked_slots']}枠 |")
    lines += ["", "> 名前は有料記事で公開しています。", ""]

    # 2. 週間完売率 TOP10
    lines += [
        "---",
        "",
        "## 📈 週間完売率ランキング TOP10",
        "",
        "出勤枠に対して何%が予約で埋まっているか。人気の「密度」を示すランキングです。",
        "",
        "| 順位 | セラピスト | 完売率 |",
        "|------|-----------|--------|",
    ]
    for rank, s in enumerate(ctx["rank_by_sellout"][:10], 1):
        lines.append(f"| {rank}位 | {MASK} | {s['sellout_rate']:.1f}% |")
    lines += ["", "> 名前は有料記事で公開しています。", ""]

    # 3. 週間総合ランキング 1〜20位
    lines += [
        "---",
        "",
        "## 🏆 週間総合ランキング",
        "",
        "完売率・出勤頻度・予約数・週次トレンドを加重合成した独自スコアによるランキングです。",
        "**11〜15位のみ全項目公開**、それ以外は名前をマスクしています。",
        "",
        "| 順位 | セラピスト | 完売率 | スコア | 予約枠数 | 週次変化 |",
        "|------|-----------|--------|--------|---------|---------|",
    ]
    for rank, s in enumerate(ctx["rank_by_score"][:20], 1):
        trend_str = f"+{s['trend']:.1f}pt" if s["trend"] >= 0 else f"{s['trend']:.1f}pt"
        if 11 <= rank <= 15:
            lines.append(
                f"| **{rank}位** | **{s['staff_name']}** | {s['sellout_rate']:.1f}% "
                f"| {s['score']:.1f} | {s['booked_slots']}枠 | {trend_str} |"
            )
        else:
            lines.append(
                f"| {rank}位 | {MASK} | {s['sellout_rate']:.1f}% | {s['score']:.1f} | ― | ― |"
            )
    lines += ["", "> 21位以降・マスク部分の詳細は有料記事で公開しています。", ""]

    # 4. 月間総合ランキング 1〜20位
    lines += [
        "---",
        "",
        f"## 📅 月間総合ランキング（直近{min(snap_count, 4)}週）",
        "",
        "過去最大4週分を集計した月間ランキングです。継続して人気が高いセラピストを把握できます。",
        "",
    ]
    if len(monthly) < 2:
        lines += ["※ データ蓄積中です（2週目以降から表示）。", ""]
    else:
        lines += [
            "| 順位 | セラピスト | 月間平均完売率 | 月間スコア |",
            "|------|-----------|-------------|-----------|",
        ]
        for rank, s in enumerate(monthly[:20], 1):
            if 11 <= rank <= 15:
                lines.append(
                    f"| **{rank}位** | **{s['staff_name']}** | {s['avg_sellout']:.1f}% | {s['score']:.1f} |"
                )
            else:
                lines.append(
                    f"| {rank}位 | {MASK} | {s['avg_sellout']:.1f}% | {s['score']:.1f} |"
                )
        lines += ["", "> 名前の詳細は有料記事で公開しています。", ""]

    # 有料誘導
    lines += [
        "---",
        "",
        "## 有料記事でわかること",
        "",
        "- 🔓 全ランキングのマスクを外した**完全版**",
        "- 📊 21位以降を含む全スタッフランキング（シフト・出勤日数付き）",
        "- 📈 過去4週間トレンド（急上昇・急落スタッフを特定）",
        "- 🗓️ 来週の先行予約状況（今週末に動くべきスタッフ）",
        "- 💎 穴場スタッフ（スコア中位×予約が取りやすい）",
        "- 🔮 先週の予測検証",
        "",
        "👉 **[有料記事を読む]（リンクをここに貼る）**",
        "",
        "---",
        "",
        f"*集計基準日：{yesterday.isoformat()}　本記事は公開情報をもとにした個人による分析です。*",
    ]
    return "\n".join(lines)


# ====================================================================
# 有料記事
# ====================================================================
def generate_paid(ctx: dict) -> str:
    active             = ctx["active"]
    yesterday          = ctx["yesterday"]
    snap_count         = ctx["snap_count"]
    reviews_by_staff   = ctx["reviews_by_staff"]
    trend_weeks        = ctx["trend_weeks"]
    next_week_preview  = ctx["next_week_preview"]
    bargains           = ctx["bargains"]
    monthly            = ctx["monthly"]

    lines = _header(ctx)

    lines += [
        "> **収録内容**",
        "> 1. 週間完売数ランキング（全順位・名前付き）",
        "> 2. 週間完売率ランキング（全順位・名前付き）",
        "> 3. 週間総合ランキング（全スタッフ・詳細付き）",
        "> 4. 月間総合ランキング（全スタッフ）",
        "> 5. 過去4週間トレンド",
        "> 6. 来週の先行予約状況",
        "> 7. 穴場スタッフ",
        "> 8. 先週の予測検証",
        "",
        "---",
        "",
    ]

    # 1. 週間完売数ランキング
    lines += [
        "## 📦 週間完売数ランキング",
        "",
        "| 順位 | セラピスト | 予約枠数 | 完売率 | スコア |",
        "|------|-----------|---------|--------|--------|",
    ]
    for rank, s in enumerate(ctx["rank_by_booked"], 1):
        lines.append(
            f"| {rank} | {s['staff_name']} | {s['booked_slots']}枠 "
            f"| {s['sellout_rate']:.1f}% | {s['score']:.1f} |"
        )

    # 2. 週間完売率ランキング
    lines += [
        "",
        "---",
        "",
        "## 📈 週間完売率ランキング",
        "",
        "| 順位 | セラピスト | 完売率 | 予約枠数 | スコア |",
        "|------|-----------|--------|---------|--------|",
    ]
    for rank, s in enumerate(ctx["rank_by_sellout"], 1):
        lines.append(
            f"| {rank} | {s['staff_name']} | {s['sellout_rate']:.1f}% "
            f"| {s['booked_slots']}枠 | {s['score']:.1f} |"
        )

    # 3. 週間総合ランキング
    lines += [
        "",
        "---",
        "",
        "## 🏆 週間総合ランキング",
        "",
        f"集計基準：{yesterday.isoformat()}時点（前日までの予約を反映）",
        "",
        "| 順位 | セラピスト | スコア | 完売率 | 予約枠数 | 週次変化 | 今日のシフト |",
        "|------|-----------|--------|--------|---------|---------|------------|",
    ]
    for rank, s in enumerate(ctx["rank_by_score"], 1):
        trend_str = f"+{s['trend']:.1f}pt" if s["trend"] >= 0 else f"{s['trend']:.1f}pt"
        shift = (f"{s['today_shift_start']}〜{s['today_shift_end']}"
                 if s.get("today_shift_start") else "本日休み")
        lines.append(
            f"| {rank} | {s['staff_name']} | {s['score']:.1f} | "
            f"{s['sellout_rate']:.1f}% | {s['booked_slots']}枠 | {trend_str} | {shift} |"
        )

    # 4. 月間総合ランキング
    lines += [
        "",
        "---",
        "",
        f"## 📅 月間総合ランキング（直近{min(snap_count, 4)}週）",
        "",
    ]
    if len(monthly) < 2:
        lines += [f"※ データ蓄積中（{snap_count}週分）。2週目以降から有効になります。", ""]
    else:
        lines += [
            "| 順位 | セラピスト | 月間平均完売率 | 月間合計予約枠 | 月間スコア |",
            "|------|-----------|-------------|-------------|-----------|",
        ]
        for rank, s in enumerate(monthly, 1):
            lines.append(
                f"| {rank} | {s['staff_name']} | {s['avg_sellout']:.1f}% "
                f"| {s['total_booked']}枠 | {s['score']:.1f} |"
            )

    # 5. 4週間トレンド
    lines += ["", "---", "", "## 📊 過去4週間トレンド", ""]
    available_weeks = [w for w in trend_weeks if w["snap_id"]]
    if len(available_weeks) < 2:
        lines += [
            f"※ 現在{snap_count}週分のデータを蓄積中です。",
            "トレンド分析は2週目以降から有効になります。",
            "",
        ]
    else:
        top20_names = [s["staff_name"] for s in active[:20]]
        week_labels = [f"{w['friday'][5:]}時点" for w in available_weeks]
        lines += [
            f"対象：スコア上位20名　期間：直近{len(available_weeks)}週",
            "",
            "| セラピスト | " + " | ".join(week_labels) + " | 傾向 |",
            "|-----------|" + "|".join(["---"] * len(available_weeks)) + "|-----|",
        ]
        for name in top20_names:
            week_scores = [w["ranks"].get(name, (None, None))[1] for w in available_weeks]
            score_cells = " | ".join(f"{s:.1f}" if s is not None else "―" for s in week_scores)
            lines.append(f"| {name} | {score_cells} | {trend_arrow(week_scores)} |")

        risers = [(name, v[0] - v[-1], v[0])
                  for name in [s["staff_name"] for s in active]
                  for v in [[x for x in [w["ranks"].get(name, (None, None))[1]
                              for w in available_weeks] if x is not None]]
                  if len(v) >= 2 and v[0] - v[-1] >= 8]
        if risers:
            lines += ["", "**急上昇スタッフ（+8pt以上）**", ""]
            for name, diff, score in sorted(risers, key=lambda x: -x[1])[:5]:
                rev = pick_review(reviews_by_staff.get(name, []))
                lines.append(f"- **{name}**　+{diff:.1f}pt → スコア{score:.1f}")
                if rev:
                    lines.append(f"  > {rev}")
            lines.append("")

    # 6. 来週先行予約
    next_mon = ctx["monday"]
    next_sun = next_mon + timedelta(days=6)
    lines += [
        "---", "",
        f"## 🗓️ 来週の先行予約状況（{next_mon.strftime('%m/%d')}〜{next_sun.strftime('%m/%d')}）",
        "",
    ]
    if next_week_preview:
        lines += [
            "| セラピスト | 先行予約枠 | 備考 |",
            "|-----------|---------|------|",
        ]
        for r in next_week_preview[:10]:
            rate = (r["booked"] / r["capacity"] * 100) if r["capacity"] else 0
            comment = "🔥 要注意" if rate >= 30 else ("↑ 動き速い" if rate >= 15 else "")
            lines.append(f"| {r['staff_name']} | {r['booked']}枠 | {comment} |")
        lines += ["", "> ※ 土曜時点のデータです。週明けに急増するスタッフも存在します。", ""]
    else:
        lines += ["来週分のデータはまだ収集されていません。", ""]

    # 7. 穴場スタッフ
    lines += [
        "---", "",
        "## 💎 今週の穴場スタッフ",
        "",
        "スコアは中位帯だが出勤頻度が低く、予約が取りやすいスタッフです。",
        "",
    ]
    for s in bargains:
        rev = pick_review(reviews_by_staff.get(s["staff_name"], []))
        shift = (f"{s['today_shift_start']}〜{s['today_shift_end']}"
                 if s.get("today_shift_start") else "本日休み")
        lines += [
            f"#### {s['staff_name']}",
            f"スコア {s['score']:.1f}　完売率 {s['sellout_rate']:.1f}%",
            f"今日のシフト：{shift}",
        ]
        if rev:
            lines += [f"口コミ：{rev}", ""]
        else:
            lines.append("")

    # 8. 予測検証
    lines += ["---", "", "## 🔮 先週の予測検証", ""]
    if snap_count < 3:
        lines += [
            f"※ 現在{snap_count}週分のデータです。予測検証は3週目以降から掲載します。", ""
        ]
    else:
        lines += [
            "先週号で「早期完売が見込まれる」と予告したスタッフの実際の結果です。",
            "",
            "（※ 次週以降、実績データと照合して自動生成されます）",
            "",
        ]

    lines += [
        "---",
        "",
        f"*集計基準日：{yesterday.isoformat()}　蓄積：{snap_count}週分*",
        "*本記事は公開情報をもとにした個人による分析です。*",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    today = date.today()
    days_until_sat = (5 - today.weekday()) % 7
    target = today if days_until_sat == 0 else today + timedelta(days=days_until_sat)

    ctx = _build_context(target_saturday=target)
    if ctx is None:
        print("データが不足しています。collect.py を先に実行してください。")
        exit(1)

    OUTPUT_DIR.mkdir(exist_ok=True)

    free = generate_free(ctx)
    paid = generate_paid(ctx)

    out_free = OUTPUT_DIR / "article_free.md"
    out_paid = OUTPUT_DIR / "article_paid.md"

    out_free.write_text(free, encoding="utf-8")
    out_paid.write_text(paid, encoding="utf-8")

    print(f"生成完了:")
    print(f"  無料記事 → {out_free}")
    print(f"  有料記事 → {out_paid}")
    print("\n--- 無料記事プレビュー ---")
    for line in free.split("\n")[:50]:
        print(line)
