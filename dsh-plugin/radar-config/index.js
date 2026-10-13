/**
 * dsh-radar-config 宿主半
 *
 * ① 本地模式：直接读写 /sdcard/Download/DSH工作区/radar/config.yaml，本地跑一轮
 * ② 远程模式：通过 gh CLI 读写 GitHub 仓库里的 config.yaml、灌 Secrets、
 *             触发 Actions、看运行状态与日志 —— 容器死了也不影响云端 7x24 跑
 *
 * 不引任何 node 依赖，YAML 读写交给 config_io.py（python3 + PyYAML 已就绪）。
 */
import { spawn } from "node:child_process";
import { existsSync, readFileSync, writeFileSync, readdirSync, statSync, openSync, closeSync } from "node:fs";
import { join } from "node:path";

export const name = "dsh-radar-config";
export const inject = ["webServer"];

const WS = "/sdcard/Download/DSH工作区";
const RADAR = join(WS, "radar");
const CONFIG = join(RADAR, "config.yaml");
const PROXY_FILE = join(RADAR, "proxy.txt");
const OUTBOX = join(RADAR, "outbox");
const RUN_LOG = join(RADAR, "data", "last_run.log");
const IO = "/root/.dsh/plugin-src/dsh-radar-config/config_io.py";
const REMOTE_FILE = join(RADAR, "remote.json");
const API = "/radar-config/api";

let last = { at: 0, pid: null };

/** 远程仓库配置，落在 radar/remote.json，容器重建后仍在 /sdcard 上 */
let remote = { repo: "", workflow: "radar-daily.yml", branch: "main" };

function loadRemote() {
  try {
    if (existsSync(REMOTE_FILE)) {
      const j = JSON.parse(readFileSync(REMOTE_FILE, "utf8"));
      if (j && typeof j === "object") Object.assign(remote, j);
    }
  } catch {}
}

function persistRemote() {
  try {
    writeFileSync(REMOTE_FILE, JSON.stringify(remote, null, 2) + "\n", "utf8");
    return true;
  } catch { return false; }
}

function sendJson(res, status, payload) {
  res.writeHead(status, { "cache-control": "no-store", "content-type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(payload));
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on("data", (c) => {
      size += c.length;
      if (size > 2 * 1024 * 1024) { reject(new Error("body too large")); req.destroy(); return; }
      chunks.push(c);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

function py(args, input, timeoutMs) {
  return new Promise((resolve) => {
    const p = spawn("python3", args, { cwd: RADAR });
    let out = "", err = "";
    const t = setTimeout(() => { try { p.kill("SIGKILL"); } catch {} }, timeoutMs || 60000);
    p.stdout.on("data", (d) => (out += d));
    p.stderr.on("data", (d) => (err += d));
    p.on("close", (code) => { clearTimeout(t); resolve({ code, out, err }); });
    p.on("error", (e) => { clearTimeout(t); resolve({ code: -1, out, err: String(e) }); });
    if (input !== undefined && input !== null) p.stdin.write(input);
    try { p.stdin.end(); } catch {}
  });
}

/** 调 gh CLI：stdin 传值（Secret 不走 argv），stdout 收结果 */
function gh(args, input, timeoutMs) {
  return new Promise((resolve) => {
    const env = Object.assign({}, process.env, { GH_PAGER: "", NO_COLOR: "1", GH_PROMPT_DISABLED: "1" });
    const p = spawn("gh", args, { env });
    let out = "", err = "";
    const t = setTimeout(() => { try { p.kill("SIGKILL"); } catch {} }, timeoutMs || 90000);
    p.stdout.on("data", (d) => (out += d));
    p.stderr.on("data", (d) => (err += d));
    p.on("close", (code) => { clearTimeout(t); resolve({ code, out, err }); });
    p.on("error", (e) => { clearTimeout(t); resolve({ code: -1, out, err: String(e) }); });
    if (input !== undefined && input !== null) { try { p.stdin.write(input); } catch {} }
    try { p.stdin.end(); } catch {}
  });
}

function repoArg(r) { return String(r === undefined || r === null ? "" : r).trim() || remote.repo || ""; }

let _userCache = null, _userAt = 0;
/** 当前 gh 登录名。5 分钟缓存 —— gh 进程冷启动实测首次要 30 秒，
 *  而登录名不会变，每次 state 都去问一遍纯属浪费。 */
async function ghUser() {
  if (_userCache !== null && Date.now() - _userAt < 300000) return _userCache;
  const r = await gh(["api", "user", "--jq", ".login"], null, 20000);
  if (r.code === 0) { _userCache = r.out.trim(); _userAt = Date.now(); }
  return _userCache || "";
}

/** 从远端仓库读 config.yaml（raw 直出 YAML 文本） */
async function remoteRead(repo) {
  const r = await gh(["api", `repos/${repo}/contents/radar/config.yaml`,
                      "-H", "Accept: application/vnd.github.raw+json"], null, 45000);
  if (r.code !== 0) return { ok: false, error: (r.err || r.out || "").trim().slice(0, 400) || "读取失败" };
  const p = await py([IO, "parse"], JSON.stringify({ text: r.out }), 20000);
  let j = {};
  try { j = JSON.parse(p.out); } catch { return { ok: false, error: (p.err || p.out || "").slice(0, 300) }; }
  if (!j.ok) return { ok: false, error: j.error };
  return { ok: true, config: j.config, text: r.out };
}

function newestDigest() {
  try {
    if (!existsSync(OUTBOX)) return null;
    const files = readdirSync(OUTBOX).filter((f) => f.endsWith(".md")).sort();
    if (!files.length) return null;
    const p = join(OUTBOX, files[files.length - 1]);
    const st = statSync(p);
    const text = readFileSync(p, "utf8");
    return { path: p, name: files[files.length - 1], mtime: st.mtimeMs, size: st.size,
             lines: text.split("\n").length, head: text.slice(0, 2000) };
  } catch (e) {
    return { error: String(e) };
  }
}

function readTail(file, n) {
  try {
    if (!existsSync(file)) return "";
    const t = readFileSync(file, "utf8");
    return t.length > n ? t.slice(-n) : t;
  } catch { return ""; }
}

function proxyFile() {
  try { return existsSync(PROXY_FILE) ? readFileSync(PROXY_FILE, "utf8").trim() : ""; } catch { return ""; }
}

export function apply(ctx, config = {}) {
  const configPath = config.configPath || CONFIG;
  loadRemote();

  // ─────────────────────────── 本地后端 ───────────────────────────

  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: API + "/state",
    handler: async (req, res) => {
      if (req.method !== "GET") { res.writeHead(405, { allow: "GET" }); res.end(); return; }
      const r = await py([IO, "read", configPath], null, 20000);
      let cfg = null, err = null;
      try { const j = JSON.parse(r.out); cfg = j.config; if (!j.ok) err = j.error; }
      catch (e) { err = (r.err || r.out || "").slice(0, 300) || String(e); }
      sendJson(res, 200, { ok: !err, error: err, configPath, config: cfg,
                           proxyFile: proxyFile(), digest: newestDigest(),
                           running: Date.now() - (last.at || 0) < 15 * 60 * 1000,
                           lastRunAt: last.at || 0, lastRunPid: last.pid, log: readTail(RUN_LOG, 1200) });
    }
  }), "radar-config: state");

  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: API + "/save",
    handler: async (req, res) => {
      if (req.method !== "POST") { res.writeHead(405, { allow: "POST" }); res.end(); return; }
      try {
        const body = JSON.parse((await readBody(req)) || "{}");
        const r = await py([IO, "write", configPath], JSON.stringify({ config: body.config || {} }), 30000);
        let j = {};
        try { j = JSON.parse(r.out); } catch { j = { ok: false, error: (r.err || r.out || "").slice(0, 300) }; }
        sendJson(res, j.ok ? 200 : 500, j);
      } catch (e) { sendJson(res, 500, { ok: false, error: String(e) }); }
    }
  }), "radar-config: save");

  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: API + "/probe",
    handler: async (req, res) => {
      if (req.method !== "POST") { res.writeHead(405, { allow: "POST" }); res.end(); return; }
      try {
        const body = JSON.parse((await readBody(req)) || "{}");
        const r = await py([IO, "probe"], JSON.stringify({ url: body.url || "", proxy: body.proxy }), 60000);
        let j = {};
        try { j = JSON.parse(r.out); } catch { j = { ok: false, error: (r.err || r.out || "").slice(0, 300) }; }
        sendJson(res, 200, j);
      } catch (e) { sendJson(res, 500, { ok: false, error: String(e) }); }
    }
  }), "radar-config: probe");

  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: API + "/run",
    handler: async (req, res) => {
      if (req.method !== "POST") { res.writeHead(405, { allow: "POST" }); res.end(); return; }
      try {
        const body = JSON.parse((await readBody(req)) || "{}");
        const args = body.args || [];
        const fd = openSync(RUN_LOG, "a");
        const p = spawn("python3", ["-m", "radar", ...args], { cwd: WS, detached: true, stdio: ["ignore", fd, fd] });
        p.unref();
        closeSync(fd);
        last = { at: Date.now(), pid: p.pid };
        sendJson(res, 200, { ok: true, pid: p.pid, at: last.at, log: RUN_LOG });
      } catch (e) { sendJson(res, 500, { ok: false, error: String(e) }); }
    }
  }), "radar-config: run");

  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: API + "/digest",
    handler: (req, res) => {
      if (req.method !== "GET") { res.writeHead(405, { allow: "GET" }); res.end(); return; }
      sendJson(res, 200, { ok: true, digest: newestDigest() });
    }
  }), "radar-config: digest");

  // ─────────────────────────── 远程后端 ───────────────────────────

  /** 记仓库 + 读远端 config + 列最近运行 */
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: API + "/remote/state",
    handler: async (req, res) => {
      if (req.method !== "GET") { res.writeHead(405, { allow: "GET" }); res.end(); return; }
      const repo = repoArg();
      // 四个互不依赖的远程调用并行发，别再串行等 —— 串行时稳定 4.4 秒，
      // 手机端 UI 会明显卡在「读取中…」。并行后 ≈ 最慢那一个（~1.8 秒）。
      const [user, c, r1, r2] = await Promise.all([
        ghUser(),
        repo ? remoteRead(repo) : Promise.resolve(null),
        repo ? gh(["run", "list", "-R", repo, "-L", "8", "--json",
                   "databaseId,status,conclusion,createdAt,displayTitle,url"], null, 45000)
             : Promise.resolve({ code: 1, out: "" }),
        repo ? gh(["secret", "list", "-R", repo, "--json", "name,updatedAt"], null, 30000)
             : Promise.resolve({ code: 1, out: "" })
      ]);
      let cfg = null, err = null, runs = [], secrets = [];
      if (c) { if (c.ok) cfg = c.config; else err = c.error; }
      if (r1.code === 0) { try { runs = JSON.parse(r1.out); } catch {} }
      if (r2.code === 0) { try { secrets = JSON.parse(r2.out); } catch {} }
      sendJson(res, 200, { ok: !err, error: err, repo, workflow: remote.workflow,
                           branch: remote.branch, user, config: cfg, runs, secrets,
                           remoteFile: REMOTE_FILE, auth: !!user });
    }
  }), "radar-config: remote state");

  /** 记住仓库地址 */
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: API + "/remote/repo",
    handler: async (req, res) => {
      if (req.method !== "POST") { res.writeHead(405, { allow: "POST" }); res.end(); return; }
      try {
        const body = JSON.parse((await readBody(req)) || "{}");
        remote.repo = String(body.repo || "").trim();
        if (body.workflow) remote.workflow = String(body.workflow).trim();
        if (body.branch) remote.branch = String(body.branch).trim();
        persistRemote();
        sendJson(res, 200, { ok: true, repo: remote.repo, workflow: remote.workflow,
                             branch: remote.branch, file: REMOTE_FILE });
      } catch (e) { sendJson(res, 500, { ok: false, error: String(e) }); }
    }
  }), "radar-config: remote repo");

  /** 写远端 config.yaml —— 敏感字段先过 sanitize，绝不进公开仓库 */
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: API + "/remote/save",
    handler: async (req, res) => {
      if (req.method !== "POST") { res.writeHead(405, { allow: "POST" }); res.end(); return; }
      try {
        const body = JSON.parse((await readBody(req)) || "{}");
        const repo = repoArg(body.repo);
        if (!repo) { sendJson(res, 400, { ok: false, error: "没填仓库（owner/name）" }); return; }

        const san = await py([IO, "sanitize"], JSON.stringify({ config: body.config || {} }), 20000);
        let sj = {}; try { sj = JSON.parse(san.out); } catch {}
        if (!sj.ok) { sendJson(res, 500, { ok: false, error: sj.error || (san.err || "").slice(0, 300) }); return; }

        const dmp = await py([IO, "dump"], JSON.stringify({ config: sj.config }), 20000);
        let dj = {}; try { dj = JSON.parse(dmp.out); } catch {}
        if (!dj.ok) { sendJson(res, 500, { ok: false, error: dj.error || (dmp.err || "").slice(0, 300) }); return; }

        const text = dj.text;
        const sr = await gh(["api", `repos/${repo}/contents/radar/config.yaml`, "--jq", ".sha"], null, 30000);
        const sha = sr.code === 0 ? sr.out.trim() : "";

        const args = ["api", "-X", "PUT", `repos/${repo}/contents/radar/config.yaml`,
                      "-f", "message=chore(radar): 插件 UI 更新 config.yaml",
                      "-f", "content=" + Buffer.from(text, "utf8").toString("base64")];
        if (sha) args.push("-f", "sha=" + sha);

        const r = await gh(args, null, 60000);
        if (r.code !== 0) {
          sendJson(res, 500, { ok: false, error: (r.err || r.out || "").trim().slice(0, 500) });
          return;
        }
        let commit = "";
        try { commit = (JSON.parse(r.out).commit || {}).sha || ""; } catch {}
        sendJson(res, 200, { ok: true, commit, removed: sj.removed || [],
                             bytes: text.length, raw: text.slice(0, 1200) });
      } catch (e) { sendJson(res, 500, { ok: false, error: String(e) }); }
    }
  }), "radar-config: remote save");

  /** 在云端跑一轮 */
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: API + "/remote/run",
    handler: async (req, res) => {
      if (req.method !== "POST") { res.writeHead(405, { allow: "POST" }); res.end(); return; }
      try {
        const body = JSON.parse((await readBody(req)) || "{}");
        const repo = repoArg(body.repo);
        if (!repo) { sendJson(res, 400, { ok: false, error: "没填仓库（owner/name）" }); return; }
        const wf = String(body.workflow || remote.workflow).trim();
        const args = ["workflow", "run", wf, "-R", repo, "--ref", String(body.branch || remote.branch).trim()];
        const inputs = body.inputs || {};
        Object.keys(inputs).forEach((k) => args.push("-f", k + "=" + inputs[k]));
        const r = await gh(args, null, 60000);
        if (r.code !== 0) {
          sendJson(res, 500, { ok: false, error: (r.err || r.out || "").trim().slice(0, 400) });
          return;
        }
        sendJson(res, 200, { ok: true, repo, workflow: wf,
                             url: `https://github.com/${repo}/actions/workflows/${wf}` });
      } catch (e) { sendJson(res, 500, { ok: false, error: String(e) }); }
    }
  }), "radar-config: remote run");

  /** 拉某次运行的日志尾巴 */
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: API + "/remote/log",
    handler: async (req, res) => {
      if (req.method !== "GET") { res.writeHead(405, { allow: "GET" }); res.end(); return; }
      const u = new URL(req.url, "http://x");
      const repo = repoArg(u.searchParams.get("repo"));
      const id = u.searchParams.get("id");
      if (!repo || !id) { sendJson(res, 400, { ok: false, error: "缺 repo 或 id" }); return; }
      const r = await gh(["run", "view", id, "-R", repo, "--log"], null, 150000);
      const t = r.out || r.err || "";
      sendJson(res, 200, { ok: r.code === 0, id,
                           log: t.length > 9000 ? t.slice(-9000) : t,
                           error: r.code === 0 ? null : (r.err || "").slice(0, 400) });
    }
  }), "radar-config: remote log");

  /** 灌一个 Secret，值走 stdin（不上屏、不进 argv） */
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: API + "/remote/secret",
    handler: async (req, res) => {
      if (req.method !== "POST") { res.writeHead(405, { allow: "POST" }); res.end(); return; }
      try {
        const body = JSON.parse((await readBody(req)) || "{}");
        const repo = repoArg(body.repo);
        const nm = String(body.name || "").trim().toUpperCase().replace(/[^A-Z0-9_]/g, "_");
        if (!repo || !nm) { sendJson(res, 400, { ok: false, error: "缺 repo 或密钥名" }); return; }
        const val = body.value === undefined || body.value === null ? "" : String(body.value);
        if (!val) { sendJson(res, 400, { ok: false, error: "密钥值是空的" }); return; }
        const r = await gh(["secret", "set", nm, "-R", repo], val, 40000);
        sendJson(res, r.code === 0 ? 200 : 500,
                 r.code === 0 ? { ok: true, name: nm, bytes: val.length }
                              : { ok: false, error: (r.err || r.out || "").trim().slice(0, 400) });
      } catch (e) { sendJson(res, 500, { ok: false, error: String(e) }); }
    }
  }), "radar-config: remote secret");
}
