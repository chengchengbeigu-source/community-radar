# 推送链路

## 通道优先级

`radar/util.py` 的 `_push()` 顺序：

1. **QQ 机器人**（`bots.qq.com/app/getAppAccessToken` → 发消息）
2. **本机通知栏**（`127.0.0.1:3090/app/notify`，只有手机上的 DSH 有）
3. **Telegram**（`RADAR_TG_BOT` / `RADAR_TG_CHAT` 两个环境变量都有才走）

任何一个返回成功就停。QQ 失败会带着真实错误往上抛，不会假装「没后端」。

## 去重

`_notify_fp(title, text)` 算指纹，`_notify_seen()` 查 24 小时内发过没。
同一条推文跨账号转推也只会发一次（指纹基于标题+正文，不是 URL）。

## 限速

`push_gap_seconds`（默认 4 秒）—— 逐条发时条间隔。QQ 机器人有速率限制，
连发 10 条不间隔会被静默丢。

## 翻译

`radar/translate.py` 走 MyMemory（免费、无 key、HTTPS）。

为什么不用 LLM：
- 本机 CPT 池（8317）走 IPv6 出网，googleapis 超时
- OrcaTerm 本地桥（8899）上游 `VERIFY_SKEY_UIN_FAILED`
- 所有公益端点都要 key，且各有各的怪

三条硬约束（`translate.py` 顶部注释）：
1. 有 CJK 就不翻（`needed()` 先判断），省请求
2. 单次上限 480 字符，按句子切块（超了 MyMemory 返回 414）
3. **任何失败都返回原文** —— 翻译是锦上添花，不能因为它把推送弄丢

## emoji 处理

**译后剥，不是译前剥**。译前剥会影响 MyMemory 对句意的判断；
译后剥是因为夹在句中的 emoji 会把译文断成「由⚡您构建」这种。

`strip_emoji()` 覆盖：符号象形（1F300-1FAFF）、杂项符号（2600-27BF）、
区域指示符（国旗）、箭头（2190-21FF）、变体选择符（FE0F）、零宽连接符（200D）。
