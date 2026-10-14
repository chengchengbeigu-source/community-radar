# -*- coding: utf-8 -*-
"""单轮常驻内的定时调度器 —— 「几点做什么」不交给 GitHub cron，交给这里。

为什么要这个：GitHub 的 schedule 最小粒度粗，而且**实测不可靠**（我们观测到过一次
延迟 8 小时 48 分）。把「每晚 20:00 汇报」挂到 cron 上等于赌博。所以做法反过来：
cron 只负责把一轮拉起来，一轮待满 N 分钟，**轮内自己看表**办事。

任务表（全部在 config.yaml → schedule 下改，都有默认值）：
  collect_minutes        每这么久采集一次；有新条目就立刻推送通知
  progress_minutes       每这么久推一条在线时长进度
  checkin_hour           每天这个整点后的第一次循环做例行同步，并推结果
  digest_hour            每天这个整点后的第一次循环推当日总结（默认 20）
  hb_slice_seconds       心跳切片长度：每跑这么久回来看一次任务表
  config_reload_minutes  每这么久从 origin/main 拉一次 radar/config.yaml
                         —— 手机上改完配置，最快这么多分钟就在云端生效，不用等下一轮

「今天已经做过」记在 radar/data/scheduler.json，所以进程重启/新一轮起来都不会重复推。
"""
import datetime, json, os, subprocess, time

from radar import collect, report, store
from radar.util import DATA_DIR, WS, load_config, notify, today_str

DEFAULTS = {
    "collect_minutes": 20,
    "progress_minutes": 60,
    "checkin_hour": 8,
    "digest_hour": 20,
    "hb_slice_seconds": 600,
    "config_reload_minutes": 30,
}

STATE_PATH = os.path.join(DATA_DIR, "scheduler.json")


# ────────────────────────── 状态持久化 ──────────────────────────

def _state_load():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _state_save(d):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except OSError:
        pass


# ────────────────────────── 四件具体的事 ──────────────────────────

def do_checkin(cfg, log=print, force=False, no_sleep=True, only=None):
    """例行同步 + 把结果推送通知（**成功也推** —— 用户要的就是「今天例行同步了」这条）。"""
    from radar.checkin import run as ckrun
    try:
        res = ckrun(cfg, log=log, no_sleep=no_sleep, only=only, force=force) or []
    except Exception as e:
        log("[sched] 例行任务异常：%s: %s" % (type(e).__name__, e))
        collect.push(cfg, "例行同步异常 " + today_str(), "%s: %s" % (type(e).__name__, e), log=log)
        return None          # 返回 None＝「没跑成」，让主循环下一轮重试（别整天漏掉例行同步）
    if not res:
        log("[sched] 例行任务：今天都做过了，不重复推")
        return []
    day = today_str()
    bad = [r for r in res if r["status"] not in ("ok", "skipped", "stopped")]
    head = "⚠️ 例行任务有问题" if bad else "✅ 例行任务完成"
    lines = ["%s（%s）" % (head, day)]
    for r in res:
        mark = "✅" if r["status"] in ("ok", "skipped") else ("⏸" if r["status"] == "stopped" else "❌")
        lines.append("%s %s：%s｜%s" % (mark, r["site"], r["status"], (r["detail"] or "")[:140]))
    collect.push(cfg, "例行任务 " + day, "\n".join(lines), log=log)
    return res


def _prog_note(e, name, pg, st_all, today):
    """给进度文案补上人最关心的三件事：离目标还差多少、比自己上次涨了多少、今日额度还剩多少。

    为什么：以前只推一句「阅读时长（分钟）：552/600」。一旦心跳因额度停摆，数字就冻住，
    每小时收到的都是同一条一模一样的消息 —— 收件人根本分不清「在涨」还是「卡死了」，
    只会得出「不再更新汇报」的结论（2026-10-14 用户报的就是这个）。把原因写进消息里，
    下次一眼就能看出到底是在跑还是被额度挡住了。
    """
    from radar.heartbeat import parse_progress
    mine = (st_all or {}).get(name) or {}
    bits = []
    pv, pt = (None, None)
    try:
        pv, pt = parse_progress(pg)
    except Exception:
        pass
    if pv is not None and pt is not None:
        bits.append("距目标还差 %.0f 分钟" % (pt - pv) if pt > pv else "已达标")
        prev = mine.get("prog:%s" % today)
        try:
            if prev is not None:
                dv = pv - float(prev)
                bits.append("较上次 %s%.0f 分钟" % ("+" if dv >= 0 else "", dv))
        except (TypeError, ValueError):
            pass
        mine["prog:%s" % today] = pv
    d = e.get("daily") or {}
    budget = float(d.get("read_minutes") or 120)
    done = float(mine.get("read:%s" % today) or 0) / 60.0
    if budget > 0:
        bits.append("今日保活 %.0f/%.0f 分钟" % (done, budget))
        if done >= budget - 1:
            bits.append("今日额度已用完，心跳停摆")
    return ("（" + "，".join(bits) + "）") if bits else ""


def do_progress(cfg, log=print, push_it=True):
    """取站内实时阅读进度（upgrade-progress.json，不是有缓存的 summary.json）并推送通知。

    取不到时顺手查一下登录态：失效就按「唯一需要人修的点」推 QQ 告警。
    """
    from radar.discourse import load_state, save_state
    from radar.heartbeat import auth_ok, parse_progress, progress, progress_level
    today = today_str()
    st_all = load_state()
    out = {}
    dead = []
    met = []          # 本次观测到「已达标」的站点，循环后再统一推 🎉（避免在循环里发通知）
    for e in cfg.get("checkins") or []:
        if not e.get("enabled", True):
            continue
        if (e.get("kind") or "").lower() not in ("discourse", "nodeloc", "forum"):
            continue
        name = e.get("name") or "?"
        try:
            pg = progress(e)
        except Exception as ex:
            pg = "取不到（%s）" % type(ex).__name__
        if not pg:
            try:
                if not auth_ok(e):
                    pg = "登录态失效，需要重新登录换 cookie"
                    dead.append(name)
            except Exception:
                pass
        try:
            note = _prog_note(e, name, pg, st_all, today)
        except Exception:
            note = ""
        out[name] = (pg or "取不到") + note
        log("  [%s] 站内实时：%s" % (name, out[name]))

        # 判定「本站目标已达成」：优先看等级（升级后再也读不到阅读时长条件了），
        # 读不到等级才退回看 pv/pt。这样无论站点什么时候把条件挪走都能认出来。
        _lv = _pv = _pt = None
        try:
            _lv = (progress_level(e) or (None,))[0]
        except Exception:
            _lv = None
        try:
            _pv, _pt = parse_progress(pg)
        except Exception:
            _pv, _pt = (None, None)
        _stop_tl = (e.get("stop_at") or {}).get("trust_level")
        _ach = None
        if _lv is not None and _stop_tl is not None and float(_lv) >= float(_stop_tl):
            _ach = "L%d" % int(float(_lv))
        elif _pv is not None and _pt is not None and _pt > 0 and _pv >= _pt:
            _ach = "T%d" % int(_pt)
        if _ach:
            met.append((name, out[name], _ach, _lv))
    try:
        save_state(st_all)
    except Exception:
        pass
    if push_it and met:
        # 「已解锁」只推一次，靠 scheduler.json 记账（跨轮次也不会重复轰炸）
        st_c = _state_load()
        for nm, body, ach, lv in met:
            key = "congrats:%s:%s" % (nm, ach)
            if st_c.get(key):
                continue
            lvl_name = ("白银会员（TL1）" if lv == 1 else ("TL%s" % lv)) if lv is not None else ""
            txt = ("%s 的目标已经达成 🎉\n%s\n%s"
                   "在线时长保活已按 stop_at 停机，不会再空转白耗。"
                   % (nm, body, ("当前等级：" + lvl_name + "\n") if lvl_name else ""))
            collect.push(cfg, "🎉 %s 已解锁" % nm, txt, log=log)
            st_c[key] = int(time.time())
            _state_save(st_c)
            log("[sched] 已推「%s 已解锁」祝贺（%s）" % (nm, ach))
    if push_it and out:
        # 失效的站点不塞进这条例行进度里 —— 它另外会有一条明确的告警，别重复轰炸
        alive = {k: v for k, v in out.items() if k not in dead}
        if alive:
            body = "；".join("%s %s" % (k, v) for k, v in alive.items())
            collect.push(cfg, "在线进度 " + today_str(), body, log=log)
    if push_it and dead:
        st = _state_load()
        for nm in dead:
            _alert_auth_dead(cfg, nm, st, log=log)
    return out


def _alert_auth_dead(cfg, name, st, log=print):
    """登录态失效告警 —— 这是整套系统里唯一必须人来修的点，务必推到 QQ。

    6 小时内只提醒一次，免得每片心跳都轰炸你。
    """
    now = time.time()
    last = float(st.get("auth_alert_ts") or 0)
    if now - last < 6 * 3600:
        log("[sched] 登录态失效（%s），%.1f 小时前提醒过，暂不重复推"
            % (name, (now - last) / 3600.0))
        return False
    txt = ("%s 的登录态已失效：签到与在线时长保活都已停摆。\n"
           "这是整套系统唯一需要你动手的地方 —— 重新登录后把新的 cookie 换掉。\n"
           "发现时间：%s" % (name, datetime.datetime.now().strftime("%F %T")))
    collect.push(cfg, "⚠️ 登录态失效 " + today_str(), txt, log=log)
    st["auth_alert_ts"] = now
    st["auth_dead_name"] = name
    _state_save(st)
    return True


def do_digest(cfg, log=print, day=None):
    """当日总结（默认 20:00 那条）—— 外层只管兜异常：出岔子返回 None，主循环会重试。"""
    try:
        return _digest(cfg, log=log, day=day)
    except Exception as e:
        log("[sched] 日报异常：%s: %s" % (type(e).__name__, e))
        return None


def _digest(cfg, log=print, day=None):
    """今天新增多少、几星几条、例行同步状态、阅读进度。"""
    day = day or today_str()
    c = store.connect()
    try:
        rows = list(c.execute(
            "SELECT source, COUNT(*) n, SUM(stars>=5) s5, SUM(stars=4) s4, SUM(stars=3) s3 "
            "FROM posts WHERE date(first_seen)=? GROUP BY source", (day,)))
    except Exception as e:
        log("[sched] 日报查库失败：%s" % e)
        rows = []
    fresh_by_src = {r["source"]: r["n"] for r in rows}
    five = sum(int(r["s5"] or 0) for r in rows)
    four = sum(int(r["s4"] or 0) for r in rows)
    three = sum(int(r["s3"] or 0) for r in rows)

    prog = do_progress(cfg, log=log, push_it=False)
    prog_txt = "；".join("%s %s" % (k, v) for k, v in prog.items()) or None

    ck = []
    for e in cfg.get("checkins") or []:
        name = e.get("name") or "?"
        r = store.checked_today(c, name, day)
        if r:
            ck.append("%s %s" % (name, r[0]))

    dropped = 0
    try:
        dr = c.execute("SELECT SUM(fetched-fresh) d FROM runs WHERE date(ts)=?", (day,)).fetchone()
        dropped = int(dr["d"] or 0)
    except Exception:
        pass

    txt = report.digest(cfg, day, fresh_by_src, five, four, three, dropped, prog_txt, ck)
    log("[sched] 日报：\n" + txt)
    collect.push(cfg, "每日汇报 " + day, txt, log=log)
    return txt


def do_collect(cfg, log=print):
    """采集一轮 + 有新条目就推送通知。"""
    try:
        st = collect.run_collect(cfg, do_notify=True, log=log)
        log("[sched] 采集完成：新增 %s 条，过滤 %s 条，推送=%s"
            % (st.get("fresh"), st.get("dropped"), st.get("pushed")))
        return st
    except Exception as e:
        log("[sched] 采集异常：%s: %s" % (type(e).__name__, e))
        return {}


# ────────────────────────── 配置热重载 ──────────────────────────

def _reload_config(log=print):
    """从 origin/main 拉一份最新 radar/config.yaml —— 手机上改完不用等下一轮起来。"""
    if not (os.environ.get("NODELOC_COOKIE") or os.environ.get("XSG_TOKEN")):
        # 本地跑（没有 Secrets 环境变量）时**绝对不要碰**工作区里这份 config.yaml ——
        # 本机那份存着 user_id 之类的真值，checkout 一覆盖就没了。
        return None
    try:
        p = subprocess.run(["git", "-C", WS, "fetch", "--depth=1", "origin", "main"],
                           capture_output=True, text=True, timeout=90)
        if p.returncode != 0:
            log("[sched] git fetch 失败：%s" % ((p.stderr or p.stdout or "")[:160]))
            return None
        q = subprocess.run(["git", "-C", WS, "checkout", "-f", "FETCH_HEAD", "--", "radar/config.yaml"],
                           capture_output=True, text=True, timeout=60)
        if q.returncode != 0:
            log("[sched] git checkout config 失败：%s" % ((q.stderr or q.stdout or "")[:160]))
            return None
        # 关键：checkout 把 config.yaml 覆盖回仓库里的「裸版本」（access_token / user_id /
        # username 全是空串），必须紧接着再灌一次 Secrets，否则热重载会把panel-a的令牌和
        # source-a 的用户名抹掉。github_secrets.py 是幂等的，重复跑无害。
        s = subprocess.run(["python3", "radar/github_secrets.py"], cwd=WS,
                           capture_output=True, text=True, timeout=60)
        if s.returncode != 0:
            log("[sched] 热重载后重灌密钥失败：%s" % ((s.stderr or s.stdout or "")[:160]))
        cfg = load_config()
        log("[sched] 配置已从 origin/main 热重载"
            + ("" if s.returncode == 0 else "（但密钥没灌上，留意）"))
        return cfg
    except Exception as e:
        log("[sched] 配置热重载异常：%s: %s" % (type(e).__name__, e))
        return None


# ────────────────────────── 主循环 ──────────────────────────

def _safe(label, fn, log):
    """跑一件小事，出任何岔子都只记一行日志，绝不让异常掀掉整个常驻循环。"""
    try:
        return fn()
    except Exception as e:
        log("[sched] %s 异常：%s: %s" % (label, type(e).__name__, e))
        return None


def run(cfg, minutes, log=print):
    """在 minutes 分钟里按任务表循环：例行同步 / 采集 / 进度 / 日报 / 心跳。"""
    sch = dict(DEFAULTS)
    sch.update(cfg.get("schedule") or {})
    st = _state_load()

    start = time.time()
    deadline = start + float(minutes) * 60

    every_collect = float(sch["collect_minutes"]) * 60
    every_prog = float(sch["progress_minutes"]) * 60
    every_reload = float(sch["config_reload_minutes"]) * 60
    hb_slice = float(sch["hb_slice_seconds"])
    ck_hour = int(sch["checkin_hour"])
    dg_hour = int(sch["digest_hour"])

    next_collect = start                      # 起来的头一件事就是采一轮
    next_prog = start + 5 * 60                # 5 分钟后先报一次阅读进度，让你知道活着
    next_reload = start + every_reload
    auth_dead_until = 0.0                     # 登录态失效后的静默期（到点再探一次）

    log("[sched] 常驻开始：%.0f 分钟｜采集 %g 分钟一轮｜进度 %g 分钟一轮｜"
        "例行任务 %d 点｜日报 %d 点｜心跳切片 %.0fs｜配置热重载 %g 分钟"
        % (float(minutes), sch["collect_minutes"], sch["progress_minutes"],
           ck_hour, dg_hour, hb_slice, sch["config_reload_minutes"]))
    log("[sched] 截止：%s" % datetime.datetime.fromtimestamp(deadline).strftime("%F %T"))

    while True:
        now = time.time()
        left = deadline - now
        # ⚠️ 收工判断要**放在任务检查之后**：最后一片心跳可能正好跨过「20:00 推日报」
        # 那条线，如果先 break 就永远补不上。所以这里只在真的一点时间都不剩时才退出。
        if left <= 20:
            log("[sched] 本轮只剩 %.0fs，收工" % left)
            break

        day = today_str()
        hour = datetime.datetime.now().hour

        # 1) 配置热重载（放最前面：后面的任务用最新配置）
        if now >= next_reload:
            newcfg = _reload_config(log=log)
            if newcfg:
                cfg = newcfg
                sch.update(cfg.get("schedule") or {})
                every_collect = float(sch["collect_minutes"]) * 60
                every_prog = float(sch["progress_minutes"]) * 60
                every_reload = float(sch["config_reload_minutes"]) * 60
                hb_slice = float(sch["hb_slice_seconds"])
                ck_hour = int(sch["checkin_hour"])
                dg_hour = int(sch["digest_hour"])
            next_reload = now + every_reload

        # 2) 每天的例行同步（该点之后第一次循环执行；漏了也会补）
        #    状态只在「真跑过」之后落盘 —— 中途崩了下一轮重试，不会整天漏掉例行同步。
        if hour >= ck_hour and st.get("checkin_day") != day:
            log("[sched] → 例行任务（%s）" % day)
            if _safe("例行任务", lambda: do_checkin(cfg, log=log), log) is not None:
                st["checkin_day"] = day
                _state_save(st)

        # 3) 采集 + 即时推
        if now >= next_collect:
            log("[sched] → 采集与简报")
            _safe("采集", lambda: do_collect(cfg, log=log), log)
            next_collect = time.time() + every_collect

        # 4) 在线时长进度
        if now >= next_prog:
            log("[sched] → 阅读进度")
            _safe("进度", lambda: do_progress(cfg, log=log), log)
            next_prog = time.time() + every_prog

        # 5) 当日总结（默认 20:00）
        if hour >= dg_hour and st.get("digest_day") != day:
            log("[sched] → 当日总结（%s）" % day)
            if _safe("日报", lambda: do_digest(cfg, log=log), log) is not None:
                st["digest_day"] = day
                _state_save(st)

        # 6) 心跳一片：把剩下的时间用去累计在线时长
        left = deadline - time.time()
        if left <= 90:
            log("[sched] 正事办完只剩 %.0fs，不再起心跳片子" % left)
            break
        slice_s = min(hb_slice, left - 30)
        if slice_s < 30:
            break

        # 登录态已失效：别再刷心跳（刷也是 401/403），静默 30 分钟后重探一次
        if auth_dead_until > time.time():
            nap = min(300.0, max(5.0, auth_dead_until - time.time()))
            time.sleep(min(nap, max(5.0, deadline - time.time())))
            continue

        t0 = time.time()
        hb = _heartbeat_slice(cfg, slice_s, log)
        spent = time.time() - t0
        if isinstance(hb, tuple) and len(hb) == 2 and hb[0] == "auth":
            _alert_auth_dead(cfg, hb[1], st, log)
            auth_dead_until = time.time() + 1800
            continue
        if spent < 5:
            # 心跳一步没走（比如落在 quiet_hours / active_hours 外），别空转烧 CPU
            log("[sched] 心跳未推进（可能在静默时段），歇 120s")
            time.sleep(min(120.0, max(5.0, deadline - time.time())))

    log("[sched] 常驻结束")
    return 0


def _heartbeat_slice(cfg, seconds, log):
    """对每个 discourse 类例行同步项跑一段心跳。

    登录态失效时返回 ("auth", 站点名)，交给主循环去推 QQ 告警。
    """
    from radar.heartbeat import AuthExpired
    from radar.heartbeat import run as hbrun
    n = 0
    for e in cfg.get("checkins") or []:
        if not e.get("enabled", True):
            continue
        if (e.get("kind") or "").lower() not in ("discourse", "nodeloc", "forum"):
            continue
        try:
            hbrun(e, cfg, seconds, log=log)
            n += 1
        except AuthExpired:
            return ("auth", e.get("name"))
        except Exception as ex:
            log("[sched] 心跳异常（%s）：%s: %s" % (e.get("name"), type(ex).__name__, ex))
    if not n:
        log("[sched] 没有可心跳的 discourse 签到项")
    return n
