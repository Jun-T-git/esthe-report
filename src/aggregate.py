"""
【日次・週次】集計スクリプト

生データ（slot_records）から daily_aggregates / weekly_summaries を計算する。
集計ロジックを変えたい場合はここだけ修正すればよい。

使い方:
  python3 aggregate.py --mode=daily              # 今日分の日別集計
  python3 aggregate.py --mode=weekly             # 直近週の週別集計
  python3 aggregate.py --mode=daily --date=2026-05-10   # 指定日
  python3 aggregate.py --mode=all                # 全期間を再計算
"""

import argparse
from datetime import datetime, timedelta, date

from db import get_conn
from score import calc_score


# ================================================================
# 日別集計
# ================================================================
def aggregate_daily(reference_date: date):
    """
    reference_date のスナップショット（最新収集）をもとに
    daily_aggregates を計算・更新する。
    """
    conn = get_conn()
    ref_str = reference_date.isoformat()

    # その日の収集データの中で最新の collected_at を使う
    row = conn.execute("""
        SELECT collected_at FROM slot_records
        WHERE date(collected_at) = ?
        ORDER BY collected_at DESC LIMIT 1
    """, (ref_str,)).fetchone()

    if not row:
        print(f"  {ref_str}: スロットデータなし。collect.py を先に実行してください。")
        conn.close()
        return 0

    collected_at = row["collected_at"]
    aggregated_at = datetime.now().isoformat()

    # スタッフ一覧（当日収集分）
    staff_rows = conn.execute("""
        SELECT DISTINCT staff_no, staff_name FROM staff_snapshots
        WHERE date(collected_at) = ?
    """, (ref_str,)).fetchall()

    inserted = 0
    for s in staff_rows:
        staff_no = s["staff_no"]
        staff_name = s["staff_name"]

        # そのスタッフの全スロットを日付別に集計
        slots = conn.execute("""
            SELECT date(slot_dt) as slot_date,
                   SUM(CASE WHEN status=2 THEN 1 ELSE 0 END) as booked,
                   SUM(CASE WHEN status=1 THEN 1 ELSE 0 END) as unavailable,
                   COUNT(*) as total_slots
            FROM slot_records
            WHERE date(collected_at) = ? AND staff_no = ?
            GROUP BY slot_date
        """, (ref_str, staff_no)).fetchall()

        for slot in slots:
            available = slot["total_slots"] - slot["unavailable"]
            sellout = (round(slot["booked"] / available * 100, 1)
                       if available > 0 else None)

            conn.execute("""
                INSERT INTO daily_aggregates
                  (aggregated_at, reference_date, staff_no, staff_name,
                   target_date, booked, unavailable, total_slots, sellout_rate)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(reference_date, staff_no, target_date)
                DO UPDATE SET
                  aggregated_at=excluded.aggregated_at,
                  booked=excluded.booked,
                  unavailable=excluded.unavailable,
                  total_slots=excluded.total_slots,
                  sellout_rate=excluded.sellout_rate
            """, (
                aggregated_at, ref_str, staff_no, staff_name,
                slot["slot_date"], slot["booked"], slot["unavailable"],
                slot["total_slots"], sellout,
            ))
            inserted += 1

    conn.commit()
    conn.close()
    print(f"  日別集計完了: {ref_str} ({len(staff_rows)}名, {inserted}件)")
    return inserted


# ================================================================
# 週別集計
# ================================================================
def aggregate_weekly(week_start: date, reference_date: date):
    """
    week_start（月曜）〜 week_start+6 の週を
    reference_date 時点の daily_aggregates から集計して weekly_summaries に書く。

    reference_date は通常「土曜日」（投稿前日の金曜データを使う）。
    """
    week_end = week_start + timedelta(days=6)
    conn = get_conn()
    ref_str = reference_date.isoformat()
    aggregated_at = datetime.now().isoformat()

    # 対象週の日別集計を取得（reference_date 以前で最新のデータを使う）
    # 各 (staff_no, target_date) について reference_date 以前の最新 reference_date を使う
    rows = conn.execute("""
        SELECT da.staff_no, da.staff_name,
               da.target_date, da.booked, da.unavailable, da.total_slots, da.sellout_rate,
               ss.shift_start, ss.shift_end
        FROM daily_aggregates da
        LEFT JOIN staff_snapshots ss
          ON ss.staff_no = da.staff_no
          AND date(ss.collected_at) = (
              SELECT MAX(date(collected_at)) FROM staff_snapshots
              WHERE staff_no = da.staff_no AND date(collected_at) <= ?
          )
        WHERE da.target_date BETWEEN ? AND ?
          AND da.reference_date = (
              SELECT MAX(reference_date) FROM daily_aggregates da2
              WHERE da2.staff_no = da.staff_no
                AND da2.target_date = da.target_date
                AND da2.reference_date <= ?
          )
    """, (ref_str, week_start.isoformat(), week_end.isoformat(), ref_str)).fetchall()

    if not rows:
        print(f"  {week_start}週: daily_aggregates データなし")
        conn.close()
        return 0

    # スタッフ別に集約
    from collections import defaultdict
    by_staff: dict[int, dict] = defaultdict(lambda: {
        "staff_name": "", "working_days": 0, "total_booked": 0,
        "total_capacity": 0, "shift_start": None, "shift_end": None,
    })

    for r in rows:
        no = r["staff_no"]
        d = by_staff[no]
        d["staff_name"] = r["staff_name"]
        d["shift_start"] = d["shift_start"] or r["shift_start"]
        d["shift_end"] = d["shift_end"] or r["shift_end"]
        if r["booked"] > 0 or (r["total_slots"] - r["unavailable"]) > 0:
            d["working_days"] += 1
        d["total_booked"] += r["booked"]
        capacity = r["total_slots"] - r["unavailable"]
        d["total_capacity"] += capacity

    # スコア計算（正規化のため最大予約数を先に求める）
    max_booked = max((v["total_booked"] for v in by_staff.values()), default=1) or 1

    # 前週の sellout_rate を取得（トレンド用）
    prev_week_start = week_start - timedelta(days=7)
    prev_rates = {}
    prev_rows = conn.execute("""
        SELECT staff_no, sellout_rate FROM weekly_summaries
        WHERE week_start = ?
    """, (prev_week_start.isoformat(),)).fetchall()
    prev_rates = {r["staff_no"]: r["sellout_rate"] for r in prev_rows}

    inserted = 0
    for no, d in by_staff.items():
        sellout = (round(d["total_booked"] / d["total_capacity"] * 100, 1)
                   if d["total_capacity"] > 0 else 0.0)
        trend = sellout - prev_rates.get(no, sellout)
        score = calc_score(
            sellout_rate=sellout,
            working_days=d["working_days"],
            booked_slots=d["total_booked"],
            trend=trend,
            max_booked=max_booked,
        )

        conn.execute("""
            INSERT INTO weekly_summaries
              (aggregated_at, week_start, week_end, reference_date,
               staff_no, staff_name, working_days, total_booked,
               total_capacity, sellout_rate, score, shift_start, shift_end)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(week_start, reference_date, staff_no)
            DO UPDATE SET
              aggregated_at=excluded.aggregated_at,
              working_days=excluded.working_days,
              total_booked=excluded.total_booked,
              total_capacity=excluded.total_capacity,
              sellout_rate=excluded.sellout_rate,
              score=excluded.score,
              shift_start=excluded.shift_start,
              shift_end=excluded.shift_end
        """, (
            aggregated_at, week_start.isoformat(), week_end.isoformat(), ref_str,
            no, d["staff_name"], d["working_days"], d["total_booked"],
            d["total_capacity"], sellout, score,
            d["shift_start"], d["shift_end"],
        ))
        inserted += 1

    conn.commit()
    conn.close()
    print(f"  週別集計完了: {week_start}週 基準:{ref_str} ({inserted}名)")
    return inserted


# ================================================================
# CLI
# ================================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["daily", "weekly", "all"], default="daily")
    parser.add_argument("--date", help="基準日 YYYY-MM-DD（省略時は今日）")
    args = parser.parse_args()

    ref = date.fromisoformat(args.date) if args.date else date.today()

    if args.mode == "daily":
        print(f"日別集計: {ref}")
        aggregate_daily(ref)

    elif args.mode == "weekly":
        # 土曜投稿想定: 翌週月曜を week_start とする
        days_to_mon = (7 - ref.weekday()) % 7  # 今日が土曜なら2日後が月曜
        week_start = ref + timedelta(days=days_to_mon if days_to_mon > 0 else 7)
        print(f"週別集計: {week_start}週 (基準日: {ref})")
        aggregate_weekly(week_start, ref)

    elif args.mode == "all":
        # 全収集日を再集計
        conn = get_conn()
        dates = conn.execute("""
            SELECT DISTINCT date(collected_at) as d FROM slot_records ORDER BY d
        """).fetchall()
        conn.close()
        print(f"全期間再集計: {len(dates)}日分")
        for row in dates:
            d = date.fromisoformat(row["d"])
            aggregate_daily(d)
        # 週別も全週再計算
        if dates:
            first = date.fromisoformat(dates[0]["d"])
            last = date.fromisoformat(dates[-1]["d"])
            cur = first - timedelta(days=first.weekday())  # 最初の月曜
            while cur <= last:
                aggregate_weekly(cur, last)
                cur += timedelta(days=7)


if __name__ == "__main__":
    main()
