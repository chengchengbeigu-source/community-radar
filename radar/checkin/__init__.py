# -*- coding: utf-8 -*-
"""登录态例行同步执行器：配置驱动，每天每站一次，带随机抖动与日志。

两类站点：
  kind: simple（默认）—— 直接拿 cookie 打一个 URL，正则判断结果；
  kind: newapi          —— New API 面板（如「panel-a」），POST /api/user/checkin；
                           鉴权优先用「访问令牌」，没令牌就用账号密码登录换 session。
"""
import os, re, time, random, json, subprocess, urllib.parse, urllib.request
from radar import store
from radar.util import http_get, notify, today_str, WS, DATA_DIR, get_proxy, UA

NEWAPI_KINDS = ("newapi", "new-api", "new_api")


def _squash(t, n=200):
    return re.sub(r"\s+", " ", str(t or "")).strip()[:n]


def _cookie(entry):
    cf = entry.get("cookie_file")
    if cf:
        p = cf if os.path.isabs(cf) else os.path.join(WS, cf)
        with open(p, encoding="utf-8") as f:
            return f.read().strip()
    return entry.get("cookie", "")


def _strip_base(url):
    """把粘来的页面路径削成站点根：https://api.pie-xian.com/profile → https://api.pie-xian.com"""
    base = (url or "").strip().rstrip("/")
    for suffix in ("/profile", "/console", "/token", "/api/user/checkin",
                   "/api/user/self", "/api/status", "/api"):
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


def _safe_name(s):
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", str(s or "site"))[:40]


def _json_req(url, payload, headers, proxy, timeout=30, method="POST", jar=None):
    """发 JSON 请求（curl），返回 (http_code, 响应体)。
    带 jar 时会写 cookie jar —— 登录换 session 就靠它，比解析响应头稳。
    用 -w 打标记拿状态码，不用 -i，免得代理的 CONNECT 头混进正文。"""
    cmd = ["curl", "-sS", "-m", str(int(timeout)), "-A", UA, "-X", method,
           "-H", "Content-Type: application/json", "-H", "Accept: application/json"]
    if jar:
        cmd += ["-c", jar, "-b", jar]
    if payload is not None:
        cmd += ["--data-raw", json.dumps(payload, ensure_ascii=False)]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    if proxy:
        cmd += ["-x", proxy]
    cmd += ["-w", "\n__HTTP_CODE__%{http_code}"]
    cmd.append(url)
    pr = subprocess.run(cmd, capture_output=True, text=True, timeout=int(timeout) + 15)
    raw = pr.stdout or ""
    code = 0
    m = re.search(r"__HTTP_CODE__(\d{3})\s*$", raw)
    if m:
        code = int(m.group(1))
        raw = raw[: m.start()]
    if not raw.strip() and pr.stderr:
        return code, "[curl] " + _squash(pr.stderr, 200)
    return code, raw.strip()


def _jar_cookie(jar):
    """从 curl 的 cookie jar 里读出 cookie 串。"""
    if not jar or not os.path.exists(jar):
        return ""
    out = []
    for ln in open(jar, encoding="utf-8", errors="replace"):
        if ln.startswith("#") or not ln.strip():
            continue
        f = ln.rstrip("\n").split("\t")
        if len(f) >= 7:
            out.append(f"{f[5]}={f[6]}")
    return "; ".join(out)


def _newapi_login(base, user, pwd, proxy):
    code, head, body = _json_req(base + "/api/user/login",
                                 {"username": user, "password": pwd}, {}, proxy)
    m = re.search(r"(?im)^set-cookie:\s*([^;\r\n]+)", head)
    return code, (m.group(1).strip() if m else ""), body


def _judge_newapi(code, body):
    """把 New API 的 JSON 翻成人话。"""
    try:
        d = json.loads(body)
    except Exception:
        d = {}
    msg = str(d.get("message") or "").strip()
    if d.get("success"):
        extra = ""
        data = d.get("data")
        if isinstance(data, dict):
            q = data.get("quota") or data.get("amount") or 0
            try:
                if q:
                    extra = f"＋{int(q) / 500000:.2f} 额度"
            except Exception:
                pass
        return "ok", _squash(f"例行任务成功 {extra}｜{msg}" if msg else f"例行任务成功 {extra}")
    if re.search(r"已签到|已经签到|重复签到|今日已|already", msg):
        return "ok", _squash("今日已签到｜" + msg, 180)
    return "unknown", (_squash(msg, 200) or f"HTTP {code}｜" + _squash(body, 160))


def _do_newapi(cfg, e, log=print):
    """New API 站点：鉴权 = Bearer 令牌 + New-Api-User（用户 ID）两个头。
    没令牌/没 ID 时，用账号密码登录，从登录响应里把两者都抠出来。"""
    base = _strip_base(e.get("url"))
    px = get_proxy(cfg, override=e.get("proxy"))
    jar = os.path.join(DATA_DIR, "jar_" + _safe_name(e.get("name") or base) + ".txt")
    token = (e.get("access_token") or e.get("token") or "").strip()
    uid = str(e.get("user_id") or e.get("new_api_user") or "").strip()
    cookie = _cookie(e)
    if not token and not cookie and e.get("username"):
        code, body = _json_req(base + "/api/user/login",
                               {"username": e.get("username"), "password": e.get("password", "")},
                               {}, px, jar=jar)
        try:
            d = json.loads(body).get("data") or {}
            uid = uid or str(d.get("id") or "")
            token = token or (d.get("access_token") or "")
        except Exception:
            pass
        cookie = _jar_cookie(jar)
        if not token and not cookie:
            return "error", f"登录失败（HTTP {code}）｜" + _squash(body, 160)
        log(f"[checkin] {base} 账号登录成功（id={uid or '?'}，session={'有' if cookie else '无'}）")
    if not token and not cookie:
        return "error", "没填访问令牌，也没给账号密码 —— 签不了"
    hdr = {}
    if token:
        hdr["Authorization"] = "Bearer " + token
    if uid:
        hdr["New-Api-User"] = uid
    code, body = _json_req(base + "/api/user/checkin", {}, hdr, px, jar=jar)
    status, detail = _judge_newapi(code, body)
    if "未提供 New-Api-User" in detail:
        status = "error"
        detail = "缺 New-Api-User 头（用户 ID）。补上「用户 ID」，或者填账号密码让我自己登上去取。"
    elif status != "ok" and re.search(r"token 无效|未登录|无权|无权限|New-Api-User", detail):
        status = "error"
        status = "error"
    return status, detail


def _is_newapi(e):
    kind = str(e.get("kind") or e.get("type") or "").strip().lower()
    return kind in NEWAPI_KINDS or bool(e.get("access_token")) or bool(e.get("token"))


def run(cfg, log=print, no_sleep=False, only=None, force=False):
    entries = [e for e in (cfg.get("checkins") or []) if e.get("enabled")]
    if not entries:
        log("[checkin] 配置里没有启用的站点（radar/config.yaml → checkins）")
        return []
    c = store.connect()
    day, out = today_str(), []
    for e in entries:
        name = e.get("name") or e.get("url", "?")
        if only and only != name:
            continue
        done = store.checked_today(c, name, day)
        if done and done[0] == "ok" and not force:
            log(f"[checkin] {name}：今天已完成（{_squash(done[1], 60)}），跳过")
            continue
        jitter = float(e.get("jitter_minutes", 0) or 0)
        if jitter and not no_sleep:
            s = random.uniform(0, jitter * 60)
            log(f"[checkin] {name}：随机延迟 {s:.0f}s 后执行")
            time.sleep(s)
        if (e.get("kind") or "").lower() in ("discourse", "nodeloc", "forum"):
            from radar import discourse
            try:
                status, detail = discourse.run_daily(e, cfg, log=log, force=force)
            except Exception as ex:
                status, detail = "error", f"{type(ex).__name__}: {ex}"
        elif _is_newapi(e):
            try:
                status, detail = _do_newapi(cfg, e, log)
            except Exception as ex:
                status, detail = "error", f"{type(ex).__name__}: {ex}"
        else:
            ck = _cookie(e)
            px = get_proxy(cfg, override=e.get("proxy"))
            headers = dict(e.get("extra_headers") or {})
            if ck:
                headers["Cookie"] = ck
            data = None
            if (e.get("method", "GET").upper() == "POST"):
                body = e.get("data") or {}
                data = urllib.parse.urlencode(body).encode()
                headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
            try:
                body_txt = http_get(e["url"], headers=headers, timeout=30, data=data,
                                    method=e.get("method", "GET"), proxy=px) or ""
            except Exception as ex:
                status, detail = "error", f"{type(ex).__name__}: {ex}"
            else:
                ok_pat = e.get("success_regex", "签到成功|已签到|success|ok")
                al_pat = e.get("already_regex", "今日已签到|已经签到|already")
                if re.search(ok_pat, body_txt, re.I):
                    status, detail = "ok", _squash(body_txt)
                elif al_pat and re.search(al_pat, body_txt, re.I):
                    status, detail = "ok", "今日已完成｜" + _squash(body_txt, 160)
                else:
                    status, detail = "unknown", _squash(body_txt)
        store.log_checkin(c, name, day, status, detail)
        log(f"[checkin] {name}：{status}｜{detail[:120]}")
        out.append({"site": name, "status": status, "detail": detail})
        # 注意：这里**不再**自己推通知。成功/失败都由调用方（radar/scheduler.py 的
        # do_checkin）汇总成一条推送通知 —— 否则一次例行同步失败会推两条，成功则一条都没有。
    return out
