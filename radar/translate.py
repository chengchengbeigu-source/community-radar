# -*- coding: utf-8 -*-
"""把英文推送文案翻成中文（推送前调用）。

为什么自己写而不接 LLM：
  - 本机 CPT 池（8317）走 IPv6 出网，googleapis 超时
  - OrcaTerm 本地桥（8899）上游 VERIFY_SKEY_UIN_FAILED
  - 所有公益端点都要 key，且各有各的怪
  MyMemory 免费、无 key、走 HTTPS 稳定，质量对「读懂大意」足够。

三条硬约束：
  1) 纯英文才翻；中文/日文/数字为主的直接跳过，别浪费请求
  2) 单次上限约 500 字符（超了 414），按句子切块分批
  3) 任何失败都返回原文 —— 翻译是锦上添花，不能因为它把推送弄丢
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.mymemory.translated.net/get"
LIMIT = 480          # 实测安全上限（官方说 500，留余量）
_UA = "Mozilla/5.0 (Linux; Android 14) radar-translate/1.0"

# 中日文/韩文任一出现，说明不是纯英文，不用翻
_RE_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")


def needed(text):
    """要不要翻：有 CJK 就不翻；字母占比太低（纯链接/纯数字）也不翻。"""
    s = str(text or "")
    if not s.strip():
        return False
    if _RE_CJK.search(s):
        return False
    letters = sum(1 for c in s if c.isascii() and c.isalpha())
    return letters >= 12


def _chunks(text, limit=LIMIT):
    """按句子切成 ≤limit 的块；单句超长就硬切。"""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return [text]
    parts, buf = [], ""
    for sent in re.split(r"(?<=[.!?])\s+", text):
        while len(sent) > limit:          # 单句本身超长
            if buf:
                parts.append(buf); buf = ""
            parts.append(sent[:limit]); sent = sent[limit:]
        if len(buf) + len(sent) + 1 <= limit:
            buf = f"{buf} {sent}".strip() if buf else sent
        else:
            if buf:
                parts.append(buf)
            buf = sent
    if buf:
        parts.append(buf)
    return parts


def _one(chunk, src="en", dst="zh-CN", timeout=20):
    q = urllib.parse.quote(chunk[:LIMIT])
    url = f"{API}?q={q}&langpair={src}|{dst}"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    d = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    t = (d.get("responseData") or {}).get("translatedText") or ""
    # 上游偶尔把错误说明当译文返回
    if not t or "MYMEMORY WARNING" in t.upper() or "QUERY LENGTH LIMIT" in t.upper():
        return None
    return t


def to_zh(text, log=None):
    """英文 → 中文。失败一律返回原文（宁可不翻，不能丢内容）。"""
    if not needed(text):
        return text
    out = []
    for i, ck in enumerate(_chunks(text)):
        try:
            t = _one(ck)
            if t:
                out.append(t)
            else:
                out.append(ck)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as e:
            if log:
                log(f"  [译] 第{i+1}块失败 {type(e).__name__}，保留英文")
            out.append(ck)
    return " ".join(out)


if __name__ == "__main__":
    import sys
    print(to_zh(" ".join(sys.argv[1:]), log=print))

# 翻译前先剥 emoji：夹在句中的 emoji 会让译文断成「由⚡您构建」这种鬼样
_RE_EMOJI = re.compile(
    "[" 
    "\U0001F300-\U0001FAFF"   # 符号与象形、扩展
    "\U00002600-\U000027BF"   # 杂项符号、装饰
    "\U0001F1E6-\U0001F1FF"   # 区域指示符（国旗）
    "\U00002190-\U000021FF"   # 箭头
    "\U00002B00-\U00002BFF"
    "\U0000FE0F"              # 变体选择符
    "\U0000200D"              # 零宽连接符
    "]+"
)


def strip_emoji(text):
    """去掉 emoji 及其残留连接符，再并掉多余空格。原文照留，只处理译文。"""
    t = _RE_EMOJI.sub(" ", str(text or ""))
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t
