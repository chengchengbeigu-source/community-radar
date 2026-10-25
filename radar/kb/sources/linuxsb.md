# linux.sb（烧饼社区）

现象: 找不到任何 JSON API，`/latest.json` 是 404，前端 index.js 里一条 API 路径都没有。
根因: 服务端渲染的 PHP（不是 Discourse，也不是 SPA）。数据直接印在 HTML 里。
修法: 两条路 —— 热帖走 RSS，板块列表走 `/forum/{id}` 的 HTML 正则。

## 站点结构（实测 2026-10-26）

首页 HTML 里就印着板块链接：

| id | 名称 | 价值 |
|---|---|---|
| 1 | 错误地方 | 低（闲聊/水区） |
| **2** | **福利放送** | **高 —— 羊毛主力** |
| 3 | 资源分享 | 中 |
| **4** | **技术交流** | **高 —— 干货** |
| 5 | 求助问答 | 中 |
| 6 | 社区治理 | 低 |
| 7 | 消息 | 抓不到（需登录） |
| 8 | 我要推广 | 低（广告） |
| 9 | 社区公告 | 低 |
| 10 | 大禹治水 | 低 |

## 可用端点

| 端点 | 状态 | 大小 | 说明 |
|---|---|---|---|
| `/daily_hot_topics_rss` | **200** | 20KB | 24h 热帖，**description 带全文 HTML** |
| `/daily_hot_topics_rss?period=36h` | 200 | — | 36 小时档 |
| `/daily_hot_topics_rss?period=week` | 200 | — | 周榜 |
| `/forum/{1..10}` | **200** | ~160KB | 板块页，含全部 topic 链接 |
| `/rss` | 404 | — | 没有通用 RSS |
| `/latest.json` | 404 | — | 没有 JSON API |

## 解析

RSS 那条最省事，`description` 里带 `<div class="nb-editor-post-content">` 完整正文：

```
<item>
  <title>dsh ds4.1 最简单破甲焚决</title>
  <link>https://linux.sb/topic/27844</link>
  <description>&lt;div class="post-readability-content"&gt;…</description>
</item>
```

板块页抠链接（注意**三个捕获组**）：

```python
re.findall(r'<a[^>]+href="(/topic/(\d+))"[^>]*>(.*?)</a>', html, re.S)
#          整路径          topic号        内层HTML
```

## 坑

- 板块页同一条 topic 会重复出现多次（分页导航/相关推荐都带链接），**必须按 href 去重**。
- `/forum/7`（消息）只有 61KB、没帖子 —— 需登录，别抓。
- 正则的捕获组数量必须和解包数量对齐，`for href, inner in findall(三组)` 会
  `ValueError: too many values to unpack`。这个错 2026-10-26 实际踩过。
