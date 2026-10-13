/**
 * 独立验证台：把 index.js 的原样逻辑挂到一个裸 http server 上。
 * 用途 —— DSH 宿主不重启时，也能实测「读/写远端仓库、灌密钥、触发运行、拉日志」这条路。
 * 用法：node standalone.mjs [port]   （默认 3097）
 */
import { createServer } from "node:http";
import { apply } from "./index.js";

const PORT = Number(process.argv[2] || 3097);
const routes = [];

const ctx = {
  effect(fn) { fn(); },
  webServer: {
    register({ kind, path, handler }) {
      if (kind !== "exact") throw new Error("only exact supported in standalone");
      routes.push({ path, handler });
      return () => {};
    }
  }
};

apply(ctx);

const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://127.0.0.1");
  const hit = routes.find((r) => r.path === url.pathname);
  if (!hit) {
    res.writeHead(404, { "content-type": "application/json" });
    res.end(JSON.stringify({ ok: false, error: "no route " + url.pathname,
                             routes: routes.map((r) => r.path) }));
    return;
  }
  try {
    await hit.handler(req, res);
  } catch (e) {
    if (!res.headersSent) res.writeHead(500, { "content-type": "application/json" });
    res.end(JSON.stringify({ ok: false, error: String(e) }));
  }
});

server.listen(PORT, "127.0.0.1", () => {
  console.log("[standalone] http://127.0.0.1:" + PORT);
  routes.forEach((r) => console.log("  " + r.path));
});
