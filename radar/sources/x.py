# -*- coding: utf-8 -*-
"""source-x 采集器：X（Twitter）账号最新推文，全程免鉴权。

== 为什么是这套管道（2026-10-26 云端+手机双端实测）==

死掉的路：
  - RSSHub 官方实例 /twitter/user/<name> → 302 到 google.com/404，路由已关
  - 五个 Nitter 实例 → 200 但零推文 / 000 / 403 / 404
  - X syndication timeline（按账号要时间线）→ 429，按 IP 封
  - guest token → GraphQL UserTweets → token 取不到，403
  - X 搜索页（未登录）→ 200 但只有 23KB 空壳，0 个推文 ID

活着的主路（**fxtwitter 第三方镜像，一次请求拿整条时间线**）：
  https://api.fxtwitter.com/2/profile/<screen_name>/statuses
  → 200 / ~31KB JSON，results[] 里 14~20 条完整推文（正文/时间/作者/互动数全有）
  → cursor.bottom 可翻页（第 2 页再 19 条）
  → 实测 15 个账号 14 个成功（xai 返回 500，走兜底）

兜底路（X 自家两个端点，手机可用、云端被 403）：
  ① https://x.com/<screen_name>  → SSR HTML，正则抠 /status/<id>，只给最近 5~8 条
  ② https://cdn.syndication.twimg.com/tweet-result?id=<id>&lang=en&token=a → 单推 JSON

== 为什么 fxtwitter 是主路而不是兜底 ==
云端实测（GitHub Actions / Azure IP）：
   curl 打 x.com/opencode        → **403**（数据中心 IP 被 X 封）
   curl 打 fxtwitter /2/profile  → **200 / 31350B**
手机实测：
   两个都 200，但 x.com 只给 5~9 条，fxtwitter 给 14~20 条
⇒ fxtwitter 既更多又更通用，双端都能跑。所以它当主路。

== 短板（上游限制，不修）==
  - 搜索页拿不到东西，做不了关键词全站搜索
  - 靠 uid 增量去重工作：每轮抓同一批，只有新 ID 才入库
  这两点正好匹配定时任务形态：盯固定账号，来新的就报。
"""
import datetime
import json
import os
import re
import tempfile
import time

from radar.util import http_get, get_proxy
from radar.util import DATA_DIR

# 主路：一次请求拿整条时间线
FX_TL = "https://api.fxtwitter.com/2/profile/%s/statuses"
# 兜底：X 自家（云端会被 403，手机可用）
PAGE = "https://x.com/%s"
SYND = "https://cdn.syndication.twimg.com/tweet-result?id=%s&lang=en&token=a"

# 服务端渲染页里推文链接形如 /opencode/status/2113699251616412056
_RE_STATUS = re.compile(r"/status/(\d{15,20})")
# syndication 返回 ISO8601（2026-10-23T18:29:06.000Z）；fxtwitter 返回
# "Sun Oct 25 16:38:32 +0000 2026"（同时也给 created_timestamp 的 unix 秒）
_TS_OLD = "%a %b %d %H:%M:%S %z %Y"

# 干净的浏览器 UA：X 对 curl 默认 UA 会返回更小的壳，带上这个才给 SSR 内容
_HDR = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}
# fxtwitter 要 JSON 的 Accept，不然可能给 HTML
_HDR_JSON = {
    "User-Agent": _HDR["User-Agent"],
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------- 已见 ID 缓存 ----------------
# 走 fxtwitter 主路时每条推文的正文跟 ID 一起来，不需要逐条补抓，所以这层缓存
# 现在只服务两条路：① 兜底路（x.com 页 + 单推 API）省掉重复取正文；
# ② 判断「这轮有没有真的新东西」，日志里报得准。
_CACHE_NAME = "x_seen.json"
_CACHE_MAX_DAYS = 30


def _cache_path():
    return os.path.join(DATA_DIR, _CACHE_NAME)


def _cache_load(log=print):
    p = _cache_path()
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        cut = (datetime.datetime.now() - datetime.timedelta(days=_CACHE_MAX_DAYS)).timestamp()
        return {k: v for k, v in d.items() if isinstance(v, (int, float)) and v >= cut}
    except (OSError, ValueError):
        return {}


def _cache_save(d, log=print):
    """原子写，失败不炸（缓存丢了最多多抓一遍，不该让整轮采集挂掉）。"""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=DATA_DIR, prefix=".x_seen.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, _cache_path())
    except OSError as e:
        log(f"  [x] 缓存写入失败（忽略）{type(e).__name__}")


# ---------------- 时间 ----------------

def _age_days(ts):
    """aware datetime → 距今多少天（保留 1 位小数）。None 原样返回。"""
    if ts is None:
        return None
    now = datetime.datetime.now(datetime.timezone.utc)
    return round((now - ts).total_seconds() / 86400.0, 1)


def _parse_ts(s):
    """老式 "Fri Oct 23 18:29:06 +0000 2026" 或 ISO8601 → aware datetime；失败 None。"""
    if not s:
        return None
    s = str(s).strip()
    try:
        dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        pass
    try:
        return datetime.datetime.strptime(s, _TS_OLD)
    except ValueError:
        return None


def _ids_from_page(html):
    """从账号页 HTML 抠推文 ID，按数值倒序（越大越新）。"""
    ids = {m.group(1) for m in _RE_STATUS.finditer(html or "")}
    return sorted(ids, key=int, reverse=True)


# ---------------- 主路：fxtwitter 时间线 ----------------

def _fx_norm(t):
    """fxtwitter 的 results[] 单条 → 本模块统一的 tweet dict。

    fxtwitter 字段（实测）：id / url / text / raw_text / author{} / replies /
    reposts / likes / bookmarks / quotes / created_at / created_timestamp /
    views / lang / media / reposted_by{}
    转推：reposted_by 是被转的人（=我们盯的账号），author 是原作者。
    """
    if not isinstance(t, dict):
        return None
    tid = str(t.get("id") or "")
    if not tid:
        return None
    # 正文：优先 text；个别是 note tweet（长推）时 raw_text.text 更全
    text = (t.get("text") or "").strip()
    if not text:
        raw = t.get("raw_text")
        if isinstance(raw, dict):
            text = (raw.get("text") or "").strip()
    author = t.get("author") or {}
    # 时间：created_timestamp（unix 秒）最稳，其次 created_at 字符串
    ts = None
    ux = t.get("created_timestamp")
    if isinstance(ux, (int, float)) and ux > 0:
        ts = datetime.datetime.fromtimestamp(ux, datetime.timezone.utc)
    if ts is None:
        ts = _parse_ts(t.get("created_at"))
    return {
        "id": tid,
        "text": text,
        "created_at": ts,
        "created_raw": (t.get("created_at") or "")[:19],
        "screen_name": author.get("screen_name") or "",
        "name": author.get("name") or "",
        "likes": t.get("likes") or 0,
        "replies": t.get("replies") or 0,
        "is_rt": bool(t.get("reposted_by")),
        "reposted_by": ((t.get("reposted_by") or {}).get("screen_name") or ""),
        "url": t.get("url") or f"https://x.com/i/status/{tid}",
        "views": t.get("views") or 0,
    }


def _fx_timeline(acct, proxy, log):
    """主路：一次请求拿整条时间线。返回 tweet dict 列表（新→旧），失败返回 None。

    注意返回 [] 和 None 含义不同：
      []   = 请求成功但这个账号没有可解析的推文（停用/全私密）
      None = 请求失败，调用方该走兜底
    """
    url = FX_TL % acct
    last = None
    for attempt in range(3):
        try:
            raw = http_get(url, headers=_HDR_JSON, timeout=25, proxy=proxy)
            d = json.loads(raw)
            if isinstance(d, dict) and d.get("code") not in (None, 200):
                # fxtwitter 用 code 表达业务错误（xai 实测 500）
                last = f"code={d.get('code')} {d.get('message') or ''}".strip()
                break
            rs = (d or {}).get("results") or []
            out = [x for x in (_fx_norm(t) for t in rs) if x]
            return out
        except Exception as e:
            last = f"{type(e).__name__}"
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    log(f"  [x] @{acct} fxtwitter 失败（{last}），转兜底")
    return None


# ---------------- 兜底：x.com 页 + 单推 API ----------------

def _tweet(tid, proxy, log):
    """单推 JSON → 规整 dict；失败返回 None。"""
    try:
        raw = http_get(SYND % tid, headers=_HDR, timeout=20, proxy=proxy)
        d = json.loads(raw)
    except Exception as e:
        log(f"  [x] 推文 {tid} 取正文失败 {type(e).__name__}")
        return None
    body = d
    rt = d.get("retweeted_status") or d.get("quoted_tweet")
    text = (d.get("text") or "").strip()
    if not text and isinstance(rt, dict):
        body = rt
        text = (rt.get("text") or "").strip()
    user = body.get("user") or d.get("user") or {}
    return {
        "id": tid,
        "text": text,
        "created_at": _parse_ts(body.get("created_at") or d.get("created_at")),
        "created_raw": (body.get("created_at") or d.get("created_at") or "")[:19],
        "screen_name": user.get("screen_name") or "",
        "name": user.get("name") or "",
        "likes": body.get("favorite_count") or d.get("favorite_count") or 0,
        "replies": body.get("conversation_count") or d.get("conversation_count") or 0,
        "is_rt": bool(d.get("retweeted_status")),
        "reposted_by": "",
        "url": f"https://x.com/i/status/{tid}",
        "views": 0,
    }


# ---------------- 共用 ----------------

def _to_item(t, screen_name, window_days):
    """单推 → radar 通用 item。"""
    text = (t.get("text") or "").replace("\n", " ").strip()
    title = text[:180] if text else f"@{screen_name} 的推文"
    url = t.get("url") or ""
    # fxtwitter 给的 url 是规范链接；兜底路没 url 就自己拼
    if not url or not url.startswith("http"):
        url = f"https://x.com/{screen_name or t.get('screen_name') or 'i'}/status/{t['id']}"
    return {
        "source": "x",
        "ext_id": t["id"],
        "uid": f"x:{t['id']}",
        "title": title,
        "url": url,
        "board": "X/官方发布",
        # 盯的账号 vs 实际作者：转推时两者不同，报「谁转的」更有用
        "author": screen_name or t.get("screen_name") or "",
        "excerpt": text[:300],
        "age_text": (t.get("created_raw") or "")[:19],
        "age_days": _age_days(t.get("created_at")),
        "created_ts": t.get("created_at").isoformat() if t.get("created_at") else None,
        "raw": {"likes": t.get("likes"), "replies": t.get("replies"),
                "views": t.get("views"), "rt": t.get("is_rt"),
                "via": screen_name, "origin": t.get("screen_name")},
    }


def collect(cfg, scfg, window_days, log=print):
    """抓一轮。scfg 里认这些键：
      accounts: ["opencode", "deepseek_ai", ...]   # 要盯的账号（不带 @）
      tweets:   ["https://x.com/a/status/123"]     # 额外钉死的推文（会一直抓）
      per_account: 8                               # 每账号最多留几条
      delay: 0.8                                   # 账号间间隔秒数，别打太快
      proxy: direct                                # 走 get_proxy 的语义
    """
    prx = get_proxy(cfg, scfg.get("proxy"))
    accounts = [a.strip().lstrip("@") for a in (scfg.get("accounts") or []) if a.strip()]
    per = max(1, int(scfg.get("per_account", 8)))
    delay = float(scfg.get("delay", 0.8))

    items, seen = [], set()
    seen_cache = _cache_load(log)
    cache_before = len(seen_cache)
    new_ids = skipped = 0
    via_fx = via_fb = 0

    for i, acct in enumerate(accounts):
        if i:
            time.sleep(delay)

        tws = _fx_timeline(acct, prx, log)

        if tws is None:
            # ---------- 兜底：x.com 页 + 单推 API ----------
            html = None
            for attempt in range(3):
                try:
                    html = http_get(PAGE % acct, headers=_HDR, timeout=20, proxy=prx)
                    break
                except Exception as e:
                    if attempt == 2:
                        log(f"  [x] @{acct} 兜底也失败 3 次（{type(e).__name__}），跳过")
                    else:
                        time.sleep(1.5 * (attempt + 1))
            if html is None:
                continue
            ids = _ids_from_page(html)[:per]
            if not ids:
                log(f"  [x] @{acct} 页面里没抠到推文 ID（改名、停用或纯转推）")
                continue
            tws = []
            for tid in ids:
                if tid in seen_cache:
                    skipped += 1
                    continue
                t = _tweet(tid, prx, log)
                if t:
                    tws.append(t)
            via_fb += 1
        else:
            via_fx += 1

        if not tws:
            log(f"  [x] @{acct} 没有可用推文")
            continue

        got = 0
        for t in tws[:max(per, len(tws) if via_fb else per)]:
            uid = f"x:{t['id']}"
            if uid in seen:
                continue
            is_new = t["id"] not in seen_cache
            seen.add(uid)
            seen_cache[t["id"]] = seen_cache.get(t["id"]) or time.time()
            if is_new:
                new_ids += 1
            it = _to_item(t, acct, window_days)
            # 时间解析失败就别用坏值卡窗口判断
            if it["age_days"] is not None and it["age_days"] > window_days:
                continue
            items.append(it)
            got += 1
            if got >= per:
                break
        log(f"  [x] @{acct} {len(tws)} 条 → 留 {got} 条"
            f"{'（兜底路）' if via_fb and tws and not via_fx else ''}")

    # 额外钉死的单推（用户丢过来的链接，长期保留在配置里）
    for u in (scfg.get("tweets") or []):
        m = _RE_STATUS.search(str(u))
        if not m:
            log(f"  [x] 钉死的推文格式不对，跳过：{u}")
            continue
        tid = m.group(1)
        if f"x:{tid}" in seen:
            continue
        t = _tweet(tid, prx, log)
        if not t:
            continue
        seen.add(f"x:{tid}")
        seen_cache[tid] = seen_cache.get(tid) or time.time()
        items.append(_to_item(t, t.get("screen_name"), window_days))

    _cache_save(seen_cache, log)
    log(f"  [x] 采集 {len(items)} 条（{len(accounts)} 个账号）｜"
        f"主路 {via_fx} / 兜底 {via_fb}｜新 ID {new_ids}，缓存跳过 {skipped}，"
        f"缓存 {cache_before}→{len(seen_cache)}")
    return items
