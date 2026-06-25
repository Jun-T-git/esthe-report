"""
独自スコアリングエンジン
「予約困難度」= アクセス困難 (完売率主軸) + 需要強度 (完売数の対数加点)

スコア設計:
  完売率       70% — どれだけ即時アクセス困難か (主軸)
  完売数(log) 30% — どれだけ多くの客が予約しているか (需要強度)

CAP=196 は実データの過去最大値。これを「事実上の満点完売数」とする。
出勤頻度・トレンドは本体スコアからは外し、補助情報として記事側で表示する。

データソース: weekly_summaries (aggregate.py が計算した集計層)
"""

import math
from datetime import date, timedelta
from db import get_conn


CAP = 196


def calc_score(sellout_rate: float, booked: int) -> float:
    """予約困難度スコアを 0〜100 で返す。"""
    access = min(max(sellout_rate, 0), 100) / 100
    demand = min(math.log(1 + max(booked, 0)) / math.log(1 + CAP), 1.0)
    return round((0.7 * access + 0.3 * demand) * 100, 1)


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

    # 前週の sellout_rate（補助情報「週次変化」用）
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
            "booked_slots": r["total_booked"],
            "capacity": r["total_capacity"],
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
