# DSH 插件 · 配置面板（dsh-radar-config）

DSH（DeepSeek Harness）Web 端的一个内置插件，作用只有一个：
**在设置页里远程操控上面那个 `community-radar` 仓库。**

它不是本服务本体 —— 本服务本体跑在 GitHub Actions 上。这个插件只是遥控器：

| 能力 | 走什么 |
|---|---|
| 读远端 `config.yaml` | `gh api repos/{repo}/contents/config.yaml` |
| 写远端 `config.yaml` | `gh api -X PUT .../contents/config.yaml` |
| 写密钥（cookie / token / 账号名） | `gh secret set`（值不上屏） |
| 手动跑一轮 | `gh workflow run radar-daily.yml` |
| 看运行状态 / 日志 | `gh run list` / `gh run view --log` |
| 看最新简报 | `gh api .../contents/` 或 本地 outbox 缓存 |

所以 **本地容器死了完全无所谓**：配置在仓库里，插件源码在这个目录里，
新容器里把它装回来，连上同一个仓库，一切复原。

---

## 装回新容器

```bash
# 1. 拿源码
git clone https://github.com/<owner>/community-radar /root/.dsh/plugin-src/community-radar
# 2. 软链进 profile
cd /root/.dsh/plugin-src/community-radar/dsh-plugin/radar-config
ln -sfn "$PWD" /root/.dsh/profiles/web/node_modules/dsh-radar-config
# 3. 在 profile 的 package.json 里登记（dependencies + bundles 各一行）
#    "dsh-radar-config": "link:/root/.dsh/profiles/web/node_modules/dsh-radar-config"
# 4. 重启宿主
```

---

## 依赖

- `gh` CLI 已登录（`gh auth status` 通过，需要 `repo` + `workflow` scope）
- Python 3 + PyYAML（读写 YAML，插件宿主半通过 `config_io.py` 调用）

## 认证

**不用另配 token** —— 直接复用 `gh` 的登录态（`/root/.config/gh/hosts.yml`）。
换账号或换仓库时，只需在插件 UI 里改 `repo` 字段。
