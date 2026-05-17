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
# 週次スナップショット検索（weekly_summaries ベース）
#
# 新スキーマでは「スナップショット」= (week_start, reference_date) のペア。
# 旧スキーマ呼び出し側との互換のため dict として返す。
# ====================================================================
def _snap_dict(row) -> dict:
    return {
        "week_start": row["week_start"],
        "week_end": row["week_end"],
        "reference_date": row["reference_date"],
        # 旧コード互換用エイリアス
        "collected_at": row["reference_date"],
        "period_start": row["week_start"],
        "period_end": row["week_end"],
    }


def find_snapshot_before(target_date: date):
    """target_date 以前で最新の週次サマリを返す（reference_date と week_start の両方が target_date 以下）"""
    conn = get_conn()
    row = conn.execute("""
        SELECT week_start, week_end, reference_date
        FROM weekly_summaries
        WHERE reference_date <= ?
          AND week_start <= ?
        ORDER BY week_start DESC, reference_date DESC
        LIMIT 1
    """, (target_date.isoformat(), target_date.isoformat())).fetchone()
    conn.close()
    return _snap_dict(row) if row else None


def find_latest_snapshot():
    """最新の週次サマリを返す（next_week_preview の参照日に使う）"""
    conn = get_conn()
    row = conn.execute("""
        SELECT week_start, week_end, reference_date
        FROM weekly_summaries
        ORDER BY reference_date DESC, week_start DESC
        LIMIT 1
    """).fetchone()
    conn.close()
    return _snap_dict(row) if row else None


def count_snapshots():
    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(DISTINCT week_start) FROM weekly_summaries"
    ).fetchone()[0]
    conn.close()
    return n


# ====================================================================
# 来週の先行予約データ（daily_aggregates から直接集計）
# ====================================================================
def get_next_week_preview(reference_date: str, next_mon: date, next_sun: date) -> list:
    conn = get_conn()
    rows = conn.execute("""
        SELECT staff_no, staff_name,
               SUM(booked) AS booked,
               SUM(total_slots - unavailable) AS capacity
        FROM daily_aggregates
        WHERE reference_date = ?
          AND target_date BETWEEN ? AND ?
        GROUP BY staff_no, staff_name
        HAVING booked > 0
        ORDER BY booked DESC
    """, (reference_date, next_mon.isoformat(), next_sun.isoformat())).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ====================================================================
# 4週間トレンド
# ====================================================================
def build_trend_table(target_date: date, n_weeks: int = 4) -> list:
    """target_date 以前の直近 n_weeks 個の週について、各 week_start で最新の reference_date を採用してスコアマップを返す"""
    conn = get_conn()
    rows = conn.execute("""
        SELECT week_start, MAX(reference_date) AS reference_date
        FROM weekly_summaries
        WHERE week_start <= ?
        GROUP BY week_start
        ORDER BY week_start DESC
        LIMIT ?
    """, (target_date.isoformat(), n_weeks)).fetchall()
    conn.close()

    weeks = []
    for r in rows:
        scored = score_snapshot(r["week_start"], r["reference_date"])
        rank_map = {s["staff_name"]: (i + 1, s["score"], s["sellout_rate"])
                    for i, s in enumerate(scored)}
        # 表示用に各週の金曜日を擬似ラベルとして使う
        friday = (date.fromisoformat(r["week_start"]) + timedelta(days=4)).isoformat()
        weeks.append({
            "friday": friday,
            "snap_id": f"{r['week_start']}_{r['reference_date']}",
            "ranks": rank_map,
        })
    while len(weeks) < n_weeks:
        weeks.append({"friday": "", "snap_id": None, "ranks": {}})
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
    """過去n週のweekly_summariesからスタッフごとの平均スコアを返す（各週は最新reference_dateを採用）"""
    conn = get_conn()
    week_rows = conn.execute("""
        SELECT week_start, MAX(reference_date) AS reference_date
        FROM weekly_summaries
        GROUP BY week_start
        ORDER BY week_start DESC
        LIMIT ?
    """, (n_weeks,)).fetchall()
    if not week_rows:
        conn.close()
        return []

    conditions = " OR ".join(
        "(week_start = ? AND reference_date = ?)" for _ in week_rows
    )
    params = []
    for r in week_rows:
        params.extend([r["week_start"], r["reference_date"]])

    rows = conn.execute(f"""
        SELECT staff_no, staff_name,
               AVG(sellout_rate)   AS avg_sellout,
               SUM(total_booked)   AS total_booked,
               SUM(total_capacity) AS total_capacity,
               COUNT(*)            AS weeks_appeared
        FROM weekly_summaries
        WHERE {conditions}
        GROUP BY staff_no, staff_name
        HAVING total_booked > 0
        ORDER BY avg_sellout DESC
    """, params).fetchall()
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
            "total_booked": r["total_booked"],         # 月間合計完売数
            "total_capacity": r["total_capacity"],     # 月間合計出勤枠数（15分単位）
            "weeks_appeared": r["weeks_appeared"],
            "score": score,
        })
    result.sort(key=lambda x: x["score"], reverse=True)
    return result


# ====================================================================
# 予約アクション分析（各カテゴリ5名）
# ====================================================================
def get_action_insights(scored: list, week_start: str) -> dict:
    """
    3カテゴリの推奨スタッフを各5名抽出して返す:
      1. hot_high     : 出勤多い × すぐ埋まる
      2. hot_low      : 出勤少ない × すぐ埋まる
      3. more_shifts  : 今週はいつもより出勤が多い
    """
    HOT_RATE = 80.0  # 完売率これ以上を「すぐ埋まる」とみなす
    INCREASE_RATIO = 1.3  # 過去平均比これ以上を「出勤増」とみなす

    if not scored:
        return {"hot_high": [], "hot_low": [], "more_shifts": []}

    # 出勤日数の中央値で高/低出勤を分割
    days_sorted = sorted(s["working_days"] for s in scored)
    median_days = days_sorted[len(days_sorted) // 2]

    hot_high = sorted(
        [s for s in scored
         if s["working_days"] >= median_days and s["sellout_rate"] >= HOT_RATE],
        key=lambda s: (-s["sellout_rate"], -s["booked_slots"]),
    )[:5]

    hot_low = sorted(
        [s for s in scored
         if s["working_days"] < median_days and s["sellout_rate"] >= HOT_RATE],
        key=lambda s: (-s["sellout_rate"], -s["booked_slots"]),
    )[:5]

    # 過去週平均 capacity を取得（各 week_start で最新 reference_date を採用）
    conn = get_conn()
    rows = conn.execute("""
        SELECT staff_no, AVG(total_capacity) AS avg_cap
        FROM weekly_summaries ws
        WHERE week_start < ?
          AND reference_date = (
              SELECT MAX(reference_date) FROM weekly_summaries ws2
              WHERE ws2.week_start = ws.week_start
          )
        GROUP BY staff_no
        HAVING avg_cap > 0
    """, (week_start,)).fetchall()
    conn.close()
    avg_caps = {r["staff_no"]: r["avg_cap"] for r in rows}

    candidates = []
    for s in scored:
        avg = avg_caps.get(s["staff_no"])
        if avg and s["capacity"] > avg * INCREASE_RATIO:
            candidates.append((s, s["capacity"] - avg))
    more_shifts = [s for s, _ in sorted(candidates, key=lambda x: -x[1])[:5]]

    return {"hot_high": hot_high, "hot_low": hot_low, "more_shifts": more_shifts}


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
def _build_context(target_date: date = None) -> dict:
    """
    target_date 時点で最新の週次サマリを使って記事コンテキストを構築。
    対象週 = target_date 以前で最新の week_start（通常はその週の月曜）。
    """
    today = target_date or date.today()

    this_week_snap = find_snapshot_before(today)
    if not this_week_snap:
        return None

    week_start = date.fromisoformat(this_week_snap["week_start"])
    week_end   = date.fromisoformat(this_week_snap["week_end"])
    reference  = date.fromisoformat(this_week_snap["reference_date"])
    next_mon   = week_start + timedelta(days=7)
    next_sun   = week_start + timedelta(days=13)

    next_week_snap = find_latest_snapshot()
    snap_count = count_snapshots()

    scored = score_snapshot(this_week_snap["week_start"], this_week_snap["reference_date"])
    active = [s for s in scored if s["sellout_rate"] > 0]
    if not active:
        return None

    third = len(active) // 3
    bargains = sorted(active[third: third * 2], key=lambda x: x["working_days"])[:3]

    next_week_preview = []
    if next_week_snap:
        next_week_preview = get_next_week_preview(
            next_week_snap["reference_date"], next_mon, next_sun
        )

    return {
        "today": today,
        "yesterday": reference,       # 表示用: 基準日
        "monday": week_start,         # 対象週の月曜
        "week_end": week_end,
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
        "action_insights": get_action_insights(active, this_week_snap["week_start"]),
        "title": _make_title(active, snap_count, _month_week_label(week_start), monday=week_start),
    }


def _header(ctx: dict) -> list:
    return [
        f"# {ctx['title']}",
        "",
        f"> **集計基準日**：{ctx['this_week_snap']['reference_date']}（基準日までの予約を反映）  ",
        f"> **分析期間**：{ctx['this_week_snap']['week_start']} 〜 {ctx['this_week_snap']['week_end']}  ",
        f"> **分析スタッフ数**：{len(ctx['active'])}名　蓄積データ：{ctx['snap_count']}週分",
        "",
        "> ⚠️ **注意**：完売数・完売率は **各日の朝8時(JST)時点** のスナップショットを使用しています。",
        "> 当該日の朝以降に発生した予約（当日予約など）は反映されません。",
        "",
        "**用語の定義**",
        "- **出勤枠数**：そのスタッフが出勤している時間帯の15分刻みスロット数の合計。",
        "- **完売数**：上記のうち、当該日の朝8時(JST)時点で予約が入っていた枠の数（15分単位）。",
        "- **完売率**：完売数 ÷ 出勤枠数 × 100。出勤時間のうち何%が予約で埋まったかを示す。",
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
        "## 📦 週間完売数ランキング TOP15",
        "",
        "基準日時点で最も多くの枠が予約済みになっているセラピストのランキングです。",
        "",
        "| 順位 | セラピスト | 完売数 | 出勤枠数 |",
        "|------|-----------|-------|------------|",
    ]
    for rank, s in enumerate(ctx["rank_by_booked"][:15], 1):
        lines.append(f"| {rank}位 | {MASK} | {s['booked_slots']}枠 | {s['capacity']}枠 |")
    lines.append("| 16位以降 | … | … | … |")
    lines += ["", "> 名前と16位以降の詳細は有料記事で公開しています。", ""]

    # 2. 週間完売率 TOP15
    lines += [
        "---",
        "",
        "## 📈 週間完売率ランキング TOP15",
        "",
        "出勤枠数に対して何%が完売しているか。人気の「密度」を示すランキングです。",
        "",
        "| 順位 | セラピスト | 完売率 | 完売数 | 出勤枠数 |",
        "|------|-----------|--------|-------|------------|",
    ]
    for rank, s in enumerate(ctx["rank_by_sellout"][:15], 1):
        lines.append(
            f"| {rank}位 | {MASK} | {s['sellout_rate']:.1f}% "
            f"| {s['booked_slots']}枠 | {s['capacity']}枠 |"
        )
    lines.append("| 16位以降 | … | … | … | … |")
    lines += ["", "> 名前と16位以降の詳細は有料記事で公開しています。", ""]

    # 3. 週間総合ランキング TOP15
    lines += [
        "---",
        "",
        "## 🏆 週間総合ランキング TOP15",
        "",
        "完売率・出勤頻度・完売数・週次トレンドを加重合成した独自スコアによるランキングです。",
        "**11〜15位のみ全項目公開**、それ以外は名前をマスクしています。",
        "",
        "| 順位 | セラピスト | スコア | 完売率 | 完売数 | 出勤枠数 | 週次変化 |",
        "|------|-----------|--------|--------|-------|------------|---------|",
    ]
    for rank, s in enumerate(ctx["rank_by_score"][:15], 1):
        trend_str = f"+{s['trend']:.1f}pt" if s["trend"] >= 0 else f"{s['trend']:.1f}pt"
        if 11 <= rank <= 15:
            lines.append(
                f"| **{rank}位** | **{s['staff_name']}** | {s['score']:.1f} "
                f"| {s['sellout_rate']:.1f}% | {s['booked_slots']}枠 | {s['capacity']}枠 | {trend_str} |"
            )
        else:
            lines.append(
                f"| {rank}位 | {MASK} | {s['score']:.1f} | {s['sellout_rate']:.1f}% | ― | ― | ― |"
            )
    lines.append("| 16位以降 | … | … | … | … | … | … |")
    lines += ["", "> 16位以降・マスク部分の詳細は有料記事で公開しています。", ""]

    # 4. 月間総合ランキング TOP15
    lines += [
        "---",
        "",
        f"## 📅 月間総合ランキング TOP15（直近{min(snap_count, 4)}週）",
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
        for rank, s in enumerate(monthly[:15], 1):
            if 11 <= rank <= 15:
                lines.append(
                    f"| **{rank}位** | **{s['staff_name']}** | {s['avg_sellout']:.1f}% | {s['score']:.1f} |"
                )
            else:
                lines.append(
                    f"| {rank}位 | {MASK} | {s['avg_sellout']:.1f}% | {s['score']:.1f} |"
                )
        lines.append("| 16位以降 | … | … | … |")
        lines += ["", "> 16位以降・マスク部分の詳細は有料記事で公開しています。", ""]

    # 5. 今週の予約アクション提案（全マスキング）
    insights = ctx["action_insights"]
    lines += [
        "---",
        "",
        "## 💡 今週の予約アクション提案",
        "",
        "完売状況と出勤傾向から、今週特に動くべきセラピストを3つの観点でピックアップしました。",
        "**※ 無料記事ではセラピスト名をマスクしています。実名は有料記事で公開。**",
        "",
        "### ① 出勤が多いのにすぐ埋まる／急いで予約推奨",
        "",
        "出勤日数が多めでも完売率が高い「人気の本命」。早めに予約しないと埋まります。",
        "",
    ]
    if insights["hot_high"]:
        for _ in insights["hot_high"]:
            lines.append(f"- {MASK}")
    else:
        lines.append("- 該当なし")

    lines += [
        "",
        "### ② 出勤が少ないがすぐ埋まる／急いで予約推奨",
        "",
        "出勤日数が少なく、わずかな枠が瞬時に完売するレア枠。見つけたら即予約を。",
        "",
    ]
    if insights["hot_low"]:
        for _ in insights["hot_low"]:
            lines.append(f"- {MASK}")
    else:
        lines.append("- 該当なし")

    lines += [
        "",
        "### ③ 今週はいつもより出勤が多い／予約チャンス",
        "",
        "過去週平均より出勤時間が大きく増えているセラピスト。普段取りにくい人を狙うチャンス。",
        "",
    ]
    if insights["more_shifts"]:
        for _ in insights["more_shifts"]:
            lines.append(f"- {MASK}")
    else:
        lines.append("- 該当なし（過去週データ不足の可能性）")

    lines.append("")

    # 有料誘導
    lines += [
        "---",
        "",
        "## 有料記事でわかること",
        "",
        "- 🔓 全ランキングのマスクを外した**完全版（16位以降の全スタッフ含む）**",
        "- 💡 予約アクション提案（① 出勤多×完売 ② 出勤少×完売 ③ 今週増シフト）の**実名**",
        "- 📊 シフト・出勤日数付きの詳細ランキング",
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
        "> 5. 今週の予約アクション提案",
        "> 6. 過去4週間トレンド",
        "> 7. 来週の先行予約状況",
        "> 8. 穴場スタッフ",
        "> 9. 先週の予測検証",
        "",
        "---",
        "",
    ]

    # 1. 週間完売数ランキング
    lines += [
        "## 📦 週間完売数ランキング",
        "",
        "| 順位 | セラピスト | 完売数 | 出勤枠数 | 完売率 | スコア |",
        "|------|-----------|-------|------------|--------|--------|",
    ]
    for rank, s in enumerate(ctx["rank_by_booked"], 1):
        lines.append(
            f"| {rank} | {s['staff_name']} | {s['booked_slots']}枠 | {s['capacity']}枠 "
            f"| {s['sellout_rate']:.1f}% | {s['score']:.1f} |"
        )

    # 2. 週間完売率ランキング
    lines += [
        "",
        "---",
        "",
        "## 📈 週間完売率ランキング",
        "",
        "| 順位 | セラピスト | 完売率 | 完売数 | 出勤枠数 | スコア |",
        "|------|-----------|--------|-------|------------|--------|",
    ]
    for rank, s in enumerate(ctx["rank_by_sellout"], 1):
        lines.append(
            f"| {rank} | {s['staff_name']} | {s['sellout_rate']:.1f}% "
            f"| {s['booked_slots']}枠 | {s['capacity']}枠 | {s['score']:.1f} |"
        )

    # 3. 週間総合ランキング
    lines += [
        "",
        "---",
        "",
        "## 🏆 週間総合ランキング",
        "",
        f"集計基準：{yesterday.isoformat()}時点（基準日までの予約を反映）",
        "",
        "| 順位 | セラピスト | スコア | 完売率 | 完売数 | 出勤枠数 | 週次変化 |",
        "|------|-----------|--------|--------|-------|------------|---------|",
    ]
    for rank, s in enumerate(ctx["rank_by_score"], 1):
        trend_str = f"+{s['trend']:.1f}pt" if s["trend"] >= 0 else f"{s['trend']:.1f}pt"
        lines.append(
            f"| {rank} | {s['staff_name']} | {s['score']:.1f} | "
            f"{s['sellout_rate']:.1f}% | {s['booked_slots']}枠 | {s['capacity']}枠 "
            f"| {trend_str} |"
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
            "| 順位 | セラピスト | 月間平均完売率 | 月間合計完売数 | 月間合計出勤枠数 | 月間スコア |",
            "|------|-----------|-------------|-------------|-------------------|-----------|",
        ]
        for rank, s in enumerate(monthly, 1):
            lines.append(
                f"| {rank} | {s['staff_name']} | {s['avg_sellout']:.1f}% "
                f"| {s['total_booked']}枠 | {s['total_capacity']}枠 | {s['score']:.1f} |"
            )

    # 5. 今週の予約アクション提案
    insights = ctx["action_insights"]
    lines += [
        "",
        "---",
        "",
        "## 💡 今週の予約アクション提案",
        "",
        "完売状況と出勤傾向から、今週特に動くべきセラピストを3つの観点でピックアップしました。",
        "",
        "### ① 出勤が多いのにすぐ埋まる／急いで予約推奨",
        "",
        "出勤日数が多めでも完売率が高い「人気の本命」。早めに予約しないと埋まります。",
        "",
    ]
    if insights["hot_high"]:
        for s in insights["hot_high"]:
            lines.append(f"- {s['staff_name']}")
    else:
        lines.append("- 該当なし")

    lines += [
        "",
        "### ② 出勤が少ないがすぐ埋まる／急いで予約推奨",
        "",
        "出勤日数が少なく、わずかな枠が瞬時に完売するレア枠。見つけたら即予約を。",
        "",
    ]
    if insights["hot_low"]:
        for s in insights["hot_low"]:
            lines.append(f"- {s['staff_name']}")
    else:
        lines.append("- 該当なし")

    lines += [
        "",
        "### ③ 今週はいつもより出勤が多い／予約チャンス",
        "",
        "過去週平均より出勤時間が大きく増えているセラピスト。普段取りにくい人を狙うチャンス。",
        "",
    ]
    if insights["more_shifts"]:
        for s in insights["more_shifts"]:
            lines.append(f"- {s['staff_name']}")
    else:
        lines.append("- 該当なし（過去週データ不足の可能性）")

    # 6. 4週間トレンド
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

    # 7. 来週先行予約（対象週の翌週）
    next_mon = ctx["monday"] + timedelta(days=7)
    next_sun = next_mon + timedelta(days=6)
    lines += [
        "---", "",
        f"## 🗓️ 来週の先行予約状況（{next_mon.strftime('%m/%d')}〜{next_sun.strftime('%m/%d')}）",
        "",
    ]
    if next_week_preview:
        lines += [
            "| セラピスト | 完売数 | 出勤枠数 | 完売率 | 備考 |",
            "|-----------|-------|------------|--------|------|",
        ]
        for r in next_week_preview[:10]:
            rate = (r["booked"] / r["capacity"] * 100) if r["capacity"] else 0
            comment = "🔥 要注意" if rate >= 30 else ("↑ 動き速い" if rate >= 15 else "")
            lines.append(
                f"| {r['staff_name']} | {r['booked']}枠 | {r['capacity']}枠 "
                f"| {rate:.1f}% | {comment} |"
            )
        lines += [
            "",
            f"> ※ 基準日 {ctx['this_week_snap']['reference_date']} 時点のデータです。週が進むに連れ急増するスタッフも存在します。",
            "",
        ]
    else:
        lines += ["来週分のデータはまだ収集されていません。", ""]

    # 8. 穴場スタッフ
    lines += [
        "---", "",
        "## 💎 今週の穴場スタッフ",
        "",
        "スコアは中位帯だが出勤頻度が低く、予約が取りやすいスタッフです。",
        "",
    ]
    for s in bargains:
        rev = pick_review(reviews_by_staff.get(s["staff_name"], []))
        lines += [
            f"#### {s['staff_name']}",
            f"スコア {s['score']:.1f}　完売率 {s['sellout_rate']:.1f}%"
            f"（完売 {s['booked_slots']}枠 / 出勤 {s['capacity']}枠）",
        ]
        if rev:
            lines += [f"口コミ：{rev}", ""]
        else:
            lines.append("")

    # 9. 予測検証
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
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="基準日 YYYY-MM-DD（省略時は今日）")
    args = parser.parse_args()
    target = date.fromisoformat(args.date) if args.date else date.today()

    ctx = _build_context(target_date=target)
    if ctx is None:
        print("データが不足しています。collect.py と aggregate.py を先に実行してください。")
        exit(1)

    OUTPUT_DIR.mkdir(exist_ok=True)

    free = generate_free(ctx)
    paid = generate_paid(ctx)

    # ファイル名に記事対象週（次の月曜）の日付を含める
    date_tag = ctx["monday"].strftime("%Y%m%d")
    out_free = OUTPUT_DIR / f"article_free_{date_tag}.md"
    out_paid = OUTPUT_DIR / f"article_paid_{date_tag}.md"

    out_free.write_text(free, encoding="utf-8")
    out_paid.write_text(paid, encoding="utf-8")

    print(f"生成完了:")
    print(f"  無料記事 → {out_free}")
    print(f"  有料記事 → {out_paid}")
    print("\n--- 無料記事プレビュー ---")
    for line in free.split("\n")[:50]:
        print(line)
