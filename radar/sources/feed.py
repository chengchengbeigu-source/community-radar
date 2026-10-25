# -*- coding: utf-8 -*-
"""通用 RSS / Atom 采集器 —— 让「加一个新站」变成填一个 URL，不用写代码。

为什么需要它：每接一个新论坛都写一个 xxx.py 太笨。绝大多数论坛（Discourse、
Flarum、Discuz、WordPress、Ghost、Substack…）都有 RSS，抓 RSS 就能拿到
标题 + 链接 + 摘要 + 时间，对「薅羊毛情报雷达」够用了。

配置（config.yaml）：
  sources:
    feed:
      enabled: true
      feeds:
        - name: 某某论坛
          url: https://example.com/feed.xml
          board: 福利          # 可选，做板块标签用
          keep: []             # 可选，只留标题含这些词的
      proxy: direct

想加站就在面板里加一行，下次采集自动生效。
"""
import datetime
import re

from radar.util import http_get, get_proxy

UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")

_ENT = {"&lt;": "<", "&gt;": ">", "&amp;": "&", "&quot;": '"',
        "&#39;": "'", "&apos;": "'", "&nbsp;": " "}


def _unescape(s):
    for a, b in _ENT.items():
        s = s.replace(a, b)
    s = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))) if int(m.group(1)) < 0x110000 else " ", s)
    s = re.sub(r"&#x([0-9a-fA-F]+);",
               lambda m: chr(int(m.group(1), 16)) if int(m.group(1), 16) < 0x110000 else " ", s)
    return s


def _strip_tags(s):
    s = re.sub(r"<script[^>]*>.*?</script>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<style[^>]*>.*?</style>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    s = re.sub(r"</p>|</li>|</div>|</h\d>|</tr>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return s


def _text(s, limit=600):
    """HTML → 纯文本。

    顺序很关键：RSS 的 description 常是 CDATA 包着的**转义 HTML**，
    形如 `&lt;div class="..."&gt;&lt;p&gt;正文`。先剥标签再反转义的话，
    标签这层皮扒不掉，推出去用户看到的就是一串 <div>。
    2026-10-26 linux.sb 的 RSS 实测踩到：正文前面挂着
    `<div class="post-readability-content"><div class="nb-editor-post-content">`。
    """
    s = str(s or "")
    s = re.sub(r"<!\[CDATA\[|\]\]>", " ", s)   # CDATA 壳先摘掉，别让它混进正文
    for _ in range(2):                            # 反转义 → 剥标签，跑两遍（可能双重转义）
        s = _strip_tags(_unescape(s))
    return re.sub(r"\s+", " ", s).strip()[:limit]


def _pick(block, *tags):
    """按候选标签名依次取第一个非空值（RSS 和 Atom 标签不一样）。"""
    for t in tags:
        m = re.search(rf"<{t}[^>]*>(.*?)</{t}>", block, re.S | re.I)
        if m and m.group(1).strip():
            return m.group(1)
    # Atom 的 link 是自闭合属性形式：<link href="..."/>
    m = re.search(r'<link[^>]+href="([^"]+)"', block, re.I)
    return m.group(1) if m else ""


def _age(pub):
    if not pub:
        return None
    s = str(pub).strip()
    # RSS: RFC822；Atom: ISO8601
    for parser in ("rfc822", "iso"):
        try:
            if parser == "rfc822":
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(s).astimezone(datetime.timezone.utc).replace(tzinfo=None)
            else:
                dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
                if dt.tzinfo:
                    dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
            return round((datetime.datetime.utcnow() - dt).total_seconds() / 86400.0, 3)
        except Exception:
            continue
    return None


def _parse(xml, feed_name, board, source_id):
    """RSS 的 <item> 和 Atom 的 <entry> 都吃。"""
    blocks = re.findall(r"<item>(.*?)</item>", xml, re.S | re.I)
    if not blocks:
        blocks = re.findall(r"<entry>(.*?)</entry>", xml, re.S | re.I)
    out, seen = [], set()
    for b in blocks:
        title = _text(_pick(b, "title"), 200)
        link = _text(_pick(b, "link", "guid", "id"), 400)
        pub = _text(_pick(b, "pubDate", "published", "updated", "dc:date"), 60)
        desc = _text(_pick(b, "description", "content:encoded", "content", "summary"), 600)
        if not title or not link or not link.startswith("http"):
            continue
        if link in seen:
            continue
        seen.add(link)
        ext = link.rstrip("/").rsplit("/", 1)[-1].split("?")[0] or link
        out.append({
            "source": source_id,
            "ext_id": ext,
            "uid": f"{source_id}:{ext}",
            "title": title,
            "url": link,
            "board": board or feed_name,
            "author": _text(_pick(b, "dc:creator", "author", "name"), 60),
            "excerpt": desc,
            "age_text": pub[:25],
            "age_days": _age(pub),
            "created_ts": pub,
            "raw": {},
        })
    return out


def collect(cfg, scfg, window_days, log=print):
    prx = get_proxy(cfg, scfg.get("proxy"))
    hdr = {"User-Agent": UA, "accept": "application/rss+xml,application/xml,text/xml,*/*"}
    feeds = scfg.get("feeds") or []
    if not feeds:
        log("  [feed] 没配 feeds，跳过（面板里加一行就行）")
        return []
    items = []
    for f in feeds:
        if isinstance(f, str):
            f = {"url": f, "name": f}
        if not f.get("url"):
            continue
        name = str(f.get("name") or f["url"])[:40]
        if f.get("enabled") is False:
            continue
        try:
            xml = http_get(f["url"], proxy=prx, headers=hdr, timeout=25)
            got = _parse(xml, name, f.get("board") or "", "feed")
            keep = [k for k in (f.get("keep") or []) if k]
            if keep:
                got = [g for g in got if any(k in g["title"] for k in keep)]
            log(f"  [feed] {name} → {len(got)} 条")
            items += got
        except Exception as e:
            log(f"  [feed] {name} 失败 {type(e).__name__}: {str(e)[:120]}")
    log(f"  [feed] 合计 {len(items)} 条")
    return items
