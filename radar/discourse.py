# -*- coding: utf-8 -*-
"""上游站点 站点每日任务：例行同步 + 累计在线时长，带风控节流与等级自动停机。

真实契约（来自 source-a 的 discourse-checkin 插件 JS 与实测）：
  例行同步  POST /checkin      头 X-上游站点-Checkin: true + X-Checkin-Nonce: <nonce>
                         体 nonce=<同一个 nonce> & timestamp=<毫秒>
                         另需 上游站点 的 X-CSRF-Token（GET /session/csrf 取）
  阅读  上游站点 自己统计 time_read：浏览器读帖就自然上报；也可直接 POST /topics/timings
  等级  /session/current.json 的 current_user.trust_level（source-a 映射：
        TL0 青铜会员 / TL1 白银会员 / TL2 黄金 / TL3 钻石 / TL4 王者）
"""
import json, os, random, re, string, subprocess, time, urllib.error, urllib.parse, urllib.request, ssl

from radar.util import UA, DATA_DIR, WS, http_get, notify

LEVEL_NAMES = {0: "青铜会员", 1: "白银会员", 2: "黄金会员", 3: "钻石会员", 4: "王者会员"}
ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
        "--disable-blink-features=AutomationControlled", "--no-first-run",
        "--disable-features=Translate,BackForwardCache"]
_CTX = ssl.create_default_context(); _CTX.check_hostname = False; _CTX.verify_mode = ssl.CERT_NONE


# ---------- 基础 ----------
def _state_path():
    return os.path.join(DATA_DIR, "missions.json")


def load_state():
    try:
        with open(_state_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(d):
    os.makedirs(os.path.dirname(_state_path()), exist_ok=True)
    with open(_state_path(), "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def _cookie(e):
    cf = e.get("cookie_file")
    if cf:
        p = cf if os.path.isabs(cf) else os.path.join(WS, cf)
        try:
            return open(p, encoding="utf-8").read().strip()
        except Exception:
            return ""
    return (e.get("cookie") or "").strip()


def _base(e):
    return (e.get("base") or e.get("url") or "").rstrip("/")


def _req(e, path, data=None, headers=None, proxy=None, method=None, timeout=25):
    """返回 (status, body)。proxy='direct'/空 用 urllib，其余走 curl。"""
    base = _base(e)
    url = path if path.startswith("http") else base + path
    ck = _cookie(e)
    h = {"User-Agent": UA, "Accept": "application/json, text/javascript, */*; q=0.01",
         "Accept-Language": "zh-CN,zh;q=0.9", "X-Requested-With": "XMLHttpRequest",
         "Referer": base + "/"}
    if ck:
        h["Cookie"] = ck
    if headers:
        h.update(headers)
    body = None
    if data is not None:
        if isinstance(data, (dict, list)):
            data = urllib.parse.urlencode(data, doseq=True)
        body = data.encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"

    if proxy and proxy not in ("direct", "none", "-"):
        cmd = ["curl", "-sS", "-m", str(timeout), "-x", proxy, "-X", (method or ("POST" if body else "GET")),
               "-w", "\n__CODE__%{http_code}"]
        for k, v in h.items():
            cmd += ["-H", f"{k}: {v}"]
        if body:
            cmd += ["--data-binary", data]
        cmd.append(url)
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10).stdout
            m = re.search(r"__CODE__(\d+)$", out)
            return (int(m.group(1)) if m else 0), out[:m.start()] if m else out
        except Exception as ex:
            return 0, f"curl error: {ex}"
    r = urllib.request.Request(url, data=body, headers=h, method=method or ("POST" if body else "GET"))
    try:
        with urllib.request.urlopen(r, timeout=timeout, context=_CTX) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as ex:
        return ex.code, ex.read().decode("utf-8", "replace")
    except Exception as ex:
        return 0, f"{type(ex).__name__}: {ex}"


def _j(body):
    try:
        return json.loads(body)
    except Exception:
        return {}


def csrf(e, proxy=None):
    st, b = _req(e, "/session/csrf", proxy=proxy)
    return _j(b).get("csrf", "")


def level_info(e, proxy=None):
    """登录态 + 等级。返回 dict(ok, username, user_id, trust_level, level_name, points)"""
    st, b = _req(e, "/session/current.json", proxy=proxy)
    d = _j(b)
    u = d.get("current_user") or {}
    if not u:
        return {"ok": False, "status": st, "raw": b[:200]}
    tl = u.get("trust_level")
    return {"ok": True, "status": st, "username": u.get("username"), "user_id": u.get("id"),
            "trust_level": tl, "level_name": LEVEL_NAMES.get(tl if isinstance(tl, int) else -1, str(tl)),
            "points": u.get("points"), "gamification_score": u.get("gamification_score")}


def stats(e, proxy=None, username=None):
    """站内真实统计。走 /u/<username>.json —— 注意这里读到的 time_read 是 1 小时缓存值，
    想实时看进度请用 heartbeat.progress()（走 /u/<name>/upgrade-progress.json）。

    更正（2026-10-13 实测）：上游站点 的 time_read 由 UserStat#update_time_read! 累加，
    条件是「相邻两次请求的真实时间差 0 < diff < 100 秒」；而 POST /topics/timings
    **会累计新 last_seen**，所以每 55~75 秒发一次 timings 就能 1:1 养时长，不需要浏览器。
    见 radar/heartbeat.py 的模块说明。"""
    un = username or e.get("username")
    if not un:
        un = level_info(e, proxy).get("username")
    if not un:
        return {}
    st, b = _req(e, f"/u/{un}.json", proxy=proxy)
    u = _j(b).get("user") or {}
    return {"ok": bool(u), "time_read": u.get("time_read"), "recent_time_read": u.get("recent_time_read"),
            "trust_level": u.get("trust_level"), "last_seen": u.get("last_seen_age")}


# ---------- 例行同步 ----------
def checkin_once(e, proxy=None, log=print):
    cs = csrf(e, proxy)
    if not cs:
        return "error", "拿不到 CSRF（cookie 可能已失效）"
    nonce = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(26))
    st, b = _req(e, "/checkin", data={"nonce": nonce, "timestamp": int(time.time() * 1000)},
                 headers={"X-CSRF-Token": cs, "X-Discourse-Checkin": "true", "X-Checkin-Nonce": nonce},
                 proxy=proxy)
    d = _j(b)
    msg = d.get("message") or b[:120]
    if d.get("success"):
        return "ok", f"例行任务成功{(' +' + str(d.get('points'))) if d.get('points') else ''}"
    if "已经签到" in msg or "already" in msg.lower():
        return "ok", f"今天已完成（{msg}）"
    if st == 403 or st == 429:
        return "blocked", f"被拦 http {st} {msg}"
    return "unknown", f"http {st} {msg}"


# ---------- 在线时长 ----------
def recent_topics(e, proxy=None, n=6, min_posts=2):
    """挑帖子。n 可能大于单页 30 条，所以按页翻，最多翻 6 页。"""
    out, page = [], 1
    while len(out) < n and page <= 6:
        st, b = _req(e, f"/latest.json?page={page}", proxy=proxy)
        tops = (_j(b).get("topic_list", {}) or {}).get("topics", []) or []
        if not tops:
            break
        for t in tops:
            if (t.get("posts_count") or 0) >= min_posts and not t.get("pinned"):
                out.append({"id": t["id"], "title": (t.get("title") or "")[:60],
                            "posts": t.get("posts_count"), "views": t.get("views")})
        page += 1
    random.shuffle(out)
    return out[:n]


def read_api(e, top, dwell_s, proxy=None, log=print):
    """纯接口方式：先拿帖子，再按楼层上报停留毫秒。

    这条就是 heartbeat 用的路子 —— /topics/timings 会累计新 last_seen，而 time_read
    按「相邻两次请求的真实时间差」累加（0 < diff < 100 才计），所以它**确实能养时长**，
    完全不需要浏览器。

    区别在于节奏：这里是一帖一次性把 dwell_s 秒全报上去（适合偶尔补一刀）；
    要长时间保活必须连发、间隔 55~75 秒，才 1:1 —— 用
    `python3 -m radar heartbeat --seconds N`。"""
    st, b = _req(e, f"/t/{top['id']}.json", proxy=proxy)
    d = _j(b)
    posts = (d.get("post_stream", {}) or {}).get("posts") or []
    if not posts:
        return 0, f"帖子 {top['id']} 拿不到楼层 (http {st})"
    cs = csrf(e, proxy)
    total_ms = int(dwell_s * 1000)
    per = max(400, total_ms // max(1, len(posts)))
    data = {"topic_id": top["id"], "topic_time": total_ms}
    for p in posts:
        data[f"timings[{p['post_number']}]"] = per
    st, b = _req(e, "/topics/timings", data=data, headers={"X-CSRF-Token": cs}, proxy=proxy)
    return (total_ms / 1000.0 if st == 200 else 0), f"http {st} 上报 {len(posts)} 层 / {dwell_s:.0f}s"


def read_browser(e, tops, cfg_daily, proxy=None, log=print, max_seconds=None):
    """浏览器方式：真开帖、真滚动、真停留。上游站点 自己上报 timings。
    max_seconds：本次最多读多少秒（= 当日剩余预算），到点就停，避免超预算把
    GitHub Actions 的 timeout 撑爆。"""
    from playwright.sync_api import sync_playwright
    base = _base(e)
    ck = _cookie(e)
    cookies = []
    for kv in ck.split(";"):
        if "=" in kv:
            k, v = kv.strip().split("=", 1)
            cookies.append({"name": k, "value": v,
                            "domain": urllib.parse.urlparse(base).hostname, "path": "/"})
    dwell = cfg_daily.get("dwell_s") or [40, 150]
    gap = cfg_daily.get("gap_s") or [20, 90]
    got = 0.0        # 累计「保活」秒数：开帖 + 停留 + 间隙都算在线时间
    n_read = 0       # 实际读完的帖数（可能少于传入的 tops）
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=not cfg_daily.get("headful"), args=ARGS)
        ctx = br.new_context(user_agent=UA, locale="zh-CN", viewport={"width": 1366, "height": 768},
                             timezone_id="Asia/Shanghai")
        try:
            ctx.add_cookies(cookies)
        except Exception as ex:
            log(f"    cookie 注入失败：{ex}")
        page = ctx.new_page()
        for i, t in enumerate(tops):
            if max_seconds is not None and got >= max_seconds:
                log(f"    预算已满（{got/60:.1f} 分钟），停止本轮阅读")
                break
            t0 = time.time()
            try:
                page.goto(f"{base}/t/{t['id']}", wait_until="domcontentloaded", timeout=60000)
            except Exception as ex:
                log(f"    打不开 {t['id']}: {type(ex).__name__}")
                got += time.time() - t0
                continue
            want = random.uniform(*dwell)
            if max_seconds is not None:
                # 把开帖耗时也算进去，别让单帖把预算顶穿
                room = max_seconds - got - (time.time() - t0)
                want = min(want, max(0.0, room))
                if want < 5:
                    got += time.time() - t0          # 这一帖开帖花的时间也算在线
                    log(f"    预算只剩 {max(0.0, room):.0f}s，停止本轮阅读")
                    break
            spent = 0.0
            while spent < want:
                page.mouse.wheel(0, random.randint(280, 900))
                step = random.uniform(1.4, 4.2)
                if max_seconds is not None:
                    step = min(step, max(0.1, want - spent))   # 别让一步跨过预算
                page.wait_for_timeout(int(step * 1000))
                spent += step
            got += time.time() - t0
            n_read = i + 1
            log(f"    读完《{t['title'][:24]}》 {spent:.0f}s（累计保活 {got/60:.1f} 分钟）")
            if i < len(tops) - 1:
                if max_seconds is not None and got >= max_seconds:
                    break
                g = random.uniform(*gap)
                if max_seconds is not None:
                    g = min(g, max(0.0, max_seconds - got))
                time.sleep(g)
                got += g
        ctx.close(); br.close()
    return got, f"浏览器读了 {n_read} 帖 / {got/60:.1f} 分钟"


# ---------- 每日任务总入口 ----------
def run_daily(e, cfg, log=print, force=False):
    """返回 (status, detail)。status: ok / skipped / blocked / error / stopped"""
    name = e.get("name") or _base(e)
    d = e.get("daily") or {}
    proxy = e.get("proxy") or "direct"
    st_all = load_state()
    mine = st_all.setdefault(name, {})
    today = time.strftime("%Y-%m-%d")

    # 停机语义（2026-10-15 改）：
    #   stop_at.trust_level 到等级后只停「阅读保活」，**每日签到照做**。理由：签到是
    #   白银档每天 5 能量（青铜 1），几乎零成本、不刷时长、不碰保活那套风控节奏 ——
    #   跟着一起停掉等于每天白丢 5 个。想回到老行为（连签到一起停）就把
    #   stop_at.keep_checkin 显式设成 false。
    stop_cfg = e.get("stop_at") or {}
    stop_tl = stop_cfg.get("trust_level")
    keep_ck = bool(stop_cfg.get("keep_checkin", True))

    # 停机标记（等级已达 / 被拦）—— keep_checkin 时不拦整段，只拦保活
    if mine.get("stopped") and not force and not keep_ck:
        return "stopped", f"已停机：{mine.get('reason')}（{mine.get('stopped_at','')[:10]}）"

    # 风控：活跃时段
    hrs = d.get("active_hours") or [8, 23]
    if not force and not (hrs[0] <= time.localtime().tm_hour < hrs[1]):
        return "skipped", f"不在活跃时段 {hrs[0]}:00-{hrs[1]}:00"

    # 风控：每周休息日
    rest = d.get("weekly_rest_days") or []
    if not force and time.localtime().tm_wday in rest:
        return "skipped", f"今天是每周休息日（周{time.localtime().tm_wday}）"

    # 0) 先看等级（也是登录态体检）
    info = level_info(e, proxy)
    if not info.get("ok"):
        return "error", f"登录态失效（http {info.get('status')}）{info.get('raw','')[:80]}"
    tl = info.get("trust_level")
    log(f"  [{name}] {info['username']} 当前 {info['level_name']}（TL{tl}，积分 {info.get('points')}）")

    # 已达目标等级 → 停保活（首次停机推一次通知）；签到是否继续由 keep_checkin 决定。
    # 这里**不能 return**：一 return 就走不到下面的签到步骤，达标当天起签到就永远不做了。
    reached = stop_tl is not None and isinstance(tl, int) and tl >= stop_tl
    if reached and not mine.get("stopped"):
        mine.update({"stopped": True, "reason": f"等级已达 {info['level_name']}（TL{tl}）",
                     "stopped_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        save_state(st_all)
        if stop_cfg.get("notify", True):
            notify(f"{name} 保活任务自动停机",
                   f"已升到 {info['level_name']}（TL{tl}），阅读保活已停；"
                   + ("每日签到继续。" if keep_ck else "例行任务与保活任务已停。"))

    before = (stats(e, proxy, info.get("username")) or {}).get("time_read")
    spent_list = []
    if before is not None:
        spent_list.append(f"站内在线时长 {before}s")
    # 1) 例行同步
    if d.get("checkin", True):
        day_key = f"checkin:{today}"
        if mine.get(day_key) == "ok" and not force:
            spent_list.append("例行任务 今天已做")
        else:
            stt, det = checkin_once(e, proxy, log)
            log(f"  [{name}] 例行任务 → {stt}｜{det}")
            mine[day_key] = "ok" if stt == "ok" else stt
            spent_list.append(f"例行任务 {stt}")
            if stt == "blocked":
                mine.update({"blocked_at": time.strftime("%Y-%m-%d %H:%M:%S"), "block_reason": det})
                save_state(st_all)
                notify(f"{name} 例行任务被拦", det + "｜今天不再重试")
                return "blocked", det

    # 2) 在线时长（每天总量封顶）—— 这就是「保活」，达标就整段跳过
    if reached:
        spent_list.append("保活已停机（等级已达，不再刷时长）")
    elif d.get("read", True):
        budget = float(d.get("read_minutes") or 20) * 60
        read_key = f"read:{today}"
        done = float(mine.get(read_key) or 0)
        left = budget - done
        if left < 60:
            spent_list.append(f"阅读 今日已完成 {done/60:.1f} 分钟")
        else:
            tops = recent_topics(e, proxy, n=int(d.get("topics_per_run") or 3))
            if not tops:
                spent_list.append("阅读 没挑到帖子")
            else:
                mode = d.get("read_mode") or "browser"
                # 每帖预计阅读秒数 = dwell 区间均值（got 只累计阅读时间，不含 gap）
                _dw = d.get("dwell_s") or [40, 150]
                _per = max(20.0, sum(float(x) for x in _dw) / max(1, len(_dw)))
                take = min(len(tops), max(1, int((left + _per - 1) // _per)))
                if mode == "api":
                    add = 0.0
                    for t in tops[:take]:
                        sec = min(random.uniform(*(d.get("dwell_s") or [40, 150])), left - add)
                        if sec <= 5:
                            break
                        s2, det = read_api(e, t, sec, proxy, log)
                        log(f"  [{name}] 阅读(api) {det}")
                        add += s2
                        time.sleep(random.uniform(*(d.get("gap_s") or [20, 90])))
                    got, note = add, f"接口上报 {add/60:.1f} 分钟"
                else:
                    got, note = read_browser(e, tops[:take], d, proxy, log,
                                             max_seconds=left)
                mine[read_key] = round(done + got, 1)
                spent_list.append(f"阅读 {note}（今日累计 {(done+got)/60:.1f} 分钟）")

    after = (stats(e, proxy, info.get("username")) or {}).get("time_read")
    if after is not None and before is not None and after != before:
        spent_list.append(f"站内在线时长 {before}→{after}s（+{(after-before)/60:.1f} 分钟）")
    save_state(st_all)
    return "ok", "｜".join(spent_list)
