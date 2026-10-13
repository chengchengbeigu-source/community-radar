#!/usr/bin/env bash
# 本服务常驻调度：每 INTERVAL_MIN 分钟跑一次采集；例行同步每天一次（脚本内去重）
cd "$(dirname "$0")/.." || exit 1
INTERVAL_MIN="${INTERVAL_MIN:-360}"
LOG=radar/data/runner.log
echo "=== runner 启动 $(date '+%F %T') 间隔 ${INTERVAL_MIN}min ===" | tee -a "$LOG"
while true; do
  { echo "--- $(date '+%F %T') 采集 ---"; python3 -m radar run; python3 -m radar checkin; } >> "$LOG" 2>&1
  sleep $((INTERVAL_MIN * 60))
done
