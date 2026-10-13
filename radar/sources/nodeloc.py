# -*- coding: utf-8 -*-
"""source-a 采集器：上游站点 JSON 直连（无需浏览器）。"""
import json, urllib.parse, datetime
from radar.util import http_get, get_proxy

BASE = "https://www.nodeloc.com"


def _age_iso(iso):
    if not iso:
        return None
    try:
        dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).replace(tzinfo=None)
        return round((datetime.datetime.now() - dt).total_seconds() / 86400.0, 3)
    except Exception:
        return None


def _topic(t, cats, users):
    cid = t.get("category_id")
    posters = t.get("posters") or [{}]
    return {
        "source": "nodeloc", "ext_id": str(t.get("id")),
        "uid": f"nodeloc:{t.get('id')}",
        "title": (t.get("title") or "").strip(),
        "url": f"{BASE}/t/{t.get('id')}",
        "board": cats.get(cid, ""), "author": users.get(posters[0].get("user_id"), ""),
        "excerpt": (t.get("excerpt") or "")[:300],
        "age_text": (t.get("created_at") or "")[:19].replace("T", " "),
        "age_days": _age_iso(t.get("created_at")),
        "created_ts": t.get("created_at"),
        "raw": {"posts": t.get("posts_count"), "views": t.get("views"), "tags": t.get("tags")},
    }


def collect(cfg, scfg, window_days, log=print):
    cats, users, items, seen = {}, {}, [], set()
    prx = get_proxy(cfg, scfg.get("proxy"))
    try:
        d = json.loads(http_get(f"{BASE}/categories.json", proxy=prx))
        cats = {c["id"]: c.get("name", "") for c in d.get("category_list", {}).get("categories", [])}
        for c in d.get("category_list", {}).get("categories", []):
            for s in (c.get("subcategory_list") or []):
                cats[s["id"]] = s.get("name", "")
    except Exception as e:
        log(f"  [nodeloc] categories 拉取失败 {type(e).__name__}")

    pages = int(scfg.get("latest_pages", 3))
    for p in range(pages):
        url = f"{BASE}/latest.json" + (f"?page={p}" if p else "")
        try:
            d = json.loads(http_get(url, proxy=prx))
        except Exception as e:
            log(f"  [nodeloc] {url} 失败 {type(e).__name__}")
            break
        for u in (d.get("users") or []):
            users[u.get("id")] = u.get("username", "")
        tl = d.get("topic_list") or {}
        topics = tl.get("topics") or []
        if not topics:
            break
        ages = []
        for t in topics:
            it = _topic(t, cats, users)
            if it["age_days"] is not None:
                ages.append(it["age_days"])
            if it["uid"] not in seen:
                seen.add(it["uid"])
                items.append(it)
        if ages and min(ages) > window_days:
            log(f"  [nodeloc] latest 第 {p+1} 页已到 {min(ages):.0f} 天前，早停")
            break
        if not tl.get("more_topics_url"):
            break

    for term in (scfg.get("search_terms") or []):
        for p in range(int(scfg.get("search_pages", 1))):
            url = f"{BASE}/search.json?q={urllib.parse.quote(term)}&page={p+1}"
            try:
                d = json.loads(http_get(url, proxy=prx))
            except Exception as e:
                log(f"  [nodeloc] 搜索「{term}」失败 {type(e).__name__}")
                continue
            for u in (d.get("users") or []):
                users[u.get("id")] = u.get("username", "")
            for t in (d.get("topics") or []):
                it = _topic(t, cats, users)
                if it["uid"] not in seen:
                    seen.add(it["uid"])
                    items.append(it)
    log(f"  [nodeloc] 采集 {len(items)} 条")
    return items
