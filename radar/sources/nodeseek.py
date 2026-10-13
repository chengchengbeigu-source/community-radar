# -*- coding: utf-8 -*-
"""source-b 采集器：真浏览器过 CF 挑战 + 解析列表页；浏览器不行时退 RSS。"""
import re, time, html, email.utils, datetime
from radar.util import parse_age_days, UA, get_proxy, playwright_proxy, http_get

BASE = "https://www.nodeseek.com"
RSS = "https://rss.nodeseek.com/"
CHALLENGE = ("just a moment", "attention required", "checking your browser", "verify you are human",
             "请稍候", "正在验证")
ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
        "--disable-blink-features=AutomationControlled", "--no-first-run",
        "--disable-features=Translate,BackForwardCache"]

JS = r"""() => {
  const out = [];
  document.querySelectorAll('li.post-list-item').forEach(li => {
    const a = li.querySelector('a[href^="/post-"]');
    if (!a) return;
    const img = li.querySelector('img[alt]');
    const infoEl = li.querySelector('.post-info');
    out.push({
      href: a.getAttribute('href'),
      title: (a.innerText || '').replace(/\s+/g, ' ').trim(),
      author: img ? (img.getAttribute('alt') || '') : '',
      info: ((infoEl || li).innerText || '').replace(/\s+/g, ' ').trim(),
      html: (li.outerHTML || '').slice(0, 1500)
    });
  });
  return out;
}"""


def _board_of(html, fallback=""):
    m = re.search(r'/categories/([a-z0-9\-]+)', html or "")
    return m.group(1) if m else fallback


def _age(info):
    if not info:
        return None, ""
    m = re.search(r"(\d[^|]{0,26}?(?:ago|前))", info)
    seg = m.group(1).strip() if m else info
    return parse_age_days(seg), seg


def _grab(br, url, budget=30):
    """每个 URL 用一个**全新 context** 抓。

    为什么必须这样：GitHub 机房实测（2026-10-12）—— 同一个 context 里连续导航，
    第 1 次能过 CF（拿到列表），第 2 次起全部卡在挑战页（title='请稍候…'，45s 无列表）；
    而每个 URL 独立开新 context 冷启动，13 个列表页 12 个 1~3 秒就拿到 50+ 条。
    本机因为出口 IP 干净，同一 context 连续导航没问题，所以本地一直没暴露这个坑。

    返回 (rows, title, err)；rows 为 None 表示没抓到。
    """
    ctx = None
    try:
        ctx = br.new_context(user_agent=UA, locale="zh-CN",
                             viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        t0 = time.time()
        while time.time() - t0 < budget:
            try:
                rows = page.evaluate(JS)
            except Exception:
                rows = None
            if rows:
                return rows, (page.title() or ""), None
            time.sleep(1.0)
        title = page.title() or ""
        return None, title, f"过挑战但列表为空 title={title[:24]!r}"
    except Exception as e:
        return None, "", f"{type(e).__name__}: {str(e)[:90]}"
    finally:
        try:
            if ctx is not None:
                ctx.close()
        except Exception:
            pass


def _rss_items(cfg, scfg, log=print):
    """RSS 兜底通道（rss.source-b.com，不过 CF）。

    为什么要它：浏览器通道在云端有概率整轮被 CF 挡住（见 _grab 注释），
    RSS 站是 source-b 官方挂出来的独立域名，直连 200 且不需要过挑战。
    代价是只能拿最近的（分页是滑动窗口，`?page=N` 翻几页就退化成同一窗口），
    且**没有板块字段可用**（channel 里 9 个 category 全列，`?category=` 之类参数无效），
    所以只能作为补量，不能替代列表页。

    字段映射：<guid>→ext_id、<title>→title、<link>→url、<description>→excerpt、
    <category>→board、<dc:creator>→author、<pubDate>→age_days。
    """
    prx = get_proxy(cfg, scfg.get("proxy"))
    pages = int(scfg.get("rss_pages", 6))
    out, seen = [], set()
    for pg in range(1, pages + 1):
        url = RSS + (f"?page={pg}" if pg > 1 else "")
        try:
            x = http_get(url, timeout=25, proxy=prx)
        except Exception as e:
            log(f"  [nodeseek] RSS 第 {pg} 页失败 {type(e).__name__}")
            break
        if "&lt;item&gt;" in x or "<item>" not in x:
            log(f"  [nodeseek] RSS 第 {pg} 页无 item（len={len(x)}）")
            break
        got = 0
        for blk in re.findall(r"<item>(.*?)</item>", x, re.S):
            def _f(tag, b=blk):
                # 命名空间前缀必须容忍：<dc:creator> 而 tag 只叫 creator
                m = re.search(rf"<(?:[a-zA-Z0-9]+:)?{tag}[^>]*>(.*?)</(?:[a-zA-Z0-9]+:)?{tag}>", b, re.S)
                if not m:
                    return ""
                v = m.group(1).strip()
                m2 = re.match(r"<!\[CDATA\[(.*?)\]\]>$", v, re.S)
                return (m2.group(1) if m2 else v).strip()
            pid = _f("guid") or (re.search(r"/post-(\d+)", _f("link")) or [None, ""])[1]
            if not pid or pid in seen:
                continue
            seen.add(pid)
            got += 1
            title = html.unescape(_f("title"))
            pub = _f("pubDate")
            age = None
            if pub:
                try:
                    dt = email.utils.parsedate_to_datetime(pub).replace(tzinfo=None)
                    age = round((datetime.datetime.utcnow() - dt).total_seconds() / 86400.0, 3)
                except Exception:
                    age = None
            out.append({
                "source": "nodeseek", "ext_id": pid, "uid": f"nodeseek:{pid}",
                "title": title, "url": _f("link") or f"{BASE}/post-{pid}-1",
                "board": _f("category"), "author": _f("creator"),
                "excerpt": html.unescape(_f("description"))[:300],
                "age_text": pub[:25], "age_days": age, "created_ts": pub or None,
                "raw": {"via": "rss", "page": pg},
            })
        if not got:
            break
    log(f"  [nodeseek] RSS 兜底 {len(out)} 条（{pages} 页窗口）")
    return out


def _browser_items(cfg, scfg, window_days, targets, seen, log):
    """真浏览器通道（playwright + chromium 过 CF 挑战）。

    单独拆出来是为了让调用方能整体兜住它：playwright 没装、chromium 装不上、
    或者 CF 把整条通道打死时，异常只落在这一层，不至于把整个 collect() 炸掉、
    连 RSS 兜底都跑不到。
    """
    from playwright.sync_api import sync_playwright
    items = []
    prx = playwright_proxy(get_proxy(cfg, scfg.get("proxy")))
    if prx:
        log(f"  [nodeseek] 浏览器走代理 {prx['server']}")
    tries = max(1, int(scfg.get("retries", 2)))
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True, args=ARGS, proxy=prx)
        try:
            for url, board in targets:
                rows, title, err = None, "", ""
                for attempt in range(1, tries + 1):
                    rows, title, err = _grab(br, url, 30)
                    if rows:
                        break
                    log(f"  [nodeseek] {url} 第 {attempt}/{tries} 次失败（{err}）")
                    if attempt < tries:
                        time.sleep(2)  # 换全新 context 再试，别在同一个被挡的 context 里耗
                if not rows:
                    continue
                ages = []
                for r in rows:
                    m = re.search(r"/post-(\d+)", r.get("href") or "")
                    if not m:
                        continue
                    pid = m.group(1)
                    age, age_txt = _age(r.get("info", ""))
                    if age is not None:
                        ages.append(age)
                    b = _board_of(r.get("html", ""), board)
                    if (pid, b) in seen:
                        continue
                    seen.add((pid, b))
                    items.append({
                        "source": "nodeseek", "ext_id": pid,
                        "uid": f"nodeseek:{pid}",
                        "title": r.get("title") or "", "url": BASE + r.get("href", ""),
                        "board": b, "author": r.get("author") or "",
                        "excerpt": "", "age_text": age_txt, "age_days": age,
                        "created_ts": None, "raw": {"info": r.get("info", "")[:400]},
                    })
                if ages and min(ages) > window_days:
                    log(f"  [nodeseek] {url} 已翻到 {min(ages):.0f} 天前，超出窗口早停")
                    break
        finally:
            br.close()
    return items


def collect(cfg, scfg, window_days, log=print):
    targets = [(BASE + p, "") for p in (scfg.get("listings") or [])]
    for b in (scfg.get("boards") or []):
        targets.append((f"{BASE}/categories/{b}", b))
        for n in range(2, int(scfg.get("board_pages", 1)) + 1):
            targets.append((f"{BASE}/categories/{b}/page-{n}", b))
    items, seen = [], set()
    try:
        items = _browser_items(cfg, scfg, window_days, targets, seen, log)
    except Exception as e:
        # 这里绝不能把异常放出去：浏览器通道是「增强」，RSS 才是保底。
        # 历史上就是这一行缺 try/except，砍掉 playwright 后整个 source-b 采集成 0 条。
        log(f"  [nodeseek] 浏览器通道不可用（{type(e).__name__}: {str(e)[:120]}），转 RSS 兜底")
    # 浏览器通道拿到的条数太少（云端被 CF 挡时经常只剩首页那几十条），用 RSS 补量。
    if scfg.get("rss_fallback", True) and len(items) < int(scfg.get("rss_min_items", 150)):
        try:
            rss = _rss_items(cfg, scfg, log)
        except Exception as e:
            log(f"  [nodeseek] RSS 兜底异常 {type(e).__name__}: {str(e)[:90]}")
            rss = []
        for it in rss:
            key = (it["ext_id"], it["board"] or "")
            if key in seen:
                continue
            seen.add(key)
            items.append(it)
    log(f"  [nodeseek] 采集 {len(items)} 条")
    return items
