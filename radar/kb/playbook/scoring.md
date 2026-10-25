# 打分判据

目标：把「值得点开」的帖子挑出来，把水贴/广告扔掉。宁可少杀不可错杀。

## 星级换算

```
score → stars
>= 7.0 → 5★   立刻去看
>= 5.0 → 4★   值得一看
>= 3.5 → 3★   备着
>= 2.0 → 2★   仅存档
<  2.0 → 1★   丢
```

`min_stars: 3` → 只有 ≥3.5 分的才会被推送。每轮抓几百条，通常只剩个位数。

## 加分项（关键词组）

| 组 | 权重 | 例子 |
|---|---|---|
| 公益/免费 | 3 | 公益、免费、福利、羊毛、薅 |
| VPS/主机 | 3 | vps、小鸡、免费vps、甲骨文、az100 |
| 教程 | 2 | 教程、保姆级、一键、脚本、docker |
| 优惠/上车 | 2 | 优惠码、半价、年付、拼车 |
| 注册/额度 | 2 | 注册送、额度、api key、free credits |
| 活动/抽奖 | 2 | 抽奖、限时、giveaway、grand prize |
| 资源/节点 | 1 | 图床、cdn、oss、机场、中转 |

板块权重：`promotion` 2.5 / `info` 2.5 / `review` 2.0 / `tech` 1.5；
`meaningless` -1.0 / `expose` -1.5 / `photo-share` -1.0。

限时信号（「最后一天」「先到先得」）+1 分，且星级不足 5 时保底 +1 星。

## 直接丢（不进库）

三条规则（`junk_reason()`）：
1. **标题**含 drop 词（出售/收购/接单/加微信/dm me/buy now）→ 交易贴或广告
2. 板块属于 `drop_boards`（meaningless/photo-share/expose）→ 闲聊撕逼
3. 标题+摘要里 negative 词命中 ≥3 个 → 掺水太多

第 1 条**只看标题**：正文里顺嘴提一句「出售」不算，标题写了才是真在卖。

## 两个血泪教训

### 英文词必须加词边界

`_hit()` 对 ascii 词做 `(?<![a-z0-9])词(?![a-z0-9])` 包边。不加的话：

- `trial` 命中 `indus·trial`（industrial engineering）→ 6.5★ 误报
- `cloud` 命中 `Tenstorrent Cloud`（产品名）→ 所有相关推文都加分

加边界后：「industrial engineering」6.5★ → 3.5★，「Free VPS for 3 months」正确 7.5★。

### giveaway 不是广告

第一版把 `giveaway` 放进 `DROP_WORDS`，结果把「抽奖送 GPU」这种**正是用户要的**
内容当广告丢了。正确做法：`giveaway` / `promo code` / `referral` 进**正向组**
（活动/抽奖、注册/额度），不进 drop。

判据：**「拉人给好处」= 羊毛，「卖东西」= 广告。**
`dm me` / `check my bio` / `buy now` 是卖；`giveaway` / `free credits` / `referral code` 是薅。
