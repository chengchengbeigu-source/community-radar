(() => {
  try {
    window.__ModuleLoader__.load({
      id: "dsh-radar-config",
      factory: (require) => {
        var module = { exports: {} };
        var exports = module.exports;
        Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
        var react = require("react");
        var API = "/radar-config/api";
        var h = react.createElement;

        var S = {
          wrap: { padding: "14px 4px 24px", display: "flex", flexDirection: "column", gap: "14px",
                  fontSize: "13px", lineHeight: "1.55" },
          card: { border: "1px solid rgba(127,127,127,.22)", borderRadius: "12px", padding: "12px 12px 14px",
                  display: "flex", flexDirection: "column", gap: "10px", background: "rgba(127,127,127,.06)" },
          h2: { fontSize: "13px", fontWeight: 650, display: "flex", alignItems: "center", gap: "6px" },
          hint: { fontSize: "11.5px", opacity: 0.62 },
          row: { display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" },
          lab: { fontSize: "12px", opacity: 0.7, minWidth: "58px" },
          inp: { flex: "1 1 190px", minWidth: "130px", padding: "8px 10px", borderRadius: "8px",
                 border: "1px solid rgba(127,127,127,.35)", background: "transparent", color: "inherit",
                 fontSize: "12.5px", fontFamily: "inherit" },
          inpSm: { flex: "0 1 118px", minWidth: "92px" },
          sel: { padding: "8px 10px", borderRadius: "8px", border: "1px solid rgba(127,127,127,.35)",
                 background: "transparent", color: "inherit", fontSize: "12.5px", fontFamily: "inherit" },
          btn: { padding: "8px 14px", borderRadius: "8px", cursor: "pointer", fontSize: "12.5px",
                 border: "1px solid rgba(127,127,127,.4)", background: "transparent", color: "inherit", fontFamily: "inherit" },
          btnP: { padding: "8px 16px", borderRadius: "8px", cursor: "pointer", fontSize: "12.5px", fontWeight: 600,
                  border: "1px solid rgba(16,185,129,.5)", background: "rgba(16,185,129,.16)", color: "inherit", fontFamily: "inherit" },
          btnD: { padding: "6px 10px", borderRadius: "8px", cursor: "pointer", fontSize: "12px",
                  border: "1px solid rgba(239,68,68,.35)", background: "transparent", color: "#ef4444", fontFamily: "inherit" },
          site: { border: "1px dashed rgba(127,127,127,.32)", borderRadius: "10px", padding: "10px",
                  display: "flex", flexDirection: "column", gap: "8px" },
          mono: { fontFamily: "ui-monospace, SFMono-Regular, monospace", fontSize: "11px", whiteSpace: "pre-wrap",
                  wordBreak: "break-all", background: "rgba(127,127,127,.12)", padding: "8px 10px",
                  borderRadius: "8px", maxHeight: "220px", overflow: "auto" },
          monoBig: { fontFamily: "ui-monospace, SFMono-Regular, monospace", fontSize: "10.5px", whiteSpace: "pre-wrap",
                     wordBreak: "break-all", background: "rgba(127,127,127,.12)", padding: "8px 10px",
                     borderRadius: "8px", maxHeight: "340px", overflow: "auto" },
          ok: { fontSize: "12.5px", color: "#10b981" },
          err: { fontSize: "12.5px", color: "#ef4444", whiteSpace: "pre-wrap" },
          tabOn: { padding: "7px 14px", borderRadius: "8px", cursor: "pointer", fontSize: "12.5px", fontWeight: 600,
                   border: "1px solid rgba(16,185,129,.5)", background: "rgba(16,185,129,.16)", color: "inherit", fontFamily: "inherit" },
          tabOff: { padding: "7px 14px", borderRadius: "8px", cursor: "pointer", fontSize: "12.5px",
                    border: "1px solid rgba(127,127,127,.3)", background: "transparent", color: "inherit",
                    opacity: 0.7, fontFamily: "inherit" },
          run: { display: "flex", alignItems: "center", gap: "8px", padding: "7px 9px", borderRadius: "8px",
                 border: "1px solid rgba(127,127,127,.2)", fontSize: "11.5px" },
          pill: { fontSize: "11px", padding: "1px 7px", borderRadius: "999px",
                  border: "1px solid rgba(127,127,127,.35)" }
        };
        function txt(v) { return v === undefined || v === null ? "" : String(v); }

        function Field(props) {
          return h("div", { style: S.row },
            h("span", { style: S.lab }, props.label),
            h("input", { style: S.inp, value: txt(props.value), placeholder: props.ph || "",
                         onChange: (e) => props.onChange(e.target.value) }));
        }

        function Note(props) {
          return h("div", { style: S.hint }, props.children);
        }

        function runColor(r) {
          if (r.status === "in_progress" || r.status === "queued") return "#f59e0b";
          if (r.conclusion === "success") return "#10b981";
          if (r.conclusion === "cancelled") return "rgba(127,127,127,.8)";
          return "#ef4444";
        }

        function RadarConfig() {
          var [st, set] = react.useState({
            loading: true, mode: "remote", cfg: null, digest: null, running: false,
            log: "", msg: null, err: null, probe: null, testUrl: "",
            remote: { repo: "", user: "", runs: [], secrets: [], ok: false, error: null },
            repoDraft: "", wfDraft: "radar-daily.yml", branchDraft: "main",
            logId: null, logText: "", logTitle: "",
            secretName: "", secretValue: ""
          });
          function patch(p) { set((s) => Object.assign({}, s, p)); }

          function applyRemote(j) {
            var r = j || {};
            patch({ remote: r, repoDraft: r.repo || "", cfg: r.config || {},
                    wfDraft: r.workflow || "radar-daily.yml", branchDraft: r.branch || "main" });
          }

          function load(mode, quiet) {
            var m = mode || st.mode;
            if (m === "remote") {
              if (!quiet) patch({ msg: "读仓库…", err: null });
              fetch(API + "/remote/state").then((r) => r.json()).then((j) => {
                applyRemote(j);
                patch({ loading: false, msg: null, err: j.ok ? null : (j.error || "读远端失败") });
              }).catch((e) => patch({ loading: false, msg: null, err: String(e) }));
            } else {
              if (!quiet) patch({ msg: "读取中…", err: null });
              fetch(API + "/state").then((r) => r.json()).then((j) => {
                patch({ loading: false, cfg: j.config || {}, digest: j.digest, running: !!j.running,
                        log: j.log || "", proxyFile: j.proxyFile, msg: null,
                        err: j.ok ? null : (j.error || "读配置失败") });
              }).catch((e) => patch({ loading: false, msg: null, err: String(e) }));
            }
          }

          react.useEffect(() => {
            fetch(API + "/remote/state").then((r) => r.json()).then((j) => {
              var m = j && j.repo ? "remote" : "local";
              patch({ mode: m, remote: j || {} });
              load(m);
            }).catch(() => load("local"));
          }, []);

          react.useEffect(() => {
            var t = setInterval(() => {
              if (st.mode === "remote") {
                fetch(API + "/remote/state").then((r) => r.json()).then((j) => {
                  if (j && j.runs) patch({ remote: j });
                }).catch(() => {});
              } else {
                fetch(API + "/state").then((r) => r.json()).then((j) =>
                  patch({ running: !!j.running, log: j.log || "", digest: j.digest })).catch(() => {});
              }
            }, 15000);
            return () => clearInterval(t);
          }, [st.mode]);

          var cfg = st.cfg || {};
          function setCfg(path, val) {
            var c = JSON.parse(JSON.stringify(cfg));
            var cur = c;
            for (var i = 0; i < path.length - 1; i++) {
              if (!cur[path[i]] || typeof cur[path[i]] !== "object") cur[path[i]] = {};
              cur = cur[path[i]];
            }
            cur[path[path.length - 1]] = val;
            patch({ cfg: c, msg: null, err: null });
          }
          function ciList() { return JSON.parse(JSON.stringify(cfg.checkins || [])); }
          function setCheckin(i, kv) {
            var a = ciList(); a[i] = Object.assign({}, a[i], kv);
            patch({ cfg: Object.assign({}, cfg, { checkins: a }), msg: null, err: null });
          }
          function addCheckin() {
            var a = ciList();
            a.push({ name: "", url: "", kind: "newapi", enabled: true, jitter_minutes: 0, proxy: "direct" });
            patch({ cfg: Object.assign({}, cfg, { checkins: a }), msg: null });
          }
          function delCheckin(i) {
            var a = ciList(); a.splice(i, 1);
            patch({ cfg: Object.assign({}, cfg, { checkins: a }), msg: null });
          }

          function rememberRepo() {
            patch({ msg: "记仓库…", err: null });
            fetch(API + "/remote/repo", { method: "POST", headers: { "content-type": "application/json" },
              body: JSON.stringify({ repo: st.repoDraft.trim(), workflow: st.wfDraft.trim(),
                                     branch: st.branchDraft.trim() }) }).then((r) => r.json()).then((j) => {
              if (!j.ok) { patch({ msg: null, err: j.error || "记住失败" }); return; }
              patch({ mode: "remote", msg: "已记住 " + j.repo, err: null });
              load("remote");
            }).catch((e) => patch({ msg: null, err: String(e) }));
          }

          function save() {
            var remoteMode = st.mode === "remote";
            patch({ msg: remoteMode ? "推到仓库…" : "保存中…", err: null });
            var url = remoteMode ? API + "/remote/save" : API + "/save";
            var body = remoteMode ? { repo: st.remote.repo, config: cfg } : { config: cfg };
            fetch(url, { method: "POST", headers: { "content-type": "application/json" },
              body: JSON.stringify(body) }).then((r) => r.json()).then((j) => {
              if (!j.ok) { patch({ msg: null, err: j.error || "保存失败" }); return; }
              if (remoteMode) {
                var rm = (j.removed || []).length;
                patch({ msg: "已推到仓库 ✅ commit " + txt(j.commit).slice(0, 8) +
                       (rm ? "；顺手清空了 " + rm + " 个敏感字段（真值请用下面「密钥」区灌 Secrets）" : ""), err: null });
                load("remote", true);
              } else {
                patch({ msg: "已保存 ✅（旧文件留了 config.yaml.bak）", err: null });
              }
            }).catch((e) => patch({ msg: null, err: String(e) }));
          }

          function probe() {
            patch({ msg: "探测中…", err: null, probe: null });
            fetch(API + "/probe", { method: "POST", headers: { "content-type": "application/json" },
              body: JSON.stringify({ url: st.testUrl || "https://api.ipify.org" }) }).then((r) => r.json()).then((j) =>
              patch({ msg: null, probe: j, err: j.ok ? null : (j.error || "探测失败") }))
              .catch((e) => patch({ msg: null, err: String(e) }));
          }

          function run() {
            if (st.mode === "remote") {
              patch({ msg: "已让云端起一轮（30~90 秒出结果）…", err: null });
              fetch(API + "/remote/run", { method: "POST", headers: { "content-type": "application/json" },
                body: JSON.stringify({ repo: st.remote.repo, workflow: st.wfDraft, branch: st.branchDraft,
                                       inputs: { read: "1" } }) }).then((r) => r.json()).then((j) => {
                if (!j.ok) { patch({ msg: null, err: j.error || "触发失败" }); return; }
                patch({ msg: "云端已排队，稍等十几秒点「累计新状态」看结果", err: null });
                setTimeout(() => load("remote", true), 6000);
              }).catch((e) => patch({ msg: null, err: String(e) }));
            } else {
              patch({ msg: "已起一轮本服务采集（后台跑，10~60 秒）…", err: null });
              fetch(API + "/run", { method: "POST", headers: { "content-type": "application/json" },
                body: JSON.stringify({ args: ["run"] }) }).then((r) => r.json()).then((j) =>
                patch({ running: !!j.ok, msg: j.ok ? "采集已启动，日志见下" : null,
                        err: j.ok ? null : (j.error || "启动失败") }))
                .catch((e) => patch({ msg: null, err: String(e) }));
            }
          }

          function openLog(r) {
            patch({ logId: r.databaseId, logText: "拉日志中…", logTitle: "#" + r.databaseId });
            fetch(API + "/remote/log?repo=" + encodeURIComponent(st.remote.repo) + "&id=" + r.databaseId)
              .then((x) => x.json()).then((j) =>
                patch({ logText: j.ok ? (j.log || "(空)") : ("拉失败：" + (j.error || "")) }))
              .catch((e) => patch({ logText: String(e) }));
          }

          function putSecret() {
            var n = st.secretName.trim();
            if (!n || !st.secretValue) { patch({ err: "密钥名和值都要填", msg: null }); return; }
            patch({ msg: "灌密钥 " + n + "…", err: null });
            fetch(API + "/remote/secret", { method: "POST", headers: { "content-type": "application/json" },
              body: JSON.stringify({ repo: st.remote.repo, name: n, value: st.secretValue }) })
              .then((r) => r.json()).then((j) => {
                if (!j.ok) { patch({ msg: null, err: j.error || "灌密钥失败" }); return; }
                patch({ msg: "已灌 " + j.name + "（" + j.bytes + " 字节，值不回显）",
                        err: null, secretName: "", secretValue: "" });
                load("remote", true);
              }).catch((e) => patch({ msg: null, err: String(e) }));
          }

          var kids = [];
          if (st.loading) { kids.push(h("div", { key: "l", style: S.hint }, "读取中…")); }
          else {
            var sources = cfg.sources || {};
            var ci = cfg.checkins || [];
            var srcOn = Object.keys(sources).filter((k) => sources[k].enabled !== false).length;
            var isRemote = st.mode === "remote";

            // ⓪ 模式 + 仓库
            kids.push(h("div", { key: "mode", style: S.card },
              h("div", { style: S.h2 }, "运行位置"),
              h("div", { style: S.row },
                h("button", { style: isRemote ? S.tabOn : S.tabOff,
                              onClick: () => { patch({ mode: "remote" }); load("remote"); } }, "☁️ 云端仓库"),
                h("button", { style: isRemote ? S.tabOff : S.tabOn,
                              onClick: () => { patch({ mode: "local" }); load("local"); } }, "📱 本机容器")),
              h(Note, null, isRemote
                ? "改的就是 GitHub 上那份 config.yaml，保存即提交；容器死了也不影响云端照跑。"
                : "改的是本机 /sdcard/Download/DSH工作区/radar/config.yaml，跑一轮也只在本机跑。"),
              isRemote ? h("div", null,
                h("div", { style: S.row },
                  h("span", { style: S.lab }, "仓库"),
                  h("input", { style: S.inp, value: st.repoDraft, placeholder: "owner/name",
                               onChange: (e) => patch({ repoDraft: e.target.value }) }),
                  h("button", { style: S.btn, onClick: rememberRepo }, "记住")),
                h("div", { style: S.row },
                  h("span", { style: S.lab }, "工作流"),
                  h("input", { style: S.inpSm, value: st.wfDraft, placeholder: "radar-daily.yml",
                               onChange: (e) => patch({ wfDraft: e.target.value }) }),
                  h("span", { style: S.hint }, "分支"),
                  h("input", { style: S.inpSm, value: st.branchDraft, placeholder: "main",
                               onChange: (e) => patch({ branchDraft: e.target.value }) }),
                  h("span", { style: Object.assign({}, S.pill, { color: st.remote.auth ? "#10b981" : "#ef4444" }) },
                    st.remote.auth ? ("gh: " + st.remote.user) : "gh 未登录")),
                h(Note, null, "认证直接复用本机 gh 的登录态，不用另配 token。")) : null));

            // ① 概览
            kids.push(h("div", { key: "ov", style: S.card },
              h("div", { style: S.row },
                h("span", { style: { fontSize: "14px", fontWeight: 700 } }, "社区内容本服务"),
                h("span", { style: S.hint }, "已启用 " + srcOn + " 个源 · " + ci.length + " 个例行同步站"),
                h("span", { style: Object.assign({}, S.hint, { color: (!isRemote && st.running) ? "#f59e0b" : "#10b981" }) },
                  isRemote ? (st.remote.repo ? "☁️ " + st.remote.repo : "☁️ 未设置仓库")
                           : (st.running ? "● 正在采集" : "● 空闲"))),
              (!isRemote && st.digest && !st.digest.error) ? h("div", { style: S.hint },
                "最新简报：" + st.digest.name + " · " + st.digest.lines + " 行 · " +
                Math.round(st.digest.size / 1024) + "KB") : null,
              h("div", { style: S.row },
                h("button", { style: S.btn, onClick: () => load(st.mode) }, "重新读取"),
                h("button", { style: S.btnP, onClick: run }, isRemote ? "☁️ 云端跑一轮" : "跑一轮本服务"))));

            // ② 代理
            kids.push(h("div", { key: "px", style: S.card },
              h("div", { style: S.h2 }, "代理"),
              h(Field, { label: "全局代理", value: cfg.proxy, ph: "http://127.0.0.1:7899",
                         onChange: (v) => setCfg(["proxy"], v) }),
              h(Note, null, isRemote
                ? "云端在 GitHub 机房里跑，没有你本机的 7899，这里一般留空或写 direct（直连）。"
                : "留空 = 用 radar/proxy.txt" + (st.proxyFile ? "（现在是 " + st.proxyFile + "）" : "（现在没写）") +
                  "；填 direct = 直连。每个源、每个例行同步站都能单独覆盖。"),
              !isRemote ? h("div", { style: S.row },
                h("input", { style: S.inp, value: st.testUrl, placeholder: "测一条 URL（默认 api.ipify.org）",
                             onChange: (e) => patch({ testUrl: e.target.value }) }),
                h("button", { style: S.btn, onClick: probe }, "测这条")) : null));

            // ③ 源
            kids.push(h("div", { key: "src", style: S.card },
              h("div", { style: S.h2 }, "采集源"),
              Object.keys(sources).map((k, i) => {
                var s = sources[k] || {};
                return h("div", { key: "s" + i, style: S.row },
                  h("input", { type: "checkbox", checked: s.enabled !== false,
                               onChange: (e) => setCfg(["sources", k, "enabled"], e.target.checked) }),
                  h("span", { style: { minWidth: "76px", fontWeight: 600 } }, k),
                  h("input", { style: S.inp, value: txt(s.proxy), placeholder: "代理：auto / direct / http://…",
                               onChange: (e) => setCfg(["sources", k, "proxy"], e.target.value) }),
                  h("span", { style: S.hint }, ((s.listings || s.boards || []).length || 0) + " 页"));
              }),
              h(Note, null, "source-b 有 Cloudflare 挑战：本地走代理 + 干净节点出口；云端机房直连反而稳。")));

            // ④ 例行同步站
            kids.push(h("div", { key: "ck", style: S.card },
              h("div", { style: S.h2 }, "例行同步站（" + ci.length + "）"),
              ci.length ? null : h(Note, null, "还没有例行同步站，点下面「+ 加一个」开始。"),
              ci.map((it, i) => h("div", { key: "c" + i, style: S.site },
                h("div", { style: S.row },
                  h("input", { type: "checkbox", checked: it.enabled !== false,
                               onChange: (e) => setCheckin(i, { enabled: e.target.checked }) }),
                  h("input", { style: S.inpSm, value: txt(it.name), placeholder: "名字",
                               onChange: (e) => setCheckin(i, { name: e.target.value }) }),
                  h("select", { style: S.sel, value: it.kind || (it.access_token ? "newapi" : "simple"),
                                onChange: (e) => setCheckin(i, { kind: e.target.value }) },
                    h("option", { value: "newapi" }, "New API 面板（例行同步接口）"),
                    h("option", { value: "simple" }, "普通站点（GET + cookie）"),
                    h("option", { value: "discourse" }, "站点 上游站点（例行同步 + 在线时长）")),
                  h("span", { style: S.hint }, "代理"),
                  h("input", { style: S.inpSm, value: txt(it.proxy), placeholder: "direct",
                               onChange: (e) => setCheckin(i, { proxy: e.target.value }) }),
                  h("button", { style: S.btnD, onClick: () => delCheckin(i) }, "删")),
                h("div", { style: S.row },
                  h("span", { style: S.lab }, "站点/URL"),
                  h("input", { style: S.inp, value: txt(it.url), placeholder: "https://api.example.com/profile",
                               onChange: (e) => setCheckin(i, { url: e.target.value }) })),
                (it.kind || (it.access_token ? "newapi" : "simple")) === "newapi" ? h("div", { style: S.row },
                  h("span", { style: S.lab }, "访问令牌"),
                  h("input", { style: S.inp, value: txt(it.access_token),
                               placeholder: isRemote ? "（云端走 Secrets，这里留空）" : "面板 → 个人设置 → 访问令牌",
                               onChange: (e) => setCheckin(i, { access_token: e.target.value.trim() }) })) : h("div", { style: S.row },
                  h("span", { style: S.lab }, "Cookie"),
                  h("input", { style: S.inp, value: txt(it.cookie), placeholder: "整串 Cookie（或写 cookie_file 路径）",
                               onChange: (e) => setCheckin(i, { cookie: e.target.value }) })),
                (it.kind || (it.access_token ? "newapi" : "simple")) === "newapi" ? h("div", { style: S.row },
                  h("span", { style: S.lab }, "用户 ID"),
                  h("input", { style: S.inpSm, value: txt(it.user_id), placeholder: "New-Api-User",
                               onChange: (e) => setCheckin(i, { user_id: e.target.value.trim() }) }),
                  h("span", { style: S.hint }, "没令牌就走账号密码登录："),
                  h("input", { style: S.inpSm, value: txt(it.username), placeholder: "账号",
                               onChange: (e) => setCheckin(i, { username: e.target.value }) }),
                  h("input", { style: S.inpSm, type: "password", value: txt(it.password), placeholder: "密码",
                               onChange: (e) => setCheckin(i, { password: e.target.value }) })) : null,
                (it.kind === "discourse") ? h("div", null,
                  h("div", { style: S.row },
                    h("span", { style: S.lab }, "站点地址"),
                    h("input", { style: S.inp, value: txt(it.base || it.url), placeholder: "https://www.nodeloc.com",
                                 onChange: (e) => setCheckin(i, { base: e.target.value.trim(), url: e.target.value.trim() }) })),
                  h("div", { style: S.row },
                    h("span", { style: S.lab }, "用户名"),
                    h("input", { style: S.inpSm, value: txt(it.username), placeholder: "站点用户名",
                                 onChange: (e) => setCheckin(i, { username: e.target.value.trim() }) }),
                    h("span", { style: S.hint }, isRemote ? "云端由 NODELOC_USER 这个 Secret 灌进来"
                                                          : "Cookie 也可以写进 radar/secrets/source-a.cookie")),
                  h("div", { style: S.row },
                    h("span", { style: S.lab }, "Cookie 文件"),
                    h("input", { style: S.inp, value: txt(it.cookie_file), placeholder: "radar/secrets/nodeloc.cookie",
                                 onChange: (e) => setCheckin(i, { cookie_file: e.target.value.trim() }) })),
                  h("div", { style: S.row },
                    h("label", { style: S.lab }, h("input", { type: "checkbox", checked: (it.daily || {}).checkin !== false,
                        onChange: (e) => setCheckin(i, { daily: Object.assign({}, it.daily, { checkin: e.target.checked }) }) }), " 每日例行同步"),
                    h("label", { style: S.lab }, h("input", { type: "checkbox", checked: (it.daily || {}).read !== false,
                        onChange: (e) => setCheckin(i, { daily: Object.assign({}, it.daily, { read: e.target.checked }) }) }), " 累计在线时长")),
                  h("div", { style: S.row },
                    h("span", { style: S.lab }, "阅读上限"),
                    h("input", { style: S.inpSm, value: txt((it.daily || {}).read_minutes), placeholder: "20",
                                 onChange: (e) => setCheckin(i, { daily: Object.assign({}, it.daily, { read_minutes: Number(e.target.value) || 0 }) }) }),
                    h("span", { style: S.hint }, "分钟/天　停留"),
                    h("input", { style: S.inpSm, value: txt(((it.daily || {}).dwell_s || []).join(",")), placeholder: "45,150",
                                 onChange: (e) => setCheckin(i, { daily: Object.assign({}, it.daily, { dwell_s: e.target.value.split(",").map(Number).filter(function (x) { return x > 0; }) }) }) }),
                    h("span", { style: S.hint }, "秒（随机区间）")),
                  h("div", { style: S.row },
                    h("span", { style: S.lab }, "升到就停"),
                    h("input", { style: S.inpSm, value: txt((it.stop_at || {}).trust_level), placeholder: "1",
                                 onChange: (e) => setCheckin(i, { stop_at: Object.assign({}, it.stop_at, { trust_level: Number(e.target.value) }) }) }),
                    h("span", { style: S.hint }, "信任等级 0 青铜 1 白银 2 黄金 3 钻石，到这就自动停机并通知")),
                  h("div", { style: S.row },
                    h("span", { style: S.lab }, "活跃时段"),
                    h("input", { style: S.inpSm, value: txt(((it.daily || {}).active_hours || []).join(",")), placeholder: "8,23",
                                 onChange: (e) => setCheckin(i, { daily: Object.assign({}, it.daily, { active_hours: e.target.value.split(",").map(Number).filter(function (x) { return !isNaN(x); }) }) }) }),
                    h("span", { style: S.hint }, "只在这个钟点区间跑（风控）"))) : null,
                h("div", { style: S.hint }, "New API 站（如panel-a）：例行同步 = POST 站点根 /api/user/checkin；这类站别走代理，会被 Cloudflare 挡成 403。"))),
              h("div", { style: S.row },
                h("button", { style: S.btn, onClick: addCheckin }, "+ 加一个例行同步站"),
                h("span", { style: S.hint }, "填好先保存，再告诉我，我逐站实测"))));

            // ⑤ 保存与结果
            kids.push(h("div", { key: "ac", style: S.card },
              h("div", { style: S.h2 }, isRemote ? "推到仓库" : "保存与结果"),
              h("div", { style: S.row },
                h("button", { style: S.btnP, onClick: save }, isRemote ? "☁️ 保存到仓库" : "保存配置"),
                h("span", { style: S.hint }, isRemote
                  ? "打上 GitHub commit；敏感字段（token / user_id / username / cookie / 密码）保存前会被自动清空"
                  : "写入 radar/config.yaml（自动留一份 .bak）"))));

            // ⑥ 远程专属：运行历史
            if (isRemote) {
              var runs = st.remote.runs || [];
              kids.push(h("div", { key: "runs", style: S.card },
                h("div", { style: S.row },
                  h("div", { style: S.h2 }, "云端运行（最近 " + runs.length + " 次）"),
                  h("button", { style: S.btn, onClick: () => load("remote", true) }, "累计新状态")),
                runs.length ? null : h(Note, null, st.remote.repo ? "还没有运行记录。" : "先把仓库填上并点「记住」。"),
                runs.map((r, i) => h("div", { key: "r" + i, style: S.run },
                  h("span", { style: { color: runColor(r), fontWeight: 600, minWidth: "72px" } },
                    r.status === "in_progress" ? "运行中" : (r.status === "queued" ? "排队中" : (r.conclusion || "?"))),
                  h("span", { style: { flex: "1 1 120px" } }, txt(r.createdAt).replace("T", " ").replace("Z", "")),
                  h("span", { style: S.hint }, "#" + r.databaseId),
                  h("button", { style: S.btn, onClick: () => openLog(r) }, "看日志")))));
            }

            // ⑦ 远程专属：密钥
            if (isRemote) {
              var secs = st.remote.secrets || [];
              kids.push(h("div", { key: "sec", style: S.card },
                h("div", { style: S.h2 }, "仓库密钥（Secrets）"),
                h(Note, null, "真值只存在 GitHub 加密 Secrets 里，仓库文件里永远是空的。灌进去不会回显，只会显示字节数。"),
                h("div", { style: S.row },
                  h("span", { style: S.hint }, "已有：" + (secs.length ? secs.map(function (s) { return s.name; }).join(" · ") : "（无）"))),
                h("div", { style: S.row },
                  h("input", { style: S.inpSm, value: st.secretName, placeholder: "NODELOC_COOKIE",
                               onChange: (e) => patch({ secretName: e.target.value }) }),
                  h("input", { style: S.inp, type: "password", value: st.secretValue, placeholder: "值（粘贴，不回显）",
                               onChange: (e) => patch({ secretValue: e.target.value }) }),
                  h("button", { style: S.btnP, onClick: putSecret }, "灌进去")),
                h(Note, null, "这个工作流用到的：NODELOC_COOKIE · NODELOC_USER · XSG_TOKEN · XSG_UID。TG 推送另需 RADAR_TG_BOT / RADAR_TG_CHAT，没配就静默跳过。")));
            }

            if (st.msg) kids.push(h("div", { key: "m", style: S.ok }, st.msg));
            if (st.err) kids.push(h("div", { key: "e", style: S.err }, st.err));
            if (st.probe) kids.push(h("div", { key: "pr", style: S.card },
              h("div", { style: S.h2 }, "探测结果"),
              h("div", { style: S.mono }, JSON.stringify(st.probe, null, 1))));
            if (st.logId) kids.push(h("details", { key: "rlog", style: S.card, open: true },
              h("summary", { style: S.h2 }, "云端日志 " + st.logTitle),
              h("div", { style: S.monoBig }, st.logText)));
            if (!isRemote && st.digest && !st.digest.error) kids.push(h("details", { key: "d", style: S.card },
              h("summary", { style: S.h2 }, "最新简报 " + st.digest.name + " · " + st.digest.lines + " 行"),
              h("div", { style: S.mono }, txt(st.digest.head))));
            if (!isRemote && st.log) kids.push(h("details", { key: "lg", style: S.card },
              h("summary", { style: S.h2 }, "本服务运行日志" + (st.running ? "（正在跑）" : "")),
              h("div", { style: S.mono }, st.log)));
          }
          return h("div", { style: S.wrap }, kids);
        }

        function apply(ctx) {
          ctx.slots.inject("settings.plugins.tab", () =>
            ctx.slots.register({ name: "settings.plugins.tab", id: "radar-config", order: 45,
                                 label: () => "配置面板" }, RadarConfig));
        }

        exports.name = "dsh-radar-config";
        exports.inject = ["slots"];
        exports.apply = apply;
        return module.exports;
      }
    });
  } catch (err) {
    console.warn("[radar-config] client runtime error:", err);
  }
})();
