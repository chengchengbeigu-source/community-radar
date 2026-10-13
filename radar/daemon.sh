#!/usr/bin/env bash
# community-radar · 7×24 常驻（跑在 GitHub Actions 上）
#
#   cron 每 4 小时起一轮，单轮常驻 RADAR_MINUTES 分钟（默认 290）。
#   290 分钟 > 4 小时 ⇒ 下一轮 cron 触发时上一轮还在跑，被 workflow 的
#   concurrency 排进队列（cancel-in-progress: false，不互相踩）；
#   上一轮一结束，排队的下一轮立刻接上 —— 24 小时里几乎没有空档。
#
#   「几点做什么」**不在这个脚本里**，全部交给 radar/scheduler.py 看表办事：
#     每 collect_minutes 分钟采集一次，有新条目立刻推送通知
#     每 progress_minutes 分钟推一条在线时长进度
#     每天 checkin_hour 点例行同步，并推结果（成功也推）
#     每天 digest_hour 点（默认 20:00）推当日总结
#     其余时间全用来跑心跳养在线时长
#   这些参数在 config.yaml → schedule 下改，不用动这个脚本。
#
#   环境变量：
#     RADAR_MINUTES        本轮常驻分钟数，默认 290
#     RADAR_ONCE           1 = 只跑一小会儿就退出（手动试跑用）
#     RADAR_FORCE          1 = 忽略活跃时段 / 已例行同步限制
#     RADAR_READ_MINUTES   临时覆盖当日阅读上限（分钟，空 = 用 config.yaml）
set -uo pipefail

MIN="${RADAR_MINUTES:-290}"
FFLAG=""
[ "${RADAR_FORCE:-0}" = "1" ] && FFLAG="--force"
RM=""
[ -n "${RADAR_READ_MINUTES:-}" ] && RM="--read-minutes ${RADAR_READ_MINUTES}"

if [ "${RADAR_ONCE:-0}" = "1" ]; then
  [ "$MIN" -gt 20 ] && MIN=20
  echo "[daemon] RADAR_ONCE=1 → 本轮只跑 ${MIN} 分钟就退出"
fi

echo "[daemon] 启动：本轮常驻 ${MIN} 分钟"
echo "[daemon] 截止：$(date -d "+${MIN} minutes" '+%F %T %Z')"
echo "[daemon] 任务表：radar/scheduler.py 按 config.yaml → schedule 执行"

# 调度器自己会在 MIN 分钟后收工；这里的 || 兜住异常，别让收尾步骤（存缓存/传产物）被跳过
python3 -m radar scheduler --minutes "$MIN" $FFLAG $RM || echo "[daemon] 调度器退出码非 0，继续收尾"

echo "[daemon] 结束"
