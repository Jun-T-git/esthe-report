"""
データベーススキーマ

設計方針:
  生データ層   → slot_records, staff_snapshots
                  収集した事実をそのまま保存。再集計・再分析の源泉。
  集計層       → daily_aggregates, weekly_summaries
                  生データから導出。パラメータを変えて再計算可能。
  アプリ層     → generate_article.py など
                  weekly_summaries を読むだけ。スキーマ変更不要。
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "aroma_more.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        -- ================================================================
        -- 生データ層
        -- ================================================================

        -- スタッフ基本情報スナップショット（収集日ごと）
        CREATE TABLE IF NOT EXISTS staff_snapshots (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            collected_at TEXT NOT NULL,   -- ISO8601 収集日時
            staff_no     INTEGER NOT NULL,
            staff_name   TEXT NOT NULL,
            rank         TEXT,
            shift_start  TEXT,            -- 収集当日のシフト開始 "HH:MM"
            shift_end    TEXT             -- 収集当日のシフト終了 "HH:MM"
        );
        CREATE INDEX IF NOT EXISTS idx_ss_date_no
            ON staff_snapshots(date(collected_at), staff_no);

        -- スロット生データ（15分刻みの予約状況）
        -- status: 0=空き, 1=出勤なし/予約不可, 2=予約済
        CREATE TABLE IF NOT EXISTS slot_records (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            collected_at TEXT NOT NULL,   -- 収集日時
            staff_no     INTEGER NOT NULL,
            slot_dt      TEXT NOT NULL,   -- "YYYY-MM-DD HH:MM"
            status       INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sr_collected
            ON slot_records(date(collected_at), staff_no);
        CREATE INDEX IF NOT EXISTS idx_sr_slot_dt
            ON slot_records(slot_dt, staff_no);

        -- ================================================================
        -- 集計層（生データから aggregate.py が計算して書き込む）
        -- ================================================================

        -- 日別集計（スタッフ×日付）
        CREATE TABLE IF NOT EXISTS daily_aggregates (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            aggregated_at   TEXT NOT NULL,  -- 集計実行日時
            reference_date  TEXT NOT NULL,  -- 集計基準日（収集日）"YYYY-MM-DD"
            staff_no        INTEGER NOT NULL,
            staff_name      TEXT NOT NULL,
            target_date     TEXT NOT NULL,  -- 集計対象日 "YYYY-MM-DD"
            booked          INTEGER DEFAULT 0,
            unavailable     INTEGER DEFAULT 0,
            total_slots     INTEGER DEFAULT 0,
            sellout_rate    REAL,           -- booked / (total - unavailable) * 100
            UNIQUE(reference_date, staff_no, target_date)
        );
        CREATE INDEX IF NOT EXISTS idx_da_ref_staff
            ON daily_aggregates(reference_date, staff_no);
        CREATE INDEX IF NOT EXISTS idx_da_target
            ON daily_aggregates(target_date);

        -- 週別集計（スタッフ×週）
        CREATE TABLE IF NOT EXISTS weekly_summaries (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            aggregated_at   TEXT NOT NULL,
            week_start      TEXT NOT NULL,  -- 週の月曜日 "YYYY-MM-DD"
            week_end        TEXT NOT NULL,  -- 週の日曜日 "YYYY-MM-DD"
            reference_date  TEXT NOT NULL,  -- 集計基準日（前日予約まで）
            staff_no        INTEGER NOT NULL,
            staff_name      TEXT NOT NULL,
            working_days    INTEGER DEFAULT 0,
            total_booked    INTEGER DEFAULT 0,
            total_capacity  INTEGER DEFAULT 0,
            sellout_rate    REAL DEFAULT 0.0,
            score           REAL DEFAULT 0.0,
            shift_start     TEXT,
            shift_end       TEXT,
            UNIQUE(week_start, reference_date, staff_no)
        );
        CREATE INDEX IF NOT EXISTS idx_ws_week_staff
            ON weekly_summaries(week_start, staff_no);
        CREATE INDEX IF NOT EXISTS idx_ws_ref
            ON weekly_summaries(reference_date);
    """)
    conn.commit()
    conn.close()
    print(f"DB初期化完了: {DB_PATH}")


if __name__ == "__main__":
    init_db()
