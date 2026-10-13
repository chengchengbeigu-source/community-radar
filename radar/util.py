# -*- coding: utf-8 -*-
"""radar 公共工具：路径、配置、HTTP、相对时间解析、手机通知。"""
import os, re, json, subprocess, datetime, urllib.request

RADAR_DIR = os.path.dirname(os.path.abspath(__file__))
WS        = os.path.dirname(RADAR_DIR)
DATA_DIR  = os.path.join(RADAR_DIR, "data")
OUTBOX    = os.path.join(RADAR_DIR, "outbox")
SECRETS   = os.path.join(RADAR_DIR, "secrets")
DB_PATH   = os.path.join(DATA_DIR, "radar.db")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

PROXY_FILE = os.path.join(RADAR_DIR, "proxy.txt")


def _norm_proxy(p):
    """把 'host:port' / 'user:pass@host:port' 补成带 scheme 的代理串；空/占位返回 None。"""
    p = (p or "").strip().strip('"').strip("'")
    if not p or "REPLACE" in p.upper():
        return None
    if "://" not in p:
        p = "http://" + p
    return p


def port_alive(port, host="127.0.0.1", timeout=0.6):
    import socket
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def get_proxy(cfg=None, override=None, allow_local=False):
    """代理优先级：环境变量 RADAR_PROXY/HTTPS_PROXY/ALL_PROXY > config 的 proxy > radar/proxy.txt。
    allow_local=True 且以上都没配时，本机 7890 在听就用它（手机 FlClash 的混合端口）。
    override：None/'auto' 走全局逻辑；'direct'/'none'/False 强制直连；其余当代理串。"""
    if override is not None:
        if override is False:
            return None
        o = str(override).strip()
        if o.lower() in ("", "direct", "none", "off", "no", "false"):
            return None
        if o.lower() not in ("auto", "global", "default"):
            return _norm_proxy(o)
    p = _norm_proxy(os.environ.get("RADAR_PROXY") or os.environ.get("HTTPS_PROXY")
                    or os.environ.get("ALL_PROXY"))
    if p:
        return p
    if isinstance(cfg, dict):
        v = cfg.get("proxy")
        if isinstance(v, dict):
            v = v.get("url")
        p = _norm_proxy(v)
        if p:
            return p
    try:
        if os.path.exists(PROXY_FILE):
            for ln in open(PROXY_FILE, encoding="utf-8"):
                if ln.strip() and not ln.strip().startswith("#"):
                    p = _norm_proxy(ln)
                    if p:
                        return p
    except Exception:
        pass
    if allow_local and port_alive(7890):
        return "http://127.0.0.1:7890"
    return None


def playwright_proxy(proxy):
    """代理串 → Playwright launch(proxy=...) 的结构（账号密码要单独拆出来）。"""
    if not proxy:
        return None
    from urllib.parse import urlparse, unquote
    pr = urlparse(proxy if "://" in proxy else "http://" + proxy)
    d = {"server": f"{pr.scheme}://{pr.hostname}:{pr.port or 8080}"}
    if pr.username:
        d["username"] = unquote(pr.username)
    if pr.password:
        d["password"] = unquote(pr.password)
    return d


def _curl_get(url, headers, timeout, data, method, proxy):
    cmd = ["curl", "-sS", "-L", "--compressed", "-m", str(int(timeout)),
           "-x", proxy, "-A", headers.get("User-Agent", UA)]
    for k, v in headers.items():
        if k.lower() != "user-agent":
            cmd += ["-H", f"{k}: {v}"]
    if data is not None:
        cmd += ["--data-raw", data if isinstance(data, str) else data.decode("utf-8", "replace")]
    if method:
        cmd += ["-X", method]
    cmd.append(url)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=int(timeout) + 15)
    if p.returncode != 0:
        raise RuntimeError(f"curl 退出码 {p.returncode}: {(p.stderr or '').strip()[:200]}")
    return p.stdout


def ensure_dirs():
    for d in (DATA_DIR, OUTBOX, SECRETS):
        os.makedirs(d, exist_ok=True)


def load_config(path=None):
    """读 config.yaml；若存在 radar/secrets/local.yaml（不进仓库）就按站点名叠加私密值。
    这样同一份配置既能进仓库（明文令牌留空），又能让本机继续用真凭据。"""
    import yaml
    p = path or os.path.join(RADAR_DIR, "config.yaml")
    with open(p, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    ov = os.path.join(SECRETS, "local.yaml")
    if os.path.exists(ov):
        try:
            with open(ov, encoding="utf-8") as f:
                o = yaml.safe_load(f) or {}
        except Exception:
            o = {}
        if isinstance(o, dict):
            by = {x.get("name"): x for x in (o.get("checkins") or []) if isinstance(x, dict)}
            for e in cfg.get("checkins") or []:
                src = by.get(e.get("name"))
                if src:
                    e.update({k: v for k, v in src.items() if v is not None and v != ""})
            for k, v in o.items():
                if k != "checkins":
                    cfg[k] = v
    return cfg


def http_get(url, headers=None, timeout=25, data=None, method=None, proxy=None):
    """GET/POST。proxy 为空走 urllib；给了代理就走 curl（http/socks5 都认，支持账号密码）。"""
    h = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if headers:
        h.update(headers)
    if proxy:
        return _curl_get(url, h, timeout, data, method, proxy)
    req = urllib.request.Request(url, headers=h, data=data, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


# ---------------- 相对/绝对时间 → 天 ----------------
_PATTERNS = [
    (r"(\d+(?:\.\d+)?)\s*(?:y|yr|yrs|year|years)\b", 365.0),
    (r"(\d+(?:\.\d+)?)\s*(?:mo|mon|month|months)\b", 30.0),
    (r"(\d+(?:\.\d+)?)\s*(?:w|wk|week|weeks)\b", 7.0),
    (r"(\d+(?:\.\d+)?)\s*(?:d|day|days)\b", 1.0),
    (r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\b", 1 / 24.0),
    (r"(\d+(?:\.\d+)?)\s*(?:min|mins|minute|minutes)\b", 1 / 1440.0),
    (r"(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds)\b", 1 / 86400.0),
    (r"(\d+(?:\.\d+)?)\s*年前", 365.0), (r"(\d+(?:\.\d+)?)\s*个月前", 30.0),
    (r"(\d+(?:\.\d+)?)\s*周前", 7.0),   (r"(\d+(?:\.\d+)?)\s*天前", 1.0),
    (r"(\d+(?:\.\d+)?)\s*小时前", 1 / 24.0), (r"(\d+(?:\.\d+)?)\s*分钟前", 1 / 1440.0),
    (r"(\d+(?:\.\d+)?)\s*秒前", 1 / 86400.0),
]


def parse_age_days(text, now=None):
    """把 '3h 15min ago' / '2d' / '3天前' / '2026-10-05' 解析成「距今多少天」。失败返回 None。"""
    if not text:
        return None
    now = now or datetime.datetime.now()
    t = str(text)
    if "刚刚" in t or "just now" in t.lower():
        return 0.0
    best = None
    for pat, days in _PATTERNS:
        m = re.search(pat, t, re.I)
        if m:
            v = float(m.group(1)) * days
            if best is None or v > best:   # 多个分量（如 3h 15min）取最大者作主判据
                best = v
    if best is not None:
        return round(best, 3)
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        try:
            dt = datetime.datetime(y, mo, d)
            return round((now - dt).total_seconds() / 86400.0, 3)
        except ValueError:
            return None
    m = re.search(r"\b(\d{1,2})-(\d{1,2})\b", t)          # 当年 MM-DD
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        try:
            dt = datetime.datetime(now.year, mo, d)
        except ValueError:
            return None
        if dt > now + datetime.timedelta(days=1):
            dt = dt.replace(year=now.year - 1)
        return round((now - dt).total_seconds() / 86400.0, 3)
    return None


def today_str():
    return datetime.datetime.now().strftime("%Y-%m-%d")


def fmt_age(days):
    if days is None:
        return "时间未知"
    if days < 1 / 24:
        return f"{int(days * 1440)}分钟前"
    if days < 1:
        return f"{days * 24:.0f}小时前"
    if days < 14:
        return f"{days:.0f}天前"
    return f"{days / 7:.1f}周前"


def qq_send(title, text):
    """通知渠道 官方机器人 OpenAPI 直推。

    为什么走这里而不是本机 /app/notify：GitHub Actions 的 runner 在云上，
    碰不到手机的 127.0.0.1:3090，所以云端必须有一条能直连公网的后端。
    通知渠道 开放平台的 C2C 单聊消息正好是公网 API，云端和手机两端都能用。

    凭据只从环境变量读，仓库里不留明文：
        RADAR_通知渠道_APPID   机器人 AppID
        RADAR_通知渠道_SECRET  机器人 AppSecret
        RADAR_通知渠道_OPENID  接收方 openid（单聊）
    access_token 有效期约 2 小时，这里每次现取，不做缓存（简报本身也是低频）。
    返回 None 表示「没配 通知渠道」；返回字符串表示「配了，结果是这个」。
    """
    appid = (os.environ.get("RADAR_QQ_APPID") or "").strip()
    secret = (os.environ.get("RADAR_QQ_SECRET") or "").strip()
    openid = (os.environ.get("RADAR_QQ_OPENID") or "").strip()
    if not (appid and secret and openid):
        return None
    import json as _json

    tok = ""
    try:
        p = subprocess.run(
            ["curl", "-s", "-m", "20", "-X", "POST",
             "https://bots.qq.com/app/getAppAccessToken",
             "-H", "Content-Type: application/json",
             "-d", _json.dumps({"appId": str(appid), "clientSecret": secret})],
            capture_output=True, text=True, timeout=25)
        tok = (_json.loads(p.stdout or "{}") or {}).get("access_token") or ""
    except Exception as e:
        return f"qq 取 token 失败: {type(e).__name__}: {e}"
    if not tok:
        return "qq 取 token 失败：返回里没有 access_token"

    body = _json.dumps({"content": f"{title}\n{text}"[:2000], "msg_type": 0},
                       ensure_ascii=False)
    try:
        q = subprocess.run(
            ["curl", "-s", "-m", "20", "-X", "POST",
             f"https://api.sgroup.qq.com/v2/users/{openid}/messages",
             "-H", f"Authorization: QQBot {tok}",
             "-H", f"X-Union-Appid: {appid}",
             "-H", "Content-Type: application/json",
             "-d", body],
            capture_output=True, text=True, timeout=25)
        out = (q.stdout or "").strip()
        # 成功响应形如 {"id":"ROBOT1.0_...","timestamp":"..."}
        if '"id"' in out:
            return "qq 已发"
        return f"qq 发送失败: {out[:180]}"
    except Exception as e:
        return f"qq 发送失败: {type(e).__name__}: {e}"


def notify(title, text):
    """推送到用户 通知渠道（首选，云端可用），失败再退本机通知栏，最后 Telegram。"""
    err = qq_send(title, text)
    if err == "qq 已发":
        return err
    # 本机（手机上的 DSH）才有 DSHA 桥
    try:
        p = subprocess.run(
            ["curl", "-s", "-m", "15", "-H", "@/root/.dsh/.bridge_headers",
             "-G", "--data-urlencode", "title=" + title,
             "--data-urlencode", "text=" + text,
             "http://127.0.0.1:3090/app/notify"],
            capture_output=True, text=True, timeout=20)
        if (p.stdout or "").strip():
            return (p.stdout or "").strip()[:200]
    except Exception:
        pass
    # Telegram（可选后端）
    bot = os.environ.get("RADAR_TG_BOT")
    chat = os.environ.get("RADAR_TG_CHAT")
    if bot and chat:
        try:
            q = subprocess.run(
                ["curl", "-s", "-m", "20", "-X", "POST",
                 f"https://api.telegram.org/bot{bot}/sendMessage",
                 "--data-urlencode", "chat_id=" + chat,
                 "--data-urlencode", "text=" + f"{title}\n{text}"[:3800]],
                capture_output=True, text=True, timeout=25)
            return (q.stdout or "").strip()[:200] or "tg 已发"
        except Exception as e:
            return f"tg 失败: {type(e).__name__}: {e}"
    if err:
        return err          # 配了 通知渠道 但发失败了，把真实错误带出去，别假装成「没后端」
    return "无通知后端（跳过了）"
