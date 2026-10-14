# -*- coding: utf-8 -*-
"""纯 HTTP 在线时长心跳：不启动浏览器，靠 POST /topics/timings 把 time_read 养起来。

════════════════════ 真实机制（2026-10-13 在 source-a 实测） ════════════════════
上游站点 把「在线时长」记在 user_stats.time_read，累加逻辑在
UserStat#update_time_read! 里，由 User#update_last_seen! 触发：

    diff = Time.now - last_seen_at
    user_stat.update_time_read!(diff)        # 仅当 0 < diff < 100

也就是说 —— 只要「距上一次请求」的真实墙上时钟差落在 (0, 100) 秒之间，
这段时间就会被 1:1 记进在线时长。POST /topics/timings 会累计新 last_seen，
所以每 55~75 秒发一次 timings，在线时长就跟着真实时间一起涨。

**它不能加速**：1 秒就是 1 秒，唯一能做的就是「真的挂在那里」。
实测：连续保活 5.1 分钟 → upgrade-progress 的在线时长 62.6 → 66.0 分钟。

两个坑：
  1. /u/<name>/summary.json 与 /u/<name>.json 里的 time_read 是**缓存值**（1 小时），
     看进度不准；要实时值请读 /u/<name>/upgrade-progress.json。
  2. 单次上报的 topic_time 服务端按 60 秒封顶，所以这里每次只报
     min(elapsed, 60)*1000 毫秒，不虚报。

用法：
  python3 -m radar heartbeat --seconds 1800      # 连续挂 30 分钟
  python3 -m radar heartbeat --seconds 60 --status   # 只看进度，不发心跳
"""
import json
import os
import random
import re
import time

from radar.discourse import _base, _j, _req, csrf, load_state, save_state

DEFAULT_QUIET = [2, 7]              # 夜里 2:00-7:00 不活动（风控友好，可关）
FLUSH_LO, FLUSH_HI = 50.0, 54.0     # 上报间隔：必须 < 100s（否则服务端不计），但上下浮动大一些
TOPIC_DWELL = (2 * 60, 18 * 60)     # 2~18 分钟换一帖：有时扫两眼就走，有时一帖看很久
PAGE_CHOICES = (0, 1, 2, 3)
# 像人一样中途离开：连续活动这么久之后，随机歇一会儿（这段时间不产生任何请求）
BREAK_EVERY = (35 * 60, 70 * 60)
BREAK_LEN = (3 * 60, 12 * 60)


class AuthExpired(Exception):
    """登录态失效（cookie 过期 / 被踢下线）—— 需要人重新登录换 cookie，机器修不了。"""


def _in_window(hour, w):
    """w = [a, b] 左闭右开；a == b 视为全天；空/None 视为「不限时段」。"""
    if not w:
        return True
    a, b = int(w[0]), int(w[1])
    if a == b:
        return True
    return (a <= hour < b) if a < b else (hour >= a or hour < b)


def _pick(e, proxy, recent):
    page = random.choice(PAGE_CHOICES)
    st, b = _req(e, "/latest.json?page=%d&no_definitions=true&ascending=false" % page,
                 proxy=proxy)
    tops = ((_j(b).get("topic_list") or {}).get("topics")) or []
    pool = [t for t in tops
            if 5 <= (t.get("posts_count") or 0) <= 400
            and not t.get("closed") and not t.get("archived")
            and t.get("id") not in recent]
    if not pool:
        pool = tops
    if not pool:
        return None
    t = random.choice(pool)
    return {"id": t["id"], "slug": t.get("slug") or "t",
            "posts": max(1, int(t.get("posts_count") or 1)),
            "title": (t.get("title") or "")[:32]}


def _flush(e, topic, posts, ms, proxy):
    """POST /topics/timings。表单嵌套参数，和站点 jQuery 发的完全一致：
    topic_id=<id>&topic_time=<ms>&timings[<楼层>]=<该层毫秒>"""
    per = max(1, int(ms / max(1, len(posts))))
    data = {"topic_id": topic["id"], "topic_time": int(ms)}
    for pn in posts:
        data["timings[%s]" % pn] = per
    return _req(e, "/topics/timings", data=data, proxy=proxy,
                headers={"X-CSRF-Token": csrf(e, proxy),
                         "X-SILENCE-LOGGER": "true",
                         "Discourse-Background": "true"})


def progress(e, proxy=None):
    """实时阅读进度。返回形如 '在线时长（分钟）：66/600' 的字符串，取不到返回 None。

    注意：走 upgrade-progress.json，不是 summary.json（后者有 1 小时缓存）。"""
    proxy = proxy or (e.get("proxy") or "direct")
    st, b = _req(e, "/session/current.json", proxy=proxy)
    un = ((_j(b).get("current_user") or {}).get("username")) or e.get("username")
    if not un:
        return None
    st, b = _req(e, "/u/%s/upgrade-progress.json" % un, proxy=proxy)
    if st != 200:
        return None
    d = _j(b)
    for it in list(d.get("unmet_conditions") or []) + list(d.get("met_conditions") or []):
        s = it if isinstance(it, str) else json.dumps(it, ensure_ascii=False)
        if "阅读时长" in s:
            return s
    # 本级已经没有「阅读时长」条件了 —— 通常是**已经升级**，下一级考核别的指标。
    # 千万别返回 None：上层拿到 None 会当成「取不到 / 登录态可能失效」，
    # 于是升级成功后每小时推一条「取不到」，看起来像又坏了（2026-10-14 想到的坑）。
    return "当前等级 %s（本级不再考核阅读时长）" % (
        d.get("current_level_name") or ("TL%s" % d.get("current_level")))


def auth_ok(e, proxy=None):
    """探测登录态。返回已登录的用户名；未登录（cookie 过期/被踢）返回 None。

    走 /session/current.json —— 未登录时它回 404 且响应头带 discourse-logged-out: 1，
    而 /session/csrf 匿名也会给 token，是假活，不能拿来判断。"""
    proxy = proxy or (e.get("proxy") or "direct")
    try:
        st, b = _req(e, "/session/current.json", proxy=proxy)
    except Exception:
        return None
    if st != 200:
        return None
    return ((_j(b).get("current_user") or {}).get("username")) or None


_NUMRE = re.compile(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)")


def parse_progress(s):
    """把 '阅读时长（分钟）：552/600' 解析成 (552.0, 600.0)；解析不出返回 (None, None)。"""
    m = _NUMRE.search(str(s or ""))
    if not m:
        return (None, None)
    try:
        return (float(m.group(1)), float(m.group(2)))
    except ValueError:
        return (None, None)


def _upgrade_snapshot(e, proxy=None):
    """原始 upgrade-progress.json；取不到返回 None。"""
    proxy = proxy or (e.get("proxy") or "direct")
    st, b = _req(e, "/session/current.json", proxy=proxy)
    un = ((_j(b).get("current_user") or {}).get("username")) or e.get("username")
    if not un:
        return None
    st, b = _req(e, "/u/%s/upgrade-progress.json" % un, proxy=proxy)
    if st != 200:
        return None
    d = _j(b)
    return d if isinstance(d, dict) else None


def progress_level(e, proxy=None):
    """当前等级 + 原始快照，形如 (0, {...})；取不到给 (None, None)。"""
    d = _upgrade_snapshot(e, proxy)
    if not d:
        return (None, None)
    try:
        lv = int(d.get("current_level"))
    except (TypeError, ValueError):
        lv = None
    return (lv, d)


def _reached_and_notify(e, cfg, pg, log=print):
    """站内进度已到目标（pv >= pt）就推 🎉 并返回 True，让调用方停机。

    为什么要在心跳里就地做：调度器的进度轮询是 progress_minutes（默认 60）分钟一次，
    刚好卡在 60 分钟，达标那一刻最多还要空转一小时才被发现。这里就地判定、就地停。
    去重键与 scheduler.do_progress 完全一致（scheduler.json 的 congrats:<站名>:<标记>），
    所以两边谁先发现谁推，只会有一条 📨。
    """
    try:
        pv, pt = parse_progress(pg)
    except Exception:
        return False
    if pv is None or pt is None or pt <= 0 or pv < pt:
        return False
    name = e.get("name") or "?"
    try:
        from radar.scheduler import notify_unlocked
        notify_unlocked(name, cfg, pg, "T%d" % int(pt), log=log)
    except Exception as ex:
        log("  [%s] 达标通知推送失败（不致命，调度器的进度轮询还会再试）：%s: %s"
            % (name, type(ex).__name__, ex))
    log("  [%s] 站内进度已到 %.0f/%.0f —— 达标停机，不再发 timings" % (name, pv, pt))
    return True


def run(e, cfg, seconds, log=print):
    """连续发 timings 最多 seconds 秒，返回本次实际计入的秒数。

    - 遵守 daily.active_hours（活跃时段外不启动，和 run_daily 的风控一致）
    - 遵守 daily.quiet_hours（默认 2:00-7:00，期间只睡不发；写成 [] 表示不静默）
    - daily._force / 环境变量 RADAR_FORCE=1：**忽略上面两条时段限制**，立刻开始保活
    - 像人一样：每隔 35~70 分钟随机离开 3~12 分钟；每帖停留 2~18 分钟随机
    - 登录态失效会抛 AuthExpired（不在这里自作主张，交给调度器推 QQ）
    - 累加写回 missions.json 的 read:<日期>，run_daily 的当日预算据此跳过重复阅读
    """
    name = e.get("name") or _base(e)
    d = e.get("daily") or {}
    proxy = e.get("proxy") or "direct"
    # 取值必须区分「没配」和「配成空」：[] 是假值，写成 `or DEFAULT_QUIET` 会把
    # 「关掉夜间静默」这个意图悄悄换回默认的 2:00-7:00，于是永远关不掉。
    quiet = d.get("quiet_hours")
    if quiet is None:
        quiet = DEFAULT_QUIET
    # ── 强制模式（2026-10-15 修）──────────────────────────────────────────
    # 病根：workflow 的 force=1 写着「忽略时段限制」，__main__.py 也确实把
    # e["_force"] 打上了 —— 但**本文件从来没读过它**（全仓 grep `_force` 只有
    # __main__.py:36 一处）。于是凌晨手动派发的 run 一律撞在 active_hours 上直接
    # return 0，表现就是「点了强制运行，阅读时长一动不动」。
    # 另外 e["_force"] 只在启动时打一次，而配置热重载会把整份 cfg 换掉
    # （_reload_config → load_config），标记随之丢失；所以环境变量这条路也得认，
    # daemon.sh 在整个 job 生命周期里都带着 RADAR_FORCE。
    force = bool(e.get("_force")) or os.environ.get("RADAR_FORCE", "0") == "1"
    today = time.strftime("%Y-%m-%d")
    st_all = load_state()
    mine = st_all.setdefault(name, {})
    read_key = "read:%s" % today

    hrs = d.get("active_hours") or [8, 23]
    if not force and not _in_window(time.localtime().tm_hour, hrs):
        log("  [%s] 不在活跃时段 %d:00-%d:00，心跳不启动" % (name, hrs[0], hrs[1]))
        return 0.0
    if force:
        log("  [%s] 强制模式：跳过活跃时段/夜间静默判定，立刻开始保活" % name)

    # 登录态体检：这是整套系统唯一必须人来修的点，所以每一片都先探一下，
    # 死了就抛 AuthExpired 让调度器推 QQ 告警（而不是闷头刷 403 刷一整晚）。
    who = auth_ok(e, proxy)
    if not who:
        raise AuthExpired(name)

    # ── 达标即停（2026-10-14 修）──────────────────────────────────────────
    # 以前心跳眼里只有「累计 600 分钟」这个死目标，完全不看 stop_at.trust_level：
    # 升级到目标等级之后还会继续刷一整天，纯属白耗、还多出风控面。现在先看等级。
    stop_tl = (e.get("stop_at") or {}).get("trust_level")
    if stop_tl is not None:
        try:
            lvl, _snap = progress_level(e, proxy)
        except Exception:
            lvl = None
        if lvl is not None and float(lvl) >= float(stop_tl):
            log("  [%s] 站内等级已到 %s（stop_at.trust_level=%s），心跳不再启动"
                % (name, lvl, stop_tl))
            mine["stop_hit"] = int(float(lvl))
            save_state(st_all)
            return 0.0

    budget = float(d.get("read_minutes") or 120) * 60
    done = float(mine.get(read_key) or 0)
    left = budget - done

    # ── 收尾自适应（2026-10-14 修）────────────────────────────────────────
    # 病根：daily.read_minutes（240）是**脚本侧当日额度**，而升级目标（600 分钟）是
    # **累计**的。当天额度一烧完，之后一整天心跳全部空转 —— 站内数字冻在 552/600，
    # 收到的那条「在线进度」就变成一动不动的同一个数字，看起来像「不再更新汇报」。
    # 修法：只要**站内**距目标只剩 finish_gap_minutes 以内，就把额度临时放宽到刚好
    # 够收尾，一口气补完；平时照旧按 read_minutes 慢节奏，风控节奏不变。
    gap_min = float(d.get("finish_gap_minutes", 60) or 0)
    if gap_min > 0:
        try:
            pv, pt = parse_progress(progress(e, proxy))
        except Exception:
            pv, pt = (None, None)
        if pv is not None and pt is not None and pt > pv and (pt - pv) <= gap_min:
            if done + (pt - pv) * 60 > budget:
                log("  [%s] 收尾模式：站内 %.0f/%.0f，距目标还剩 %.1f 分钟（≤ %.0f），"
                    "临时把今日额度 %.0f→%.0f 分钟，一口气补完"
                    % (name, pv, pt, pt - pv, gap_min, budget / 60.0,
                       (done + (pt - pv) * 60) / 60.0))
                budget = done + (pt - pv) * 60
                left = budget - done

    if left < 60:
        try:
            pg = progress(e, proxy)
        except Exception:
            pg = None
        mine["stall"] = round(done / 60.0, 1)
        save_state(st_all)
        log("  [%s] 今日阅读预算已满（%.1f/%.0f 分钟），心跳跳过%s"
            % (name, done / 60.0, budget / 60.0, ("｜站内实时：" + pg) if pg else ""))
        return 0.0

    seconds = float(min(float(seconds), left))
    if seconds < 60:
        log("  [%s] 本次可用时长不足 1 分钟，心跳跳过" % name)
        return 0.0

    log("  [%s] 心跳启动：本次最多 %.1f 分钟（今日 %.1f/%.0f 分钟）"
        % (name, seconds / 60.0, done / 60.0, budget / 60.0))
    start = last_flush = time.time()
    next_break = start + random.uniform(*BREAK_EVERY)
    topic = None
    topic_started = topic_dwell = 0.0
    cursor, recent = 1, []
    got = 0.0
    n_ok = n_bad = n_auth = 0
    last_log = 0.0

    while True:
        now = time.time()
        ran = now - start
        if ran >= seconds:
            break

        if quiet and (not force) and _in_window(time.localtime().tm_hour, quiet):
            nap = max(1.0, min(600.0, seconds - ran))
            log("  [%s] 夜间安静时段 %d:00-%d:00，暂停 %.0f 分钟"
                % (name, quiet[0], quiet[1], nap / 60.0))
            time.sleep(nap)
            continue

        # 像人一样中途离开：活动一段之后随机歇几分钟，期间一个请求都不发
        if now >= next_break:
            nap = max(1.0, min(random.uniform(*BREAK_LEN), seconds - ran))
            log("  [%s] 歇一会儿（%.1f 分钟），先离开一下" % (name, nap / 60.0))
            time.sleep(nap)
            next_break = time.time() + random.uniform(*BREAK_EVERY)
            last_flush = time.time()     # 离开期间的空档本来就不该记进在线时长
            continue

        if topic is None or now - topic_started > topic_dwell:
            t = _pick(e, proxy, set(recent))
            if not t:
                time.sleep(30)
                continue
            topic = t
            topic_started = now
            topic_dwell = random.uniform(*TOPIC_DWELL)
            cursor = random.randint(1, max(1, min(3, topic["posts"])))
            recent = ([topic["id"]] + recent)[:15]
            _req(e, "/t/%s.json" % topic["id"], proxy=proxy)   # 真开帖，别只看列表
            last_flush = time.time()    # 这一发也累计新了服务端 last_seen，记账基准跟着走
            log("  [%s] 进入主题 #%s《%s》（%d 楼，停留 %.1f 分钟）"
                % (name, topic["id"], topic["title"], topic["posts"], topic_dwell / 60.0))

        gap = random.uniform(FLUSH_LO, FLUSH_HI)
        elapsed = now - last_flush
        if elapsed < gap:
            time.sleep(min(5.0, gap - elapsed))
            continue

        ms = int(min(elapsed, 60.0) * 1000)
        posts, cur = [], cursor
        for _ in range(random.choice([1, 1, 2, 2, 2, 3, 4])):
            cur = min(cur, topic["posts"])
            posts.append(cur)
            if random.random() < 0.7:
                cur += 1
        posts = sorted(set(posts))
        cursor = cur

        try:
            st, _b = _flush(e, topic, posts, ms, proxy)
        except Exception as ex:
            st, _b = 0, "%s: %s" % (type(ex).__name__, ex)
        last_flush = now

        if st == 200:
            n_ok += 1
            n_auth = 0
            got += elapsed
            mine[read_key] = round(done + got, 1)
            save_state(st_all)
        else:
            n_bad += 1
            log("  [%s] timings HTTP %s，退避 30s" % (name, st))
            if st == 0:
                time.sleep(3)     # 网络层抖动（SSL EOF/连接被掐）快速重试，别赔 30 秒
            elif st in (403, 429):
                time.sleep(30)
            # 401/403 连着两次就往登录态上查一下 —— 是 cookie 过期了就立刻上报，
            # 别在这里闷头重试（机器修不了，只有人能重新登录）。
            if st in (401, 403):
                n_auth += 1
                if n_auth >= 2 and not auth_ok(e, proxy):
                    log("  [%s] 登录态已失效（连续 %d 次 HTTP %s 且 /session/current.json 无用户）"
                        % (name, n_auth, st))
                    save_state(st_all)
                    raise AuthExpired(name)

        if now - last_log > 600:
            last_log = now
            pg = progress(e, proxy)
            log("  [%s] 已挂 %.1f 分钟｜上报 %d 次（失败 %d）｜记账 %.1f 分钟%s"
                % (name, ran / 60.0, n_ok, n_bad, mine.get(read_key, 0) / 60.0,
                   ("｜站内实时：" + pg) if pg else ""))
            # 站内已经刷满 → 就地停机 + 就地推 🎉（别再等调度器那一小时一次轮询）。
            if _reached_and_notify(e, cfg, pg, log):
                break

    save_state(st_all)
    log("  [%s] 心跳结束：本次 +%.1f 分钟，今日累计 %.1f 分钟"
        % (name, got / 60.0, mine.get(read_key, 0) / 60.0))
    return got
