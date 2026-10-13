# -*- coding: utf-8 -*-
"""生成每日 Markdown 简报 + 通知文案。"""
import json
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
