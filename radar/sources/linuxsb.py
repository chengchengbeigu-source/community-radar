# -*- coding: utf-8 -*-
"""linux.sb（烧饼社区）采集器 —— 走 RSS + forum 板块 HTML。

两路数据：
  1) `/daily_hot_topics_rss`（24h/36h/week 三档）→ 热帖，带回 HTML 正文，理想
  2) `/forum/{id}` HTML → 板块最新帖，用于「福利放送」「技术交流」这类定向抓

为什么不用 API：2026-10-26 实测它的前端是服务端渲染的 PHP，index.js 里
没有任何 API 路径，只有 form/DOM 操作。所以只能抓 HTML 或 RSS。

板块号（实测）：
  1 错误地方  2 福利放送  3 资源分享  4 技术交流
  5 求助问答  6 社区治理  7 消息      8 我要推广
  9 社区公告 10 大禹治水
"""
import datetime
import html as _html
import re

from radar.util import http_get, get_proxy

BASE = "https://linux.sb"
UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")

# 默认只抓这两块：福利放送（羊毛）+ 技术交流（干货）
DEFAULT_FORUMS = [2, 4]
FORUM_NAME = {1: "错误地方", 2: "福利放送", 3: "资源分享", 4: "技术交流",
              5: "求助问答", 6: "社区治理", 7: "消息", 8: "我要推广",
              9: "社区公告", 10: "大禹治水"}


def _clean(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s{2,}", " ", _html.unescape(s)).strip()


def _parse_rss(xml):
    out = []
    for it in re.findall(r"<item>(.*?)</item>", xml, re.S):
        def pick(tag):
            m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", it, re.S)
            return m.group(1) if m else ""
        title = _html.unescape(pick("title")).strip()
        link = pick("link").strip()
        desc = _clean(pick("description"))[:400]
        pub = pick("pubDate").strip()
        if not title or not link:
            continue
        out.append({"title": title, "url": link, "board": "热帖",
                    "excerpt": desc, "pub": pub})
    return out


def _parse_forum(page_html, fid):
    """从板块页抠 topic 链接 + 标题。同一条链接在页里出现多次，去重。"""
    out, seen = [], set()
    # 三个捕获组：整路径 / topic 号 / 内层 HTML
    for href, _tid, inner in re.findall(r'<a[^>]+href="(/topic/(\d+))"[^>]*>(.*?)</a>',
                                        page_html, re.S):
        if href in seen:
            continue
        seen.add(href)
        t = _clean(inner)
        if not t or len(t) < 3:
            continue
        out.append({"title": t[:200], "url": BASE + href,
                    "board": FORUM_NAME.get(fid, f"forum{fid}"),
                    "excerpt": "", "pub": "", "ext_id": href.rsplit("/", 1)[-1]})
    return out


def _age_days(pub):
    if not pub:
        return None
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(pub).astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return round((datetime.datetime.utcnow() - dt).total_seconds() / 86400.0, 3)
    except Exception:
        return None


def collect(cfg, scfg, window_days, log=print):
    prx = get_proxy(cfg, scfg.get("proxy"))
    hdr = {"User-Agent": UA}
    rows = []

    # ── 1) 热帖 RSS ──
    period = scfg.get("rss_period", "24h")
    rss_url = f"{BASE}/daily_hot_topics_rss" + (f"?period={period}" if period != "24h" else "")
    try:
        xml = http_get(rss_url, proxy=prx, headers=hdr)
        got = _parse_rss(xml)
        log(f"  [linuxsb] 热帖 RSS({period}) → {len(got)} 条")
        rows += got
    except Exception as e:
        log(f"  [linuxsb] RSS 失败 {type(e).__name__}: {e}")

    # ── 2) 板块页 ──
    for fid in (scfg.get("forums") or DEFAULT_FORUMS):
        try:
            html = http_get(f"{BASE}/forum/{fid}", proxy=prx, headers=hdr)
            got = _parse_forum(html, fid)
            log(f"  [linuxsb] forum/{fid} {FORUM_NAME.get(fid,'')} → {len(got)} 条")
            rows += got
        except Exception as e:
            log(f"  [linuxsb] forum/{fid} 失败 {type(e).__name__}: {e}")

    # 去重 + 转成统一结构
    seen, items = set(), []
    for r in rows:
        u = r["url"]
        if u in seen:
            continue
        seen.add(u)
        ext = r.get("ext_id") or (u.rstrip("/").rsplit("/", 1)[-1])
        items.append({
            "source": "linuxsb",
            "ext_id": ext,
            "uid": f"linuxsb:{ext}",
            "title": r["title"],
            "url": u,
            "board": r["board"],
            "author": "",
            "excerpt": r.get("excerpt", ""),
            "age_text": (r.get("pub") or "")[:25],
            "age_days": _age_days(r.get("pub")),
            "created_ts": r.get("pub", ""),
            "raw": {},
        })
    log(f"  [linuxsb] 合计 {len(items)} 条")
    return items
