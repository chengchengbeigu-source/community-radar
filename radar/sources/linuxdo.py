# -*- coding: utf-8 -*-
"""linux.do（DISCOURSE）采集器 —— 走 RSS，绕开 Cloudflare 的 JSON 403。

为什么不用 latest.json：
  2026-10-26 实测 `https://linux.do/latest.json` → **403 + "Just a moment..."**
  （Cloudflare JS 挑战）。但 `https://linux.do/latest.rss` → **200 / 88KB**，
  Discourse 的 RSS 端点没挂 CF 挑战，是最省事的官方出口。

过滤策略：
  linux.do 上「搞七捻三」（闲聊）占大头，全收会淹掉干货。默认只留
  开发调优 / 资源分享 / 福利 / 技术 一类板块（见 keep_boards），
  不命中时按 keep_all=false 丢弃；想全收就把 keep_all 设 true。
"""
import datetime
import re
import urllib.parse

from radar.util import http_get, get_proxy

BASE = "https://linux.do"
UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")

# 只留这些板块（小写子串匹配）；空列表 = 全留
DEFAULT_KEEP = ["开发调优", "资源分享", "福利", "技术", "教程", "分享", "搞七捻三"]


def _strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = (s.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
          .replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " "))
    return re.sub(r"\s{2,}", " ", s).strip()


def _age(pub):
    if not pub:
        return None
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(pub).astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return round((datetime.datetime.utcnow() - dt).total_seconds() / 86400.0, 3)
    except Exception:
        return None


def _parse(xml):
    """极简 RSS 解析：不引 feedparser，正则足够（Discourse 的输出很规整）。"""
    out = []
    for it in re.findall(r"<item>(.*?)</item>", xml, re.S):
        def pick(tag):
            m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", it, re.S)
            return m.group(1) if m else ""
        title = _strip_html(pick("title"))
        link = pick("link").strip()
        cat = _strip_html(pick("category"))
        pub = pick("pubDate").strip()
        desc = _strip_html(pick("description"))[:400]
        if not title or not link:
            continue
        out.append({"title": title, "url": link, "board": cat,
                    "pub": pub, "excerpt": desc})
    return out


def collect(cfg, scfg, window_days, log=print):
    prx = get_proxy(cfg, scfg.get("proxy"))
    keep = scfg.get("keep_boards")
    keep = DEFAULT_KEEP if keep is None else list(keep)
    keep_all = bool(scfg.get("keep_all", False))

    endpoints = []
    for ep in (scfg.get("endpoints") or ["/latest.rss", "/top.rss?period=daily"]):
        endpoints.append(ep if ep.startswith("http") else BASE + ep)

    seen, items = set(), []
    for url in endpoints:
        try:
            xml = http_get(url, proxy=prx, headers={"User-Agent": UA})
        except Exception as e:
            log(f"  [linuxdo] {url} 失败 {type(e).__name__}: {e}")
            continue
        rows = _parse(xml)
        log(f"  [linuxdo] {url.split('/')[-1][:28]} → {len(rows)} 条")
        for r in rows:
            if r["url"] in seen:
                continue
            seen.add(r["url"])
            b = r["board"]
            if not keep_all and keep and not any(k.lower() in b.lower() for k in keep):
                continue
            tid = re.search(r"/t/topic/(\d+)", r["url"]) or re.search(r"/topic/(\d+)", r["url"])
            items.append({
                "source": "linuxdo",
                "ext_id": tid.group(1) if tid else r["url"],
                "uid": f"linuxdo:{(tid.group(1) if tid else r['url'])}",
                "title": r["title"][:200],
                "url": r["url"],
                "board": b or "linux.do",
                "author": "",
                "excerpt": r["excerpt"],
                "age_text": (r["pub"] or "")[:25],
                "age_days": _age(r["pub"]),
                "created_ts": r["pub"],
                "raw": {},
            })
    log(f"  [linuxdo] 合计 {len(items)} 条（板块过滤后）")
    return items
