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
import random
import time

from radar.discourse import _base, _j, _req, csrf, load_state, save_state

DEFAULT_QUIET = [2, 7]              # 夜里 2:00-7:00 不活动（风控友好，可关）
FLUSH_LO, FLUSH_HI = 55.0, 75.0     # 上报间隔，必须 < 100s，否则服务端不计
TOPIC_DWELL = (4 * 60, 11 * 60)     # 4~11 分钟换一帖
PAGE_CHOICES = (0, 1, 2, 3)


def _in_window(hour, w):
    """w = [a, b] 左闭右开；a == b 视为全天。"""
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
    return None


def run(e, cfg, seconds, log=print):
    """连续发 timings 最多 seconds 秒，返回本次实际计入的秒数。

    - 遵守 daily.active_hours（活跃时段外不启动，和 run_daily 的风控一致）
    - 遵守 daily.quiet_hours（默认 2:00-7:00，期间只睡不发）
    - 累加写回 missions.json 的 read:<日期>，run_daily 的当日预算据此跳过重复阅读
    """
    name = e.get("name") or _base(e)
    d = e.get("daily") or {}
    proxy = e.get("proxy") or "direct"
    quiet = d.get("quiet_hours") or DEFAULT_QUIET
    today = time.strftime("%Y-%m-%d")
    st_all = load_state()
    mine = st_all.setdefault(name, {})
    read_key = "read:%s" % today

    hrs = d.get("active_hours") or [8, 23]
    if not _in_window(time.localtime().tm_hour, hrs):
        log("  [%s] 不在活跃时段 %d:00-%d:00，心跳不启动" % (name, hrs[0], hrs[1]))
        return 0.0

    budget = float(d.get("read_minutes") or 120) * 60
    done = float(mine.get(read_key) or 0)
    left = budget - done
    if left < 60:
        log("  [%s] 今日阅读预算已满（%.1f/%.0f 分钟），心跳跳过"
            % (name, done / 60.0, budget / 60.0))
        return 0.0

    seconds = float(min(float(seconds), left))
    if seconds < 60:
        log("  [%s] 本次可用时长不足 1 分钟，心跳跳过" % name)
        return 0.0

    log("  [%s] 心跳启动：本次最多 %.1f 分钟（今日 %.1f/%.0f 分钟）"
        % (name, seconds / 60.0, done / 60.0, budget / 60.0))
    start = last_flush = time.time()
    topic = None
    topic_started = topic_dwell = 0.0
    cursor, recent = 1, []
    got = 0.0
    n_ok = n_bad = 0
    last_log = 0.0

    while True:
        now = time.time()
        ran = now - start
        if ran >= seconds:
            break

        if _in_window(time.localtime().tm_hour, quiet):
            nap = max(1.0, min(600.0, seconds - ran))
            log("  [%s] 夜间安静时段 %d:00-%d:00，暂停 %.0f 分钟"
                % (name, quiet[0], quiet[1], nap / 60.0))
            time.sleep(nap)
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
        for _ in range(random.choice([1, 1, 1, 2, 2, 3])):
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
            got += elapsed
            mine[read_key] = round(done + got, 1)
            save_state(st_all)
        else:
            n_bad += 1
            log("  [%s] timings HTTP %s，退避 30s" % (name, st))
            if st in (0, 403, 429):
                time.sleep(30)

        if now - last_log > 600:
            last_log = now
            pg = progress(e, proxy)
            log("  [%s] 已挂 %.1f 分钟｜上报 %d 次（失败 %d）｜记账 %.1f 分钟%s"
                % (name, ran / 60.0, n_ok, n_bad, mine[read_key] / 60.0,
                   ("｜站内实时：" + pg) if pg else ""))

    save_state(st_all)
    log("  [%s] 心跳结束：本次 +%.1f 分钟，今日累计 %.1f 分钟"
        % (name, got / 60.0, mine.get(read_key, 0) / 60.0))
    return got
