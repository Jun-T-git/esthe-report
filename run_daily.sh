#!/bin/bash
# ============================================================
# 日次・週次 自動実行スクリプト
#
# 【日次】毎朝6時: データ収集 + 日別集計
# 【週次】毎週土曜6時: 上記に加えて週別集計 + 記事生成
#
# crontab 登録コマンド:
#   0 6 * * * /Users/junteraoka/workspace/tmp/yoyaku/run_daily.sh
# ============================================================

set -e
DIR="/Users/junteraoka/workspace/tmp/yoyaku"
LOG="$DIR/logs/$(date +%Y%m%d).log"
mkdir -p "$DIR/logs"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

cd "$DIR"
log "===== 開始 ====="

# ── 毎日実行 ──────────────────────────────────────
log "1. 生データ収集"
python3 collect.py >> "$LOG" 2>&1

log "2. 日別集計"
python3 aggregate.py --mode=daily >> "$LOG" 2>&1

# ── 土曜のみ実行 ──────────────────────────────────
if [ "$(date +%u)" = "6" ]; then
    log "3. 週別集計（土曜）"
    python3 aggregate.py --mode=weekly >> "$LOG" 2>&1

    log "4. 記事下書き生成"
    python3 generate_article.py >> "$LOG" 2>&1

    log "===== 完了（土曜フル実行）======"
else
    log "===== 完了（日次のみ）======"
fi
