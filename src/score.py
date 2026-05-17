"""
独自スコアリングエンジン
複数指標を組み合わせた「人気スコア」を算出する

スコア設計:
  完売率        40% — 需要の強さ（最も直接的な人気指標）
  出勤頻度      20% — 継続的な人気（稼働率が高い = 指名が途切れない）
  予約絶対数    20% — ボリューム（口コミ量との相関が強い）
  トレンド補正  20% — 直近のモメンタム（上昇中 vs 下降中）

トレンド補正は前週との比較でのみ有効になる。
初回は0として扱い、週ごとに精度が上がる設計。

データソース: weekly_summaries（aggregate.py が計算した集計層）
"""

from datetime import date, timedelta
from db import get_conn


def calc_score(
    sellout_rate: float,     # 0〜100+
    working_days: int,       # 0〜7
    booked_slots: int,       # 絶対数
    trend: float = 0.0,      # 前週比（完売率の差分、-100〜+100）
    max_booked: int = 1,     # 正規化用（同スナップショット内の最大値）
) -> float:
    """0〜100のスコアを返す"""
    s_sellout = min(sellout_rate, 100) / 100
    s_freq    = min(working_days, 7) / 7
    s_volume  = min(booked_slots, max_booked) / max_booked if max_booked > 0 else 0
    s_trend   = (min(max(trend, -30), 30) + 30) / 60  # -30〜+30 → 0〜1

    score = (
        s_sellout * 0.40 +
        s_freq    * 0.20 +
        s_volume  * 0.20 +
        s_trend   * 0.20
    ) * 100

    return round(score, 1)


def score_snapshot(week_start: str, reference_date: str) -> list:
    """指定週・基準日の weekly_summaries を読み、トレンドを付与しスコア降順で返す"""
    conn = get_conn()

    rows = conn.execute("""
        SELECT staff_no, staff_name, sellout_rate, working_days,
               total_booked, total_capacity, score
        FROM weekly_summaries
        WHERE week_start = ? AND reference_date = ?
    """, (week_start, reference_date)).fetchall()

    if not rows:
        conn.close()
        return []

    # 前週の sellout_rate（トレンド差分用）
    prev_ws = (date.fromisoformat(week_start) - timedelta(days=7)).isoformat()
    prev_rows = conn.execute("""
        SELECT staff_no, sellout_rate
        FROM weekly_summaries
        WHERE week_start = ?
          AND reference_date = (
              SELECT MAX(reference_date) FROM weekly_summaries
              WHERE week_start = ?
          )
    """, (prev_ws, prev_ws)).fetchall()
    prev_rates = {r["staff_no"]: r["sellout_rate"] for r in prev_rows}
    conn.close()

    scored = []
    for r in rows:
        trend = r["sellout_rate"] - prev_rates.get(r["staff_no"], r["sellout_rate"])
        scored.append({
            "staff_no": r["staff_no"],
            "staff_name": r["staff_name"],
            "score": r["score"],
            "sellout_rate": r["sellout_rate"],
            "working_days": r["working_days"],
            "booked_slots": r["total_booked"],         # 完売数
            "capacity": r["total_capacity"],           # 出勤枠数
            "trend": round(trend, 1),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


if __name__ == "__main__":
    conn = get_conn()
    latest = conn.execute("""
        SELECT week_start, MAX(reference_date) AS reference_date
        FROM weekly_summaries
        GROUP BY week_start
        ORDER BY week_start DESC
        LIMIT 1
    """).fetchone()
    conn.close()
    if latest:
        results = score_snapshot(latest["week_start"], latest["reference_date"])
        print(f"週 {latest['week_start']} (基準日 {latest['reference_date']}) のスコアリング結果 (上位20名)")
        print(f"{'順位':<4} {'名前':<15} {'スコア':>6} {'完売率':>7} {'出勤':>4} {'予約数':>5} {'トレンド':>7}")
        print("-" * 60)
        for i, r in enumerate(results[:20], 1):
            trend_str = f"+{r['trend']:.1f}" if r['trend'] >= 0 else f"{r['trend']:.1f}"
            print(f"{i:<4} {r['staff_name']:<14} {r['score']:>6.1f} "
                  f"{r['sellout_rate']:>6.1f}% {r['working_days']:>4}日 "
                  f"{r['booked_slots']:>5}枠 {trend_str:>7}")
    else:
        print("weekly_summaries が空です。aggregate.py を先に実行してください。")
