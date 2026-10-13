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


def do_progress(cfg, log=print, push_it=True):
    """取站内实时阅读进度（upgrade-progress.json，不是有缓存的 summary.json）并推送通知。"""
    from radar.heartbeat import progress
    out = {}
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
        out[name] = pg or "取不到"
        log("  [%s] 站内实时：%s" % (name, out[name]))
    if push_it and out:
        body = "；".join("%s %s" % (k, v) for k, v in out.items())
        collect.push(cfg, "在线进度 " + today_str(), body, log=log)
    return out


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
        t0 = time.time()
        _heartbeat_slice(cfg, slice_s, log)
        spent = time.time() - t0
        if spent < 5:
            # 心跳一步没走（比如落在 quiet_hours / active_hours 外），别空转烧 CPU
            log("[sched] 心跳未推进（可能在静默时段），歇 120s")
            time.sleep(min(120.0, max(5.0, deadline - time.time())))

    log("[sched] 常驻结束")
    return 0


def _heartbeat_slice(cfg, seconds, log):
    """对每个 discourse 类例行同步项跑一段心跳。"""
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
        except Exception as ex:
            log("[sched] 心跳异常（%s）：%s: %s" % (e.get("name"), type(ex).__name__, ex))
    if not n:
        log("[sched] 没有可心跳的 discourse 签到项")
    return n
