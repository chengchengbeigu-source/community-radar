# -*- coding: utf-8 -*-
"""source-x 采集器：X（Twitter）账号最新推文，全程免鉴权。

为什么是这套管道（2026-10 实测，别的路都死了）：
  - RSSHub 官方实例 /twitter/user/<name> → 302 到 google.com/404，路由已关
  - 五个 Nitter 实例 → 200 但零推文 / 000 / 403 / 404
  - X syndication timeline（按账号要时间线）→ 429，按 IP 封
  - guest token → GraphQL UserTweets → token 取不到，403
  - X 搜索页（未登录）→ 200 但只有 23KB 空壳，0 个推文 ID

能用的只有两个端点的组合：
  ① https://x.com/<screen_name>                          免鉴权，服务端渲染 ~140KB HTML
     → 里面带 /status/<id> 的链接，正则抠出最近 5~8 条推文 ID
  ② https://cdn.syndication.twimg.com/tweet-result?id=<id>&lang=en&token=a
     → 免鉴权 JSON，返回正文 / 时间 / 作者 / 互动数

实测：16 个 AI 账号里 15 个能取到正文，同账号连打 6 次全 200，不被封。

已知短板（不修，是上游限制）：
  - 每个账号只能看到最近 5~9 条，翻不了页（/with_replies、/media 都是 0 条）
  - 搜索页拿不到东西，所以做不了关键词全站搜索
  - 因此本采集器靠"增量去重"工作：每轮都抓同一批，只有新 ID 才入库

好消息是这正好匹配定时任务的形态：盯固定账号，来新的就报，不来的不重复。
"""
import datetime
import json
import os
import re
import tempfile
import time

from radar.util import http_get, get_proxy
from radar.util import DATA_DIR

PAGE = "https://x.com/%s"
SYND = "https://cdn.syndication.twimg.com/tweet-result?id=%s&lang=en&token=a"

# 服务端渲染页里推文链接形如 /opencode/status/2113699251616412056
_RE_STATUS = re.compile(r"/status/(\d{15,20})")
# syndication 返回的 created_at 实测是 ISO8601：2026-10-23T18:29:06.000Z
# （老版 API 曾是 "Fri Oct 23 18:29:06 +0000 2026"，两种都认，别只认一种）
_RE_TS_ISO = "%Y-%m-%dT%H:%M:%S"
_RE_TS_OLD = "%a %b %d %H:%M:%S %z %Y"

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


# ---------------- 已见 ID 缓存 ----------------
# 为什么需要：每轮都把 15 个账号的 80+ 条推文全量取一遍正文，是纯浪费 ——
# 90% 的 ID 上轮已经抓过。存一份「见过的 ID → 首次见到时间」，老 ID 直接跳过，
# 只有新 ID 才发第二个请求。手机上这能把每轮请求数从 ~96 降到 ~15。
_CACHE_NAME = "x_seen.json"
_CACHE_MAX_DAYS = 30


def _cache_path():
    return os.path.join(DATA_DIR, _CACHE_NAME)


def _cache_load(log=print):
    p = _cache_path()
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        # 过期清理：只保留 30 天内的
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


def _age_days(dt):
    """datetime → 距今天数（保留三位小数，和别的源统一口径）。"""
    if dt is None:
        return None
    return round((datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds() / 86400.0, 3)


def _parse_ts(s):
    """ISO8601（2026-10-23T18:29:06.000Z）或老式 "Fri Oct 23 18:29:06 +0000 2026"
    → aware datetime；失败返回 None。"""
    if not s:
        return None
    s = str(s).strip()
    try:
        # 先按 ISO8601 处理：去掉毫秒尾部再拼
        base = s.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(base)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except ValueError:
        pass
    try:
        return datetime.datetime.strptime(s, _RE_TS_OLD)
    except ValueError:
        return None


def _ids_from_page(html):
    """从账号页 HTML 抠推文 ID，按数值倒序（越大越新）。"""
    ids = {m.group(1) for m in _RE_STATUS.finditer(html or "")}
    return sorted(ids, key=int, reverse=True)


def _tweet(tid, proxy, log):
    """单推 JSON → 规整 dict；失败返回 None。"""
    try:
        import json
        raw = http_get(SYND % tid, headers=_HDR, timeout=20, proxy=proxy)
        d = json.loads(raw)
    except Exception as e:
        log(f"  [x] 推文 {tid} 取正文失败 {type(e).__name__}")
        return None
    # 转推：正文在 retweeted_status 里
    body = d
    rt = d.get("retweeted_status") or d.get("quoted_tweet")
    text = (d.get("text") or "").strip()
    if not text and isinstance(rt, dict):
        body = rt
        text = (rt.get("text") or "").strip()
    user = body.get("user") or d.get("user") or {}
    ts = _parse_ts(body.get("created_at") or d.get("created_at"))
    return {
        "id": tid,
        "text": text,
        "created_at": ts,
        "created_raw": body.get("created_at") or d.get("created_at") or "",
        "screen_name": user.get("screen_name") or "",
        "name": user.get("name") or "",
        "likes": body.get("favorite_count") or d.get("favorite_count") or 0,
        "replies": body.get("conversation_count") or d.get("conversation_count") or 0,
        "is_rt": bool(d.get("retweeted_status")),
    }


def _to_item(t, screen_name, window_days):
    """单推 → radar 通用 item。"""
    text = (t.get("text") or "").replace("\n", " ").strip()
    # 标题用正文第一段；太长就截断（report 里会再截）
    title = text[:180] if text else f"@{screen_name} 的推文"
    return {
        "source": "x",
        "ext_id": t["id"],
        "uid": f"x:{t['id']}",
        "title": title,
        "url": f"https://x.com/{screen_name or t.get('screen_name') or 'i'}/status/{t['id']}",
        "board": "X/官方发布",
        "author": t.get("screen_name") or screen_name or "",
        "excerpt": text[:300],
        "age_text": (t.get("created_raw") or "")[:19],
        "age_days": _age_days(t.get("created_at")),
        "created_ts": t.get("created_at").isoformat() if t.get("created_at") else None,
        "raw": {"likes": t.get("likes"), "replies": t.get("replies"),
                "rt": t.get("is_rt"), "via": screen_name},
    }


def collect(cfg, scfg, window_days, log=print):
    """抓一轮。scfg 里认这些键：
      accounts: ["opencode", "deepseek_ai", ...]   # 要盯的账号（不带 @）
      tweets:   ["https://x.com/a/status/123"]     # 额外钉死的推文（会一直抓）
      per_account: 8                               # 每账号最多取几条（页面本身只给 5~9）
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
    new_ids = 0
    skipped = 0

    for i, acct in enumerate(accounts):
        if i:
            time.sleep(delay)
        # 账号页重试：X 对无头请求偶发直接断连（URLError），一次失败不代表号没了。
        # 手机上尤其常见（基站切换 / IPv6 抖动），重试 2 次能吃掉绝大部分抖动。
        html = None
        for attempt in range(3):
            try:
                html = http_get(PAGE % acct, headers=_HDR, timeout=20, proxy=prx)
                break
            except Exception as e:
                if attempt == 2:
                    log(f"  [x] @{acct} 账号页失败 3 次（{type(e).__name__}），跳过")
                else:
                    time.sleep(1.5 * (attempt + 1))
        if html is None:
            continue
        ids = _ids_from_page(html)[:per]
        if not ids:
            # 账号可能改了名 / 停用 / 发全转推，不算错
            log(f"  [x] @{acct} 页面里没抠到推文 ID（改名、停用或纯转推）")
            continue
        got = 0
        for tid in ids:
            if f"x:{tid}" in seen:
                continue
            # 缓存命中且不是钉死项：跳过。正文早入库了，没必要每轮重取。
            if tid in seen_cache:
                skipped += 1
                continue
            t = _tweet(tid, prx, log)
            if not t:
                continue
            seen.add(f"x:{tid}")
            seen_cache[tid] = time.time()
            new_ids += 1
            it = _to_item(t, acct, window_days)
            # 时间解析失败就别用坏值卡窗口判断
            if it["age_days"] is not None and it["age_days"] > window_days:
                continue
            items.append(it)
            got += 1
        log(f"  [x] @{acct} 抠到 {len(ids)} 个 ID，取到 {got} 条正文")

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
        f"新 ID {new_ids}，缓存跳过 {skipped}，缓存 {cache_before}→{len(seen_cache)}")
    return items
