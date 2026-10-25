现象: 推送内容是「MiniMax M3.1 Flash is no; Today @MiniMax_AI releases;」这种碎片。
根因: 通知文案把 N 条压成一条，每条标题硬砍 24 字（`h[:24]`），拼成 `；` 分隔。
修法: 一条一个消息，正文取首句不硬砍，标题/正文/链接/日期/渠道分五行。

## 旧逻辑（`radar/report.py` 的 `build()` 末尾）

```python
heads = [r["title"] for r in (five + four)[:3]]
note = f"未汇报 {len(rows)} 条｜5★{len(five)} / 4★{len(four)}"
note += "｜" + "；".join(h[:24] for h in heads)     # ← 病根
note += f"\n详情：radar/outbox/{date.today()}.md"
```

问题：
1. N 条挤成 1 条，还限 400 字符 → 大量截断
2. `h[:24]` 按字符砍，英文正好砍在单词中间
3. 只有「详情看某文件」，手机上看不到正文和链接

## 新结构（`one_msg()`）

```
★★★★ 中文标题（取原推首句，最多 90 字）
📅 2026-10-26
📡 X（推特）· 官方发布
👤 @actual_author

中文正文（400 字内）

——— 原文 ———
English original

https://x.com/.../status/...
```

## 关键判断

- **标题取首句，不砍字数**：`re.search(r"[.!?。！？]\s", raw)` 找第一个句末标点，
  找不到才硬截。英文标题砍在词中间比长一点难看得多。
- **翻译后 emoji 必须剥掉**：夹在句中的 emoji 会让译文断成「由⚡您构建」。
  翻译前剥会影响原意判断，所以是**译后剥**。
- **原文只在真翻过时附**：`if zh_body != body_en`，没翻就别贴两遍。
- **逐条发要限速**：条间隔 `push_gap_seconds`（默认 4 秒），否则 QQ 机器人会被限流。
