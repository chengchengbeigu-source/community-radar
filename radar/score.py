# -*- coding: utf-8 -*-
"""规则打分：关键词命中 + 板块权重 + 限时信号 → 星级与「为什么值得点」。"""
import re

# 直接丢：标题命中就等于低质内容/交易贴，连库都不进（见 junk_reason）。
# 故意只放「一眼就是低质内容」的词，宁可少杀不可错杀 —— 误杀的代价是漏掉真内容。
DROP_WORDS = ["出售", "收购", "求购", "代办", "代刷", "接单", "报价", "刷单",
              "兼职", "引流", "加微信", "微信号", "加v信", "低价出", "有偿", "招租",
              # ── 英文广告/垃圾（X 源用得上）──
              # 只放「一眼就是卖东西/拉人」的，正常产品公告不该命中。
              # giveaway / promo code / referral 不算广告 —— 那正是「薅羊毛」目标
              "subscribe to my", "dm me", "check my bio", "link in bio",
              "buy now", "shop now"]

# 直接丢：这些板块是闲聊/晒图/撕逼，不是内容
DROP_BOARDS = ["meaningless", "photo-share", "expose"]

FALLBACK = {
    "drop": DROP_WORDS,
    "drop_boards": DROP_BOARDS,
    "groups": [
        {"tag": "公益/免费", "weight": 3, "kws": ["公益", "公益站", "免费", "免费领", "免费送", "免费使用", "福利", "羊毛", "薅", "免费"]},
        {"tag": "VPS/主机", "weight": 3, "kws": ["vps", "服务器", "小鸡", "虚拟主机", "免费vps", "试用", "甲骨文", "oracle", "az100", "aws", "hetzner"]},
        {"tag": "教程", "weight": 2, "kws": ["教程", "手把手", "保姆级", "指南", "自建", "搭建", "一键", "脚本", "部署", "docker", "配置", "攻略"]},
        {"tag": "优惠/上车", "weight": 2, "kws": ["优惠", "优惠码", "折扣", "促销", "半价", "年付", "上车", "拼车", "团购"]},
        {"tag": "注册/额度", "weight": 2, "kws": ["注册送", "注册即送", "新人", "试用", "额度", "代金券", "优惠券", "充值送", "返现", "api key", "免费api", "兑换码",
                                                "free credits", "free tier", "credits", "referral", "promo code", "trial"]},
        {"tag": "活动/抽奖", "weight": 2, "kws": ["抽奖", "中奖", "活动", "领取", "限时", "最后一天", "截止", "免费领取",
                                                "giveaway", "contest", "hackathon", "grand prize", "win a"]},
        {"tag": "资源/节点", "weight": 1, "kws": ["图床", "cdn", "对象存储", "oss", "网盘", "订阅", "机场", "免费机场", "中转", "镜像", "node", "节点"]},
    ],
    "negative": {"weight": -2, "kws": ["出售", "收购", "求购", "仲裁", "曝光", "骗子", "撕逼", "水贴", "灌水", "闲聊", "路过"]},
    "boards": {"info": 2.5, "review": 2.0, "tech": 1.5, "dev": 1.5, "promotion": 2.5,
               "trade": 1.0, "carpool": 1.0, "daily": 0.0, "life": 0.0,
               "meaningless": -1.0, "expose": -1.5, "sandbox": -0.5, "photo-share": -1.0},
    "urgent": ["限时", "最后一天", "今天截止", "截止", "先到先得", "还剩", "仅剩", "马上", "仅限", "即将结束"],
}


def _kw_cfg(cfg):
    k = (cfg or {}).get("keywords") or {}
    return {
        "groups": k.get("groups") or FALLBACK["groups"],
        "negative": k.get("negative") or FALLBACK["negative"],
        "boards": k.get("boards") or FALLBACK["boards"],
        "urgent": k.get("urgent") or FALLBACK["urgent"],
        "drop": k.get("drop") or FALLBACK["drop"],
        "drop_boards": k.get("drop_boards") or FALLBACK["drop_boards"],
    }


def _hit(word, text):
    """词命中判定：英文走词边界，中文走子串。

    为什么必需：纯 `w in text` 会把 trial 命中 indus-trial、free 命中 freeway、
    oss 命中 boss/loss/cross —— 英文词必须卡词边界。
    中文没有空格分词，只能子串（「免费」命中「免费领」是对的）。
    """
    w = (word or "").lower()
    if not w:
        return False
    if w.isascii():
        body = r"[\s\-_]+".join(re.escape(x) for x in w.split())
        return re.search(r"(?<![a-z0-9])" + body + r"(?![a-z0-9])", text) is not None
    return w in text


def junk_reason(title, board="", excerpt="", cfg=None, min_neg=3):
    """判断这条是不是「不值得推」的垃圾。是就返回中文理由，不是返回 None。

    三条规则（宁可少杀不可错杀 —— 误杀的代价是漏掉真内容）：
      1) **标题**里出现 drop 词（出售/代累计/接单/加微信…）→ 低质内容或交易贴，直接丢；
      2) 板块属于 drop_boards（meaningless/photo-share/expose）→ 闲聊晒图撕逼，直接丢；
      3) 标题+摘要里 negative 词命中 >= min_neg 个 → 掺水太多，丢。

    第 1 条只看标题：摘要里顺嘴提一句「出售」不算，标题写了才是真在卖东西。
    """
    kc = _kw_cfg(cfg)
    t = (title or "").lower()
    for w in kc["drop"]:
        if _hit(w, t):
            return f"标题含广告词「{w}」"
    b = (board or "").lower()
    if b and b in [str(x).lower() for x in kc["drop_boards"]]:
        return f"板块「{board}」不是内容"
    text = f"{title} {excerpt}".lower()
    n = sum(1 for w in kc["negative"]["kws"] if _hit(w, text))
    if n >= min_neg:
        return f"命中 {n} 个掺水词"
    return None


def score_item(title, board="", excerpt="", cfg=None):
    """返回 (score 0-10, stars 1-5, tags, reasons)。"""
    kc = _kw_cfg(cfg)
    text = f"{title} {excerpt}".lower()
    s, tags, reasons = 0.0, [], []
    for g in kc["groups"]:
        hits = [w for w in g["kws"] if _hit(w, text)]
        if hits:
            s += g["weight"]
            tags.append(g["tag"])
            reasons.append(f"命中「{'/'.join(hits[:3])}」→ {g['tag']}")
    for w in kc["negative"]["kws"]:
        if _hit(w, text):
            s += kc["negative"]["weight"]
            reasons.append(f"掺水分「{w}」")
            break
    bw = kc["boards"].get((board or "").lower(), 0)
    if bw:
        s += bw
        reasons.append(("板块加分 " if bw > 0 else "板块降权 ") + f"{board}")
    for w in kc["urgent"]:
        if w in title or w in excerpt:
            s += 1
            tags.append("限时")
            reasons.append(f"限时信号「{w}」")
            break
    s = max(0.0, min(10.0, s))
    stars = 5 if s >= 7 else 4 if s >= 5 else 3 if s >= 3.5 else 2 if s >= 2 else 1
    if "限时" in tags and stars < 5:
        stars += 1
    return round(s, 1), stars, sorted(set(tags)), reasons[:4]
