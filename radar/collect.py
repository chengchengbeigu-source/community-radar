# -*- coding: utf-8 -*-
"""采集 → 打分 → 过滤 → 入库 → 出简报 → 推送通知。

从 __main__.cmd_run 里抽出来的：radar/scheduler.py 要在常驻循环里反复调用它，
而 __main__.py 应该只管命令行解析。顺便把「推送节流」的三个小函数也搬过来，
因为它们只服务推送这一件事。
"""
import datetime, json, os, time

from radar import report, score, store
from radar.sources import REGISTRY
from radar.util import OUTBOX, DATA_DIR, ensure_dirs, notify, today_str


# 这些字样说明「没真发出去」——不能算成功，也不能吃节流窗口
_NOTIFY_DEAD = ("无通知后端", "失败", "取 token 失败", "发送失败", "跳过了", '"ok":false')


def notify_ok(r):
    """推送返回串是不是「真送达」。"""
    r = (r or "").strip()
    return bool(r) and not any(d in r for d in _NOTIFY_DEAD)


def in_window(cfg, now=None):
    """现在在不在允许采集/推送的时间窗内。

    2026-10-26 用户指定：早 8 点到晚 8 点，以手机本地时间为准。
    end 是**含**的那一小时（end=20 → 20:59 还算在窗内）。
    不配或配错就当全天开放，别因为一个配置写错把雷达整个哑掉。
    """
    try:
        start = int(cfg.get("collect_start_hour", 0))
        end = int(cfg.get("collect_end_hour", 23))
    except (TypeError, ValueError):
        return True, ""
    if not (0 <= start <= 23 and 0 <= end <= 23):
        return True, ""
    h = (now or datetime.datetime.now()).hour
    if start <= end:
        ok = start <= h <= end
    else:                       # 跨午夜，比如 22 点到次日 6 点
        ok = h >= start or h <= end
    if ok:
        return True, ""
    return False, "当前 %02d 点，不在 %02d-%02d 点窗口内" % (h, start, end)


def notify_gap_ok(cfg):
    """节流检查：距上次「成功推送」不足 notify_min_gap_minutes 分钟就跳过。

    只做检查、不落时间戳 —— 时间戳由 notify_stamp() 在推送**成功**后写，
    否则一次失败（比如密钥没配）会白白吃掉整个节流窗口，后面全静默。
    """
    gap = float(cfg.get("notify_min_gap_minutes") or 0)
    if gap <= 0:
        return True, ""
    p = os.path.join(DATA_DIR, "last_notify.txt")
    try:
        last = float(open(p, encoding="utf-8").read().strip())
    except Exception:
        last = 0.0
    waited = (time.time() - last) / 60.0
    if last and waited < gap:
        return False, "距上次推送仅 %.0f 分钟（节流 %g 分钟）" % (waited, gap)
    return True, ""


def notify_stamp():
    """记下推送成功的时刻，供 notify_gap_ok() 下次比较。"""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, "last_notify.txt"), "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except OSError:
        pass


def push(cfg, title, text, stamp=False, log=print):
    """推送一条通知。stamp=True 才吃全局节流窗口（采集简报吃，例行同步/进度/日报不吃，
    它们各自的间隔已经是节流了）。返回是否真送达。"""
    if not cfg.get("notify", True):
        log("[radar] notify=false，不推")
        return False
    r = notify(title, text)
    log("[radar] 通知结果：%s" % (r or "")[:160])
    ok = notify_ok(r)
    if ok and stamp:
        notify_stamp()
    return ok


def run_collect(cfg, window=None, source=None, do_notify=True, log=print):
    """抓一遍、打分过滤入库、出简报、推送通知。返回本次统计 dict。"""
    ensure_dirs()
    # 定时任务全天候挂着，靠这里卡窗口；手动跑（source 指定）不看窗口，
    # 否则面板点「跑一次」在半夜点不动会让人以为坏了。
    if not source:
        ok_w, why_w = in_window(cfg)
        if not ok_w:
            log("[radar] 跳过本轮采集：" + why_w)
            return {"skipped": True, "reason": why_w, "count": 0}
    win = window or cfg.get("window_days", 14)
    c = store.connect()
    total_fresh, dropped = 0, 0
    fresh_by_src = {}
    for name, mod in REGISTRY.items():
        if source and source != name:
            continue
        scfg = (cfg.get("sources") or {}).get(name) or {}
        if not scfg.get("enabled", True):
            continue
        log(f"[radar] 采集 {name} …")
        try:
            items = mod.collect(cfg, scfg, win)
        except Exception as e:
            log(f"[radar] {name} 采集异常：{type(e).__name__}: {e}")
            store.log_run(c, name, 0, 0, f"error {type(e).__name__}")
            continue
        kept, fresh, junk = 0, 0, 0
        for it in items:
            age = it.get("age_days")
            if age is not None and age > win:
                continue
            # 过滤第一关：连库都不进的垃圾（低质内容/交易/闲聊板块）
            why = score.junk_reason(it["title"], it.get("board", ""), it.get("excerpt", ""), cfg)
            if why:
                junk += 1
                continue
            s, stars, tags, reason = score.score_item(
                it["title"], it.get("board", ""), it.get("excerpt", ""), cfg)
            it.update(score=s, stars=stars, tags=tags, reason=reason)
            kept += 1
            if store.upsert_post(c, it):
                fresh += 1
        dropped += junk
        total_fresh += fresh
        if fresh:
            fresh_by_src[name] = fresh_by_src.get(name, 0) + fresh
        store.log_run(c, name, len(items), fresh, f"drop {junk}" if junk else "")
        log(f"[radar] {name}：抓到 {len(items)} 条，保留 {kept} 条，过滤 {junk} 条，新增入库 {fresh} 条")

    stat = {"fresh": total_fresh, "dropped": dropped, "pushed": False,
            "five": 0, "four": 0, "three": 0, "count": 0, "by_src": fresh_by_src}

    rows = store.unreported(c)
    if not rows:
        log("[radar] 没有未汇报的新条目，跳过简报")
        return stat

    # 过滤第二关：够门槛才进简报
    good_ids = {r["uid"] for r in report.filter_rows(rows, cfg)}
    good = [r for r in rows if r["uid"] in good_ids]
    bad = [r for r in rows if r["uid"] not in good_ids]
    if bad:
        # 低于门槛的也标记成「已汇报」，否则每一轮都会重新捞出来算一遍
        store.mark_reported(c, [r["uid"] for r in bad])
        log(f"[radar] 低于门槛（<{cfg.get('min_stars', 3)}★）丢弃 {len(bad)} 条")
    stat["dropped"] += len(bad)
    if not good:
        log("[radar] 过滤后没有值得推的新条目，不推")
        return stat

    md, note = report.build(good, win, cfg)
    path = os.path.join(OUTBOX, today_str() + ".md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    with open(path[:-3] + ".json", "w", encoding="utf-8") as f:
        json.dump([dict(r) for r in good], f, ensure_ascii=False, indent=1)
    store.mark_reported(c, [r["uid"] for r in good])
    n5 = sum(1 for r in good if r["stars"] >= 5)
    n4 = sum(1 for r in good if r["stars"] == 4)
    n3 = sum(1 for r in good if r["stars"] == 3)
    stat.update(five=n5, four=n4, three=n3, count=len(good))
    log(f"[radar] 简报已写出：{path}（{len(good)} 条，5★{n5}/4★{n4}/3★{n3}）")

    # 清理过期简报
    keep = int(cfg.get("outbox_keep_days", 60))
    cut = datetime.datetime.now() - datetime.timedelta(days=keep)
    for fn in os.listdir(OUTBOX):
        p = os.path.join(OUTBOX, fn)
        try:
            if os.path.isfile(p) and datetime.datetime.fromtimestamp(os.path.getmtime(p)) < cut:
                os.remove(p)
        except OSError:
            pass

    if do_notify:
        if source:
            ok_w, why_w = True, ""
        else:
            ok_w, why_w = in_window(cfg)
        if not ok_w:
            log("[radar] 跳过推送：" + why_w)
            return stat
        ok, why = notify_gap_ok(cfg)
        if not ok:
            log(f"[radar] 跳过推送：{why}")
        else:
            # ── 逐条推送（2026-10-26）──
            # 旧行为把 N 条压成 400 字符摘要，标题砍到 24 字，用户看到的
            # 是「MiniMax M3.1 Flash is no; Today @MiniMax_AI releases;」这种碎片。
            # 现在一条一个消息：日期 / 渠道 / 标题 / 正文 / 链接，单条读完。
            msgs = report.build_notes(good, today_str(), cfg)
            gap_s = float(cfg.get("push_gap_seconds", 3) or 0)
            sent = 0
            for i, (mt, mb, mr) in enumerate(msgs):
                if i and gap_s:
                    time.sleep(gap_s)      # 条间隔，防被平台当刷屏
                r = notify(mt, mb)
                stat.setdefault("push_detail", []).append(r)
                log(f"[radar] 推送 {i+1}/{len(msgs)}：{r}｜{mt[:40]}")
                if notify_ok(r):
                    sent += 1
            if sent:
                notify_stamp()
            stat["pushed"] = sent > 0
            stat["push_sent"] = sent
    return stat
