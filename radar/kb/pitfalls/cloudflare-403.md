现象: 云端 GitHub Actions 抓论坛/X 全部 403，本机同一个 URL 200。
根因: 目标站按 IP 段封数据中心（Azure/AWS/GCP）。本机走运营商 5G，不封。
修法: 找到「不封云的第三方代理端点」，而不是硬刚原站。

## 三个实测案例

| 目标 | 被封的入口 | 云端能用的入口 |
|---|---|---|
| x.com | `x.com/{user}` 403 | `api.fxtwitter.com/2/profile/{u}/statuses` 200 |
| linux.do | `/latest.json` 403（CF JS 挑战） | `/latest.rss` 200 |
| linux.sb | 没有 JSON API | `/daily_hot_topics_rss` 200 |

## 判断原则

1. **先分清是「CF JS 挑战」还是「IP 段封禁」**
   - CF 挑战：返回 7KB 左右的 `Just a moment...` HTML，有 `cf-ray` 头
   - IP 封禁：直接 403 + 空 body，或 5605B 的 X 错误页
2. **同一个站，不同端点待遇可能完全不同** —— linux.do 的 `.json` 死、`.rss` 活，
   这是 Discourse 的 RSS 没套 CF 规则。所以「换个端点」比「换代理」便宜。
3. **别急着上代理池**。本机能直连的站，云端不一定能；但往往存在一个
   「云端能用的公开端点」（fxtwitter、RSS），找到它就不用维护代理。
