#!/bin/bash
# ============================================================
# 日次 自動実行スクリプト
#
# 毎日: データ収集 + 日別集計 + 週別集計 + 記事生成
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

log "1. 生データ収集"
python3 src/collect.py >> "$LOG" 2>&1

log "2. 日別集計"
python3 src/aggregate.py --mode=daily >> "$LOG" 2>&1

log "3. 週別集計"
python3 src/aggregate.py --mode=weekly >> "$LOG" 2>&1

log "4. 記事下書き生成"
python3 src/generate_article.py >> "$LOG" 2>&1

log "===== 完了 ====="
