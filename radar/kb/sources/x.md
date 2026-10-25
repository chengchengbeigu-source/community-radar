# X（推特）

## 云端 403 是核心问题

现象: GitHub Actions 里抓 `https://x.com/{user}` → **403 / 5605B**，15 个账号全挂。
根因: X 封 Azure 数据中心 IP 段。本机（手机 5G 出口）同一个 URL 是 200 / 143KB。
修法: **主路换 fxtwitter 的 JSON API** —— `api.fxtwitter.com` 走的是自己的缓存/代理，不封云 IP。

## 三条路对比（实测 2026-10-26）

| 路径 | 本机 | 云端 Actions | 条数/次 |
|---|---|---|---|
| `x.com/{user}` SSR HTML | 200 / 143KB | **403** | 5~9 |
| `cdn.syndication.twimg.com/tweet-result?id=` | 200 | 200（但只有单条） | 1 |
| **`api.fxtwitter.com/2/profile/{user}/statuses`** | **200 / 31KB** | **200** | **14~20** |

兜底顺序：fxtwitter → x.com HTML → syndication。实测云端 fxtwitter 成功 14/15
（`@xai` 失败，转兜底也 403，跳过）。单账号失败不影响整体。

## fxtwitter 响应结构

```json
{"code":200,"results":[{"id":"…","text":"…","created_at":"…",
  "author":{"screen_name":"…","name":"…"},
  "is_rt":true,"retweeted_tweet":{…}}],
 "cursor":{"bottom":"…"}}
```

**翻页**用 `cursor.bottom` 作为 `?cursor=` 参数（不传就只给第一页约 20 条）。

## 坑

- **转推的 author 字段别取错**：`t.screen_name` 才是真正写这句话的人，
  盯的账号只是转发者。2026-10-26 用户投诉：标题写 `@MiniMax_AI`、链接指向
  `SandbaseAI` —— 就是这里取了「盯的账号」。已在 `sources/x.py` 改成实际作者优先，
  转推标题加 `[转推 @xxx]` 前缀。
- `x.com` 页面抠 ID 的正则是 `/status/(\d{15,20})`（19 位，别写 `\d+` 太宽会捞到别的）。
- 已见 ID 缓存（`radar/data/x_seen.json`）能省 80%+ 请求：热轮从 96 次降到 15 次、29 秒跑完。
