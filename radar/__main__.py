# -*- coding: utf-8 -*-
"""本服务入口：python3 -m radar [scheduler|run|checkin|heartbeat|report|stats]

本文件只管命令行解析，真正的活在各模块里：
  radar/collect.py    采集 → 打分 → 过滤 → 入库 → 简报 → 推送通知
  radar/scheduler.py  常驻循环里按任务表办事（例行同步 / 采集 / 进度 / 日报 / 心跳）
  radar/heartbeat.py  纯 HTTP 心跳累计在线时长
  radar/checkin.py    例行同步
"""
import argparse, json, os, sys

from radar import collect, report, scheduler, store
from radar.util import OUTBOX, load_config, today_str


def cmd_run(a):
    """采集一轮 + 出简报 + 推送通知（命令行手动用）。"""
    cfg = load_config()
    st = collect.run_collect(cfg, window=a.window, source=a.source,
                             do_notify=not a.no_notify, log=print)
    return 0


def cmd_scheduler(a):
    """常驻：在 a.minutes 分钟里按任务表循环。daemon.sh 调的就是这个。"""
    cfg = load_config()
    if a.read_minutes:
        (cfg.setdefault("checkins") or [])
        for e in cfg.get("checkins") or []:
            d = e.get("daily")
            if isinstance(d, dict):
                d["read_minutes"] = float(a.read_minutes)
        print("[radar] 阅读上限临时覆盖为 %s 分钟" % a.read_minutes)
    if a.force:
        for e in cfg.get("checkins") or []:
            e["_force"] = True
    return scheduler.run(cfg, a.minutes, log=print)


def cmd_report(a):
    cfg = load_config()
    c = store.connect()
    rows = store.unreported(c)
    good = report.filter_rows(rows, cfg)
    if not good and not a.force:
        print("[radar] 过滤后没有未汇报条目"); return 0
    md, note = report.build(good, a.window or cfg.get("window_days", 14), cfg)
    p = os.path.join(OUTBOX, today_str() + ".md")
    open(p, "w", encoding="utf-8").write(md)
    print(f"[radar] 重新生成 {p}（{len(good)} 条，原始 {len(rows)} 条）")
    return 0


def cmd_stats(a):
    c = store.connect()
    tot, by, runs = store.stats(c)
    print(f"[radar] 库存帖子 {tot} 条，运行记录 {runs} 次")
    for b in by:
        print(f"   {b['source']:<10} {b['n']:>5} 条  最近 {b['last']}")
    print("  最近 5 次运行：")
    for r in c.execute("SELECT ts,source,fetched,fresh,note FROM runs ORDER BY id DESC LIMIT 5"):
        print(f"   {r['ts']} {r['source']:<10} 抓{r['fetched']:>4} 新{r['fresh']:>4} {r['note']}")
    return 0


def cmd_checkin(a):
    """例行同步 + 推送通知 结果。"""
    return 0 if scheduler.do_checkin(load_config(), log=print, force=a.force,
                                     no_sleep=a.no_sleep, only=a.only) is not None else 1


def cmd_heartbeat(a):
    """--status 只看不推；否则保活累计时长。"""
    cfg = load_config()
    if a.status:
        scheduler.do_progress(cfg, log=print, push_it=False)
        return 0
    from radar.heartbeat import run as hbrun
    n = 0
    for e in cfg.get("checkins") or []:
        if not e.get("enabled", True):
            continue
        if a.only and e.get("name") != a.only:
            continue
        if (e.get("kind") or "").lower() not in ("discourse", "nodeloc", "forum"):
            continue
        hbrun(e, cfg, a.seconds, log=print)
        n += 1
    if not n:
        print("[radar] 没有可保活的条目")
    return 0


def main():
    ap = argparse.ArgumentParser(prog="radar")
    sub = ap.add_subparsers(dest="cmd")

    d = sub.add_parser("scheduler", help="常驻循环：按任务表例行同步/采集/报进度/发日报（7×24 用）")
    d.add_argument("--minutes", type=float, default=290, help="本轮常驻多少分钟，默认 290")
    d.add_argument("--read-minutes", type=float, default=0, help="临时覆盖当日阅读上限（分钟）")
    d.add_argument("--force", action="store_true", help="忽略活跃时段/已例行同步限制")
    d.set_defaults(func=cmd_scheduler)

    r = sub.add_parser("run", help="采集 + 打分 + 出简报 + 通知")
    r.add_argument("--window", type=float)
    r.add_argument("--source")
    r.add_argument("--no-notify", action="store_true")
    r.set_defaults(func=cmd_run)

    p = sub.add_parser("report", help="只从库里重新生成简报")
    p.add_argument("--window", type=float)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_report)

    s = sub.add_parser("stats", help="看库存与运行记录")
    s.set_defaults(func=cmd_stats)

    k = sub.add_parser("checkin", help="执行例行同步并推送通知")
    k.add_argument("--only")
    k.add_argument("--no-sleep", action="store_true")
    k.add_argument("--force", action="store_true")
    k.set_defaults(func=cmd_checkin)

    h = sub.add_parser("heartbeat", help="纯 HTTP 保活累计在线时长（不启动浏览器）")
    h.add_argument("--seconds", type=float, default=1800, help="本次挂多久（秒），默认 1800")
    h.add_argument("--only", help="只对指定例行同步项心跳")
    h.add_argument("--status", action="store_true", help="只打印站内实时阅读进度（不推）")
    h.set_defaults(func=cmd_heartbeat)

    a = ap.parse_args()
    if not getattr(a, "func", None):
        ap.print_help(); return 1
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
