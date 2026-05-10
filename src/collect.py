"""
【日次】生データ収集スクリプト

役割: APIを叩いてスロット生データとスタッフ情報をDBに保存するだけ。
      集計・分析は aggregate.py が担う。

実行: 毎日6時（run_daily.sh 経由）
"""

import json
import time
import urllib.request
from datetime import datetime, timedelta

from db import init_db, get_conn

SID = "u15Vr2S7zV"
BASE_API = f"https://grow-appt.com/reserve/api/reserve/{SID}"
COLLECT_DAYS = 8   # 当日 + 7日先
DELAY = 1.2        # サーバー負荷軽減


def api_get(url: str, delay: float = DELAY) -> dict:
    time.sleep(delay)
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": f"https://grow-appt.com/reserve/order?SID={SID}&page=staff",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  Error: {url[:80]} → {e}")
        return {}


def fetch_staff_list() -> list:
    url = f"{BASE_API}/staff?sid={SID}&staff_id=&coupon_id="
    return api_get(url, delay=0.5).get("stafflist", [])


def fetch_sales(staff_no: int, start: datetime, menu_no: int = 2481) -> dict:
    """status API からスロットデータを取得（2グループ合算）"""
    seldate = start.strftime("%Y/%m/%d").replace("/", "%2F")
    base = (f"{BASE_API}/status?sid={SID}&staff_no={staff_no}&menu_no={menu_no}"
            f"&seldate={seldate}&coupon_no=&customer_no=&displaydaynum={COLLECT_DAYS}")
    sales = {
        **api_get(base).get("sales", {}),
        **api_get(base + "&group_no=1").get("sales", {}),
    }
    return sales


def save_staff_snapshot(conn, collected_at: str, staff: dict):
    conn.execute("""
        INSERT INTO staff_snapshots
          (collected_at, staff_no, staff_name, rank, shift_start, shift_end)
        VALUES (?,?,?,?,?,?)
    """, (
        collected_at,
        staff.get("no"),
        staff.get("name", ""),
        staff.get("rank", ""),
        staff.get("starttime"),
        staff.get("endtime"),
    ))


def save_slot_records(conn, collected_at: str, staff_no: int, sales: dict):
    """sales dict → slot_records に展開して保存"""
    rows = []
    for slot_key, entries in sales.items():
        # slot_key: "2026-05-11 14:00" 形式
        for entry in entries:
            status = entry.get("status", 0)
            rows.append((collected_at, staff_no, slot_key, status))
    if rows:
        conn.executemany(
            "INSERT INTO slot_records (collected_at, staff_no, slot_dt, status) VALUES (?,?,?,?)",
            rows,
        )


def run():
    init_db()
    collected_at = datetime.now().isoformat()
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    print(f"収集開始: {collected_at}")
    print(f"対象期間: {start.strftime('%Y-%m-%d')} ～ "
          f"{(start + timedelta(days=COLLECT_DAYS-1)).strftime('%Y-%m-%d')}")

    staff_list = fetch_staff_list()
    print(f"スタッフ数: {len(staff_list)}\n")

    conn = get_conn()
    for i, staff in enumerate(staff_list):
        no = staff.get("no")
        name = staff.get("name", "")
        print(f"[{i+1}/{len(staff_list)}] {name} (no:{no})")

        sales = fetch_sales(no, start)
        slot_count = sum(len(v) for v in sales.values())

        save_staff_snapshot(conn, collected_at, staff)
        save_slot_records(conn, collected_at, no, sales)

        print(f"  スロット数: {slot_count}")

    conn.commit()
    conn.close()
    print(f"\n収集完了 ({len(staff_list)}名)")


if __name__ == "__main__":
    run()
