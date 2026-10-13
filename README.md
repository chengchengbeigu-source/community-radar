# community-radar

> 一个跑在 **GitHub Actions** 上的社区内容聚合与归档流水线。
> 按 `radar/config.yaml` 里的时间表，定时从配置的数据源抓取公开条目，
> 按关键词权重打分、去重、归档进 SQLite，并生成每日摘要；
> 摘要随运行产物（Actions Artifact）留存，也可经通知通道推送到移动端。
> 所有敏感凭据一律走仓库 Secrets，仓库内不留任何密钥。

---

## 它做什么

| 环节 | 说明 |
|---|---|
| 抓取 | 按 `config.yaml → sources` 逐个数据源拉取条目，窗口天数内去重 |
| 打分 | 关键词分组权重 + 板面权重 + 时效信号，落成星级 |
| 去噪 | 入库前丢弃明显无用条目，出摘要前再过一道门槛（见下） |
| 归档 | SQLite（`radar/data/radar.db`），保留最近若干天的摘要文件 |
| 摘要 | Markdown + JSON 双份，推送到通知通道并随 artifact 留存 |
| 例行任务 | 每天固定时点执行一次站点例行动作，结果汇总成一条通知 |
| 保活 | 空闲时间跑轻量心跳，维持会话有效性 |

## 组成

```
community-radar/
├── radar/
│   ├── config.yaml            ★ 运行配置（不含任何密钥）
│   ├── __main__.py            命令行入口
│   ├── scheduler.py           ★ 轮内任务表（读 config.yaml → schedule）
│   ├── collect.py             抓取 → 打分 → 去噪 → 入库 → 摘要 → 通知
│   ├── daemon.sh              ★ 常驻入口
│   ├── heartbeat.py           轻量心跳（纯 HTTP，不启浏览器）
│   ├── util.py                HTTP / 代理 / 通知通道
│   ├── discourse.py           论坛类数据源的通用适配层
│   ├── sources/               各数据源适配器
│   ├── checkin/               例行任务执行器
│   ├── score.py               打分与去噪规则
│   ├── report.py              摘要渲染与门槛过滤
│   ├── store.py               SQLite 存储
│   ├── github_secrets.py      从 Secrets 注入运行时配置
│   └── requirements.txt
├── dsh-plugin/radar-config/   配置面板插件源码
└── .github/workflows/         云端任务定义
```

## 时间表

「什么时候做什么」不写死在 shell 里，全部看 `radar/config.yaml → schedule`，
由 `radar/scheduler.py` 按表执行：

| 键 | 默认 | 含义 |
|---|---|---|
| `collect_minutes` | 20 | 抓取周期（分钟），有新条目即时通知 |
| `progress_minutes` | 60 | 进度上报周期（分钟） |
| `checkin_hour` | 8 | 例行任务执行时点（当天首次循环补跑） |
| `digest_hour` | 20 | 每日汇报时点 |
| `hb_slice_seconds` | 600 | 保活片长度：每这么久回来看一次任务表 |
| `config_reload_minutes` | 30 | 配置热重载周期 |

改完提交即可，常驻进程会在 `config_reload_minutes` 内自动 `git fetch` 重载配置，
不必等下一轮启动。

## 部署模型

公开仓库的 Actions 分钟数不计费，适合长时间占用 runner。三个旋钮凑出连续可用性：

```
cron: 0 */4 * * *                 每 4 小时起一轮
RADAR_MINUTES = 290               单轮常驻 290 分钟（< 单 job 6 小时上限）
timeout-minutes: 360              比常驻时间大，避免中途被回收
concurrency.cancel-in-progress: false
                                  上一轮未结束 → 新一轮排队而非取消
```

290 分钟 > 4 小时，所以下一轮触发时上一轮仍在运行、被排进队列；上一轮一结束，
排队的下一轮立刻接上。`schedule` 只当发令枪 —— 实测上游 cron 可能延迟数小时，
可靠性靠「长跑 + 排队」而不是靠准点。

## 过滤门槛

三级，从早到晚：

1. **入库前丢弃**（`radar/score.py` 的 `DROP_WORDS` / `DROP_BOARDS`）：明显无用的条目不进库，
   词表在 `config.yaml → keywords.drop` / `keywords.drop_boards`。
2. **出摘要前过滤**（`radar/report.py` 的 `filter_rows()`）：星级低于 `min_stars`
   或裸评分低于 `min_score` 的一律不进摘要。
3. **推送前防抖**（`radar/collect.py` 的 `notify_gap_ok()`）：距上次真正送达不足
   `notify_min_gap_minutes` 就跳过；窗口只在真送达后才盖时间戳。

设计取向是「宁可少杀，不可错杀」：误杀等于漏掉真内容，所以只丢弃一眼就知道没用的条目。

## 密钥管理

**仓库内任何文件都不含密钥。** 全部走仓库 → Settings → Secrets and variables → Actions。
`radar/config.yaml` 里对应字段一律留空字符串，运行时由 `radar/github_secrets.py`
从环境变量注入，**注入结果不会写回仓库**（workflow 只上传 artifact）。

配置面板插件往仓库写入时还会过一道 `config_io.py` 的 `sanitize`：
`access_token` / `user_id` / `username` / `cookie` / `password` 这类字段一律清空。

## 状态接续

`radar/data` 走 `actions/cache` 在轮次之间接续：当天已完成记录、累计进度、已入库条目。
所以「这一轮」和「下一轮」之间不会重复处理、也不会丢进度。

## 手动跑一轮

Actions → **scheduled-sync** → `Run workflow`：

| 输入 | 默认 | 说明 |
|---|---|---|
| `minutes` | 290 | 本轮常驻多少分钟 |
| `once` | 0 | 1 = 只跑一小会儿就退出（试跑） |
| `force` | 0 | 1 = 忽略时段限制 |
| `read_minutes` | 空 | 临时覆盖本次保活上限 |

命令行等价：

```bash
gh workflow run radar-daily.yml -R <owner>/community-radar -f once=1 -f minutes=5
gh run list -R <owner>/community-radar -L 5
```

## 本地开发

```bash
pip install -r radar/requirements.txt
python3 -m radar run                  # 抓一轮 + 出摘要
python3 -m radar report --force       # 重出一份摘要
python3 -m radar stats                # 归档统计
python3 -m radar checkin              # 只跑例行任务
python3 -m radar heartbeat --status   # 看当前进度
python3 -m radar scheduler --minutes 10
RADAR_MINUTES=290 bash radar/daemon.sh
```

> 心跳线是纯 HTTP，**不需要** Playwright；只有把 `read_mode` 改回 `browser` 才需要。
> 但抓取源里有一个要过 JS 挑战，所以 workflow 仍然装了 Playwright；
> 装不上时该源会自动降级到 RSS 兜底，而不是整源失败。
