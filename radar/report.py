# -*- coding: utf-8 -*-
"""生成每日 Markdown 简报 + 通知文案。"""
import json
import re
from radar.util import fmt_age

HINT = {"公益/免费": "先确认还活着/要不要注册", "VPS/主机": "看额度与到期时间，够用就领",
        "教程": "先收藏，按需照做", "优惠/上车": "看是否限时、要不要优惠码",
        "注册/额度": "注册并领额度（注意邮箱/验证）", "活动/抽奖": "门槛低的话去抽",
        "资源/节点": "先测速再决定留不留"}


def _stars(rows, n):
    return [r for r in rows if r["stars"] == n]


def _detail(r):
    tags = json.loads(r["tags"] or "[]")
    reasons = json.loads(r["reason"] or "[]")
    hints = [HINT[t] for t in tags if t in HINT]
    if "限时" in tags:
        hints.insert(0, "⏰ 有时限，优先处理")
    out = [f"- **[{r['title']}]({r['url']})**"]
    bits = [b for b in [r["board"], r["source"], fmt_age(r["age_days"])] if b]
    out.append(f"  - {' · '.join(bits)}" + (f" · 作者 {r['author']}" if r["author"] else ""))
    if reasons:
        out.append(f"  - 评分 {r['score']}｜" + "；".join(reasons))
    if hints:
        out.append(f"  - 建议：{'；'.join(hints)}")
    return "\n".join(out)


def filter_rows(rows, cfg):
    """按门槛把不值得推的砍掉。这是「过滤」真正生效的地方 ——
    之前的版本只排序不剔除，低质内容/水贴照样推。

    - min_stars：星级低于这个数的直接不要（默认 3，即 ≤2★ 一律不推）
    - min_score：裸评分低于这个数的也砍（默认 0）
    """
    ms = int(cfg.get("min_stars", 3) or 0)
    msc = float(cfg.get("min_score", 0) or 0)
    return [r for r in rows if r["stars"] >= ms and r["score"] >= msc]


def build(rows, window, cfg):
    rows = sorted(rows, key=lambda r: (-r["stars"], -r["score"]))
    by_src = {}
    for r in rows:
        by_src[r["source"]] = by_src.get(r["source"], 0) + 1
    L = [f"# {cfg.get('report_title', 'community-radar')} · {__import__('datetime').date.today()}",
         "",
         f"> 时间窗口：近 {window} 天｜未汇报条目 {len(rows)} 条（" +
         "、".join(f"{k} {v}" for k, v in by_src.items()) + "）",
         "> 星级 = 关键词/板块/限时信号综合打分：5★ 立刻去看，4★ 值得一看，3★ 备着，≤2★ 仅存档。",
         ""]
    five, four = _stars(rows, 5), _stars(rows, 4)
    if five:
        L += ["## 🔥 5★ 立刻去看", ""] + [_detail(r) for r in five] + [""]
    if four:
        L += ["## ⭐ 4★ 值得一看", ""] + [_detail(r) for r in four] + [""]
    three = _stars(rows, 3)
    if three:
        L += ["## 🗂 3★ 备着", ""]
        for r in three:
            L.append(f"- [{r['title']}]({r['url']}) · {r['board'] or r['source']} · {fmt_age(r['age_days'])}")
        L.append("")
    low = [r for r in rows if r["stars"] <= 2]
    if low:
        L += [f"## 📦 其他 {len(low)} 条（≤2★，仅存档）", ""]
        for r in low[:60]:
            L.append(f"- [{r['title']}]({r['url']}) · {r['board'] or r['source']} · {fmt_age(r['age_days'])}")
        if len(low) > 60:
            L.append(f"- …其余 {len(low) - 60} 条见同名 .json")
        L.append("")
    L += ["---", "",
          "调参：`radar/config.yaml`（窗口天数、板块、关键词权重、例行任务站点）。",
          "重跑：`python3 -m radar run`；例行任务：`python3 -m radar checkin`。"]
    heads = [r["title"] for r in (five + four)[:3]]
    note = f"未汇报 {len(rows)} 条｜5★{len(five)} / 4★{len(four)}"
    if heads:
        note += "｜" + "；".join(h[:24] for h in heads)
    note += f"\n详情：radar/outbox/{__import__('datetime').date.today()}.md"
    return "\n".join(L), note[:400]


def digest(cfg, day, fresh_by_src, five, four, three, dropped,
           progress_text=None, checkin_lines=None):
    """20:00 的当日总结文案（纯文本，通知渠道 里能直接看）。

    参数都是现成数字/短句，不在本函数里做 IO —— 调用方（radar/scheduler.py）负责取数。
    """
    title = cfg.get("report_title", "community-radar")
    L = [f"📋 {title} · {day} 日报", ""]
    tot = sum(fresh_by_src.values()) if fresh_by_src else 0
    if fresh_by_src:
        L.append("今日新增：" + "、".join(f"{k} {v}" for k, v in sorted(fresh_by_src.items())))
    else:
        L.append("今日新增：0 条")
    L.append(f"值得看：5★ {five}｜4★ {four}｜3★ {three}｜已过滤 {dropped} 条")
    if checkin_lines:
        L += ["", "例行任务：" + "；".join(checkin_lines)]
    if progress_text:
        L += ["", "在线时长：" + str(progress_text)]
    if tot == 0 and not (five or four or three):
        L += ["", "（今天没有值得推的新帖）"]
    return "\n".join(L)


# ═══════════════════════════════════════════════════════════════
#  逐条推送文案（2026-10-26 新增）
#
#  旧行为：N 条压成一条 400 字符的摘要，标题被砍到 24 字，
#  用户看到的是「MiniMax M3.1 Flash is no; Today @MiniMax_AI releases;」
#  这种半截碎片，完全不知道在说什么、点去哪。
#
#  新行为：一条一个消息，固定四段结构 ——
#      日期 / 渠道 / 标题 / 正文 / 链接
#  单条即可读完，不需要再翻 outbox 文件。
# ═══════════════════════════════════════════════════════════════

# 渠道中文名：推文里写「X/官方发布」比写 x 清楚
SRC_CN = {"x": "X（推特）", "nodeseek": "NodeSeek", "nodeloc": "NodeLoc"}


def _clip(s, n):
    """按字符裁，超了加省略号。中文按字算，别用字节。"""
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _title_of(r, limit=90):
    """标题：取正文首句（到句号/问号/换行为止），没有就用整个摘要。"""
    raw = " ".join(str(r.get("title") or "").split())
    if not raw:
        return "（无标题）"
    m = re.search(r"[.!?。！？]\s", raw)
    if m and m.start() >= 12:
        raw = raw[: m.start() + 1]
    return _clip(raw, limit)


def one_msg(r, day, cfg=None, translate=True):
    """单条 → 一条完整的推送消息。四段固定结构，缺项不占行。

    返回 (title, text)：title 进通知标题栏，text 是正文。
    translate=True 时正文翻成中文（原文附在后面），英文推文不翻译的话
    用户读起来就是天书 —— 2026-10-26 实测反馈。
    """
    title_cfg = (cfg or {}).get("report_title", "社区情报雷达")
    stars = "★" * int(r.get("stars") or 0)
    src = SRC_CN.get(str(r.get("source") or ""), str(r.get("source") or ""))
    board = str(r.get("board") or "")
    # 板块里若已含来源名就不重复（x 源的 board 是 "X/官方发布"）
    chan = board if board else src
    if board.startswith("X/") and src.startswith("X"):
        chan = "X（推特）· " + board.split("/", 1)[1]

    raw_title = _title_of(r)
    body_en = _clip(r.get("excerpt") or r.get("title") or "", 400)

    zh_title, zh_body = raw_title, body_en
    if translate:
        try:
            from radar import translate as _tr
            if _tr.needed(raw_title):
                zh_title = _tr.strip_emoji(_tr.to_zh(raw_title) or raw_title)
            if _tr.needed(body_en):
                zh_body = _tr.strip_emoji(_tr.to_zh(body_en) or body_en)
        except Exception:
            pass          # 翻译是锦上添花，失败就用原文

    # 标题栏优先中文（英文原文一样时说明没翻，不重复标）
    head = f"{stars} {zh_title}"
    L = [f"📅 {day}", f"📡 {chan}"]
    author = str(r.get("author") or "").strip()
    if author:
        L.append(f"👤 @{author}")
    L.append("")
    L.append(zh_body)
    # 翻了才附原文；没翻就别重复贴两遍
    if zh_body != body_en:
        L += ["", "——— 原文 ———", body_en]
    L.append("")
    url = str(r.get("url") or "").strip()
    if url:
        L.append(url)
    return head[:60], "\n".join(L)


def build_notes(rows, day, cfg=None):
    """把 rows 拆成「一条一个消息」的列表，按星级降序。

    返回 [(title, text, row)]，调用方逐条 push。
    超过 max_per_push 条时只发前 N 条（防刷屏），其余留在简报里。
    """
    cap = int((cfg or {}).get("max_per_push", 10) or 10)
    out = []
    for r in sorted(rows, key=lambda x: (-(x.get("stars") or 0), -(x.get("score") or 0)))[:cap]:
        t, b = one_msg(r, day, cfg, translate=bool((cfg or {}).get("push_translate", True)))
        out.append((t, b, r))
    return out
