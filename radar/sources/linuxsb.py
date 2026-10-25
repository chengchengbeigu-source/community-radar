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


_BODY_PATS = [
    r'<div class="nb-editor-post-content"[^>]*>(.*?)</div>',
    r'<div class="post-readability-content"[^>]*>(.*?)</div>',
    r'<div class="long-content-fold-content"[^>]*>(.*?)</div>',
]


def _extract_body(html):
    """从单帖页抠正文。列表页只给标题，正文必须再抓一次单帖页。

    正文在 nb-editor-post-content 里（2026-10-26 实测：post-content /
    post-body 都是外壳，抠出来只有「1523 130」这种计数垃圾）。
    """
    for pat in _BODY_PATS:
        m = re.search(pat, html, re.S)
        if not m:
            continue
        txt = re.sub(r'<script[^>]*>.*?</script>', ' ', m.group(1), flags=re.S | re.I)
        txt = re.sub(r'<style[^>]*>.*?</style>', ' ', txt, flags=re.S | re.I)
        txt = re.sub(r'<br\s*/?>', ' ', txt, flags=re.I)
        txt = re.sub(r'</p>|</li>|</div>', ' ', txt, flags=re.I)
        txt = re.sub(r'<[^>]+>', ' ', txt)
        txt = _html.unescape(txt)
        txt = re.sub(r'[ \t\u3000]+', ' ', txt)
        txt = re.sub(r'\n\s*\n+', '\n', txt).strip()
        if len(txt) >= 20:
            return txt[:1200]
    return ""


def _fetch_bodies(items, cap, prx, hdr, log):
    """给缺正文的条目补抓单帖页，最多 cap 条（按标题里「福利/免费」优先）。"""
    todo = [it for it in items if not it.get("excerpt")]
    if not todo:
        return 0
    # 有羊毛信号的先补，纯技术贴排后面
    kw = ("免费", "白嫖", "福利", "送", "抽", "额度", "试用", "优惠", "折", "兑换码")
    todo.sort(key=lambda it: 0 if any(k in it["title"] for k in kw) else 1)
    done = 0
    for it in todo[:cap]:
        try:
            h = http_get(it["url"], proxy=prx, headers=hdr)
            body = _extract_body(h)
            if body:
                it["excerpt"] = body
                done += 1
        except Exception:
            continue
    log(f"  [linuxsb] 补抓正文 {done}/{min(len(todo), cap)} 条")
    return done


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

    # ── 3) 补正文：列表页只有标题，正文得进单帖页 ──
    cap = int(scfg.get("body_fetch_max", 12) or 0)
    if cap > 0:
        _fetch_bodies(items, cap, prx, hdr, log)
    return items
