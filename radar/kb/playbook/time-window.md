# 时间窗：什么时候跑、什么时候闭嘴

现象
  半夜手机响；或者你想「只在白天收」，但定时任务半夜照样爬、照样推。

根因
  调度器和采集器是两层。只改调度器（面板 tick / 云端 scheduler）不够——
  任何一条别的路径（手动 CLI、面板「全部跑一次」、云端接力轮）都能绕过它。
  反过来只改采集器也不够：调度器会白跑一轮空转。

修法
  闸放在 `radar/collect.py` 的 `run_collect()` 入口，这是**所有**采集路径的唯一收口。

  配置（`radar/config.yaml`）：
  ```yaml
  collect_start_hour: 8     # 含
  collect_end_hour: 20      # 含（20:00~20:59 还算在窗内）
  schedule:
    collect_minutes: 60     # 窗口内多久一轮
  ```

  判断函数 `in_window(cfg, now=None)`：
  - 支持跨午夜（start > end，如 22 点到次日 6 点）
  - 配置缺失或非法 → 当作全天开放，别因为一个写错的数字把雷达整个哑掉

  闸的两处：
  1. `run_collect()` 入口 —— 拦住整轮采集
  2. 推送前 —— 拦住边界情况（19:59 采完、推送队列拖到 20:01）

手动跑不受限
  `--source <name>` 时不看窗口。这是故意的：
  半夜在面板点「跑一次」没反应，用户会以为坏了，而不是以为「现在是夜里」。

云端同步
  GitHub Actions 的 cron 是 `0 */4 * * *`（每 4 小时一轮，单轮常驻 290 分钟靠排队续上），
  **不是**按时间窗排的。云端 `scheduler.do_collect()` 调 `run_collect(cfg, do_notify=True)`
  不带 source → 天然走同一道闸。所以时间窗只需要在 Python 侧配一次，云端自动跟随。
  workflow 里 `TZ=Asia/Shanghai`，`time.localtime()` 拿的是北京时间。

实测（2026-10-26 05:47）
  ```
  $ python3 -m radar run --no-notify
  [radar] 跳过本轮采集：当前 05 点，不在 08-20 点窗口内

  $ python3 -m radar run --source linuxdo --no-notify
  [linuxdo] latest.rss → 30 条
  [radar] linuxdo：抓到 57 条，保留 57 条，过滤 0 条，新增入库 10 条
  ```
