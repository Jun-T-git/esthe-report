"""
独自スコアリングエンジン
複数指標を組み合わせた「人気スコア」を算出する

スコア設計:
  完売率        40% — 需要の強さ（最も直接的な人気指標）
  出勤頻度      20% — 継続的な人気（稼働率が高い = 指名が途切れない）
  予約絶対数    20% — ボリューム（口コミ量との相関が強い）
  トレンド補正  20% — 直近のモメンタム（上昇中 vs 下降中）

トレンド補正は過去スナップショットとの比較でのみ有効になる。
初回は0として扱い、週ごとに精度が上がる設計。
"""

import sqlite3
from db import get_conn


def calc_score(
    sellout_rate: float,     # 0〜100+
    working_days: int,       # 0〜7
    booked_slots: int,       # 絶対数
    trend: float = 0.0,      # 前週比（完売率の差分、-100〜+100）
    max_booked: int = 1,     # 正規化用（同スナップショット内の最大値）
) -> float:
    """0〜100のスコアを返す"""
    # 各指標を0〜1に正規化
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


def get_trend(staff_no: int, current_snap_id: int) -> float:
    """前回スナップショットとの完売率差分を返す"""
    conn = get_conn()
    rows = conn.execute("""
        SELECT ss.sellout_rate
        FROM staff_stats ss
        JOIN snapshots s ON ss.snapshot_id = s.id
        WHERE ss.staff_no = ?
          AND ss.snapshot_id < ?
        ORDER BY s.collected_at DESC
        LIMIT 1
    """, (staff_no, current_snap_id)).fetchall()
    conn.close()
    if not rows:
        return 0.0
    return 0.0  # 前回値との差分は呼び出し元で計算


def score_snapshot(snap_id: int) -> list:
    """指定スナップショットの全スタッフにスコアを付与して返す"""
    conn = get_conn()

    rows = conn.execute("""
        SELECT ss.*, s.collected_at
        FROM staff_stats ss
        JOIN snapshots s ON ss.snapshot_id = s.id
        WHERE ss.snapshot_id = ?
    """, (snap_id,)).fetchall()

    if not rows:
        conn.close()
        return []

    max_booked = max(r["booked_slots"] for r in rows) or 1

    # 前スナップショットの完売率を取得
    prev_rates = {}
    prev_snap = conn.execute("""
        SELECT id FROM snapshots WHERE id < ? ORDER BY id DESC LIMIT 1
    """, (snap_id,)).fetchone()

    if prev_snap:
        prev_rows = conn.execute("""
            SELECT staff_no, sellout_rate FROM staff_stats WHERE snapshot_id = ?
        """, (prev_snap["id"],)).fetchall()
        prev_rates = {r["staff_no"]: r["sellout_rate"] for r in prev_rows}

    conn.close()

    scored = []
    for r in rows:
        trend = r["sellout_rate"] - prev_rates.get(r["staff_no"], r["sellout_rate"])
        s = calc_score(
            sellout_rate=r["sellout_rate"],
            working_days=r["working_days"],
            booked_slots=r["booked_slots"],
            trend=trend,
            max_booked=max_booked,
        )
        scored.append({
            "staff_no": r["staff_no"],
            "staff_name": r["staff_name"],
            "score": s,
            "sellout_rate": r["sellout_rate"],
            "working_days": r["working_days"],
            "booked_slots": r["booked_slots"],
            "trend": round(trend, 1),
            "today_shift_start": r["today_shift_start"],
            "today_shift_end": r["today_shift_end"],
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


if __name__ == "__main__":
    # 最新スナップショットでテスト
    conn = get_conn()
    latest = conn.execute("SELECT id FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    if latest:
        results = score_snapshot(latest["id"])
        print(f"スナップショット {latest['id']} のスコアリング結果 (上位20名)")
        print(f"{'順位':<4} {'名前':<15} {'スコア':>6} {'完売率':>7} {'出勤':>4} {'予約数':>5} {'トレンド':>7}")
        print("-" * 60)
        for i, r in enumerate(results[:20], 1):
            trend_str = f"+{r['trend']:.1f}" if r['trend'] >= 0 else f"{r['trend']:.1f}"
            print(f"{i:<4} {r['staff_name']:<14} {r['score']:>6.1f} "
                  f"{r['sellout_rate']:>6.1f}% {r['working_days']:>4}日 "
                  f"{r['booked_slots']:>5}枠 {trend_str:>7}")
    else:
        print("スナップショットなし。collect.py を先に実行してください。")
