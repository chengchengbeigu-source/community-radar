# linux.do

现象: `/latest.json` 一律 403，返回 Cloudflare 的 "Just a moment..."（7KB HTML）。
根因: linux.do 的 JSON 端点挂了 Cloudflare JS 挑战；RSS 端点没有。
修法: 抓 `/latest.rss` 和 `/top.rss?period=daily`，两者都是 200 且无需鉴权。

## 实测数据（2026-10-26）

| 端点 | 状态 | 大小 | 条数 |
|---|---|---|---|
| `/latest.json` | 403 | 7.1KB（挑战页） | 0 |
| `/latest.rss` | **200** | 88KB | 30 |
| `/top.rss?period=daily` | **200** | 375KB | 30 |
| `connect.linux.do` | 403 | — | — |

UA 无关：浏览器 UA 和 curl 默认 UA 都返回 200。区别只在 `.json` vs `.rss`。

## 解析

Discourse 的 RSS 结构规整，正则足够（不必引 feedparser）：

```
<item>
  <title>…</title>
  <link>https://linux.do/t/topic/3074224</link>
  <category>搞七捻三</category>
  <pubDate>Sun, 25 Oct 2026 19:31:59 +0000</pubDate>
  <description>…HTML…</description>
</item>
```

topic id 从 `<link>` 里 `re.search(r"/t/topic/(\d+)")` 抠。

## 板块分布（实测 30 条的样子）

「搞七捻三」（闲聊）占一半以上，`开发调优` / `福利羊毛` / `资源分享` 才是干货。
默认 `keep_boards` 保留：开发调优、资源分享、福利、技术、教程、分享、搞七捻三
（搞七捻三也留 —— 羊毛帖经常发在闲聊区，比如「OpenCode 免费渠道都 429 了」）。

## 坑

- **别用 `/latest.json`**，那是死路。第一反应想用 JSON API 就会撞 403。
- `top.rss` 体积 375KB（省流量的场景可以只要 `latest.rss`）。
- RSS 只给 30 条，翻页用 `?page=N`；`/latest.rss?page=1` 实测可用。
